from __future__ import annotations

from typing import Any

from dynamic.probes.common import collect_runtime_instrumentation
from dynamic.schemas.runtime_state import RuntimeState


class FormsErrorsAuthProbe:
    name = "forms_errors_auth_probe"

    def collect(self, states: list[RuntimeState]) -> dict[str, Any]:
        form_snapshots: list[dict[str, Any]] = []
        error_messages: list[dict[str, Any]] = []
        observed_error_mutations: list[dict[str, Any]] = []
        auth_snapshots: list[dict[str, Any]] = []
        has_error_semantics = False
        input_events = [
            {**event, "quality": "observed", "source": "runtime_event", "decisive": True}
            for event in collect_runtime_instrumentation(states, "input_events")
        ]
        change_events = [
            {**event, "quality": "observed", "source": "runtime_event", "decisive": True}
            for event in collect_runtime_instrumentation(states, "change_events")
        ]
        submit_events = [
            {**event, "quality": "observed", "source": "runtime_event", "decisive": True}
            for event in collect_runtime_instrumentation(states, "submit_events")
        ]
        form_interaction_events = input_events + change_events + submit_events
        form_control_states = [
            {**item, "quality": "computed", "source": "runtime_form_snapshot", "decisive": True}
            for item in collect_runtime_instrumentation(states, "form_control_states")
        ]

        for step, state in enumerate(states, start=1):
            dom = (state.dom_html or "").lower()
            for mutation in state.dom_mutations or []:
                if not isinstance(mutation, dict):
                    continue
                serialized = str(mutation).lower()
                mutation_type = str(mutation.get("mutation_type") or mutation.get("type") or "").lower()
                if mutation_type.startswith("observed") and any(
                    token in serialized for token in ["error", "invalid", "required", "incorrect", "try again"]
                ):
                    tied_to_control = isinstance(mutation.get("related_control"), dict) and bool(
                        mutation.get("related_control")
                    )
                    tied_to_live_region = isinstance(mutation.get("live_region"), dict) and bool(
                        mutation.get("live_region")
                    )
                    observed_error_mutations.append(
                        {
                            "step": step,
                            "url": state.url,
                            "mutation": mutation,
                            "quality": "observed" if tied_to_control or tied_to_live_region else "weak_keyword",
                            "source": "mutation_observer",
                            "decisive": bool(tied_to_control or tied_to_live_region),
                        }
                    )

            if not dom:
                continue

            has_form = "<form" in dom or "input" in dom
            if has_form:
                form_snapshots.append(
                    {
                        "step": step,
                        "url": state.url,
                        "quality": "static_candidate",
                        "source": "dom_token",
                        "decisive": False,
                    }
                )

            if any(token in dom for token in ["error", "invalid", "required", "try again", "incorrect"]):
                error_messages.append(
                    {
                        "step": step,
                        "url": state.url,
                        "quality": "weak_keyword",
                        "source": "text_keyword",
                        "decisive": False,
                    }
                )
                if any(token in dom for token in ["aria-invalid", "aria-describedby", 'role="alert"', 'role="status"', "aria-live"]):
                    has_error_semantics = True

            if any(token in dom for token in ["login", "sign in", "authenticate", "otp", "captcha", "password"]):
                auth_snapshots.append(
                    {
                        "step": step,
                        "url": state.url,
                        "quality": "static_candidate",
                        "source": "text_keyword",
                        "decisive": False,
                    }
                )

        observed_form_snapshots = [
            {
                "step": item.get("step"),
                "url": item.get("url"),
                "target": item.get("target") or {},
                "form": item.get("form") or {},
                "quality": "computed",
                "source": "runtime_form_snapshot",
                "decisive": True,
            }
            for item in form_control_states
            if isinstance(item.get("target"), dict) and item.get("target")
        ]
        form_snapshots.extend(observed_form_snapshots)

        help_for_forms = [
            {
                "step": item.get("step"),
                "target": item.get("target") or {},
                "form": item.get("form") or {},
                "help_messages": item.get("help_messages") or [],
                "quality": "computed",
                "source": "runtime_form_snapshot",
                "decisive": True,
            }
            for item in form_control_states
            if item.get("help_present") is True and item.get("help_messages")
        ]
        form_action_error_feedback = _tied_form_error_feedback(
            form_interaction_events,
            observed_error_mutations,
        )
        contextual_help_checks = _contextual_help_checks(form_interaction_events, form_control_states)
        redundant_entry_indicators = _redundant_entry_candidates(form_interaction_events)
        observed_auth_events = [
            event
            for event in form_interaction_events
            if _looks_like_auth_target(event.get("target") if isinstance(event.get("target"), dict) else {})
        ]
        has_error_semantics = has_error_semantics or any(
            item.get("error_feedback_present") is True
            or str((item.get("target") or {}).get("aria_invalid") or "").lower() == "true"
            for item in form_control_states
        )

        return {
            "form_snapshots": form_snapshots,
            "form_control_states": form_control_states,
            "error_messages": error_messages,
            "observed_error_mutations": observed_error_mutations,
            "input_events": input_events,
            "change_events": change_events,
            "submit_events": submit_events,
            "form_interaction_events": form_interaction_events,
            "form_action_error_feedback": form_action_error_feedback,
            "contextual_help_checks": contextual_help_checks,
            "contextual_help_failures": [],
            "auth_snapshots": auth_snapshots,
            "observed_auth_events": observed_auth_events,
            "auth_barrier_failures": [],
            "help_for_forms": help_for_forms,
            "redundant_entry_indicators": redundant_entry_indicators,
            "observed_redundant_entry_failures": [],
            "error_prevention_failures": [],
            "has_error_semantics": has_error_semantics,
        }


def _tied_form_error_feedback(
    interactions: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tied: list[dict[str, Any]] = []
    for error in errors:
        if error.get("decisive") is not True:
            continue
        error_step = _step(error)
        mutation = error.get("mutation") if isinstance(error.get("mutation"), dict) else {}
        related_control = mutation.get("related_control") if isinstance(mutation.get("related_control"), dict) else {}
        related_form = mutation.get("related_form") if isinstance(mutation.get("related_form"), dict) else {}
        for event in interactions:
            event_step = _step(event)
            if error_step is None or event_step is None or error_step not in {event_step, event_step + 1}:
                continue
            event_target = event.get("target") if isinstance(event.get("target"), dict) else {}
            event_form = event.get("form") if isinstance(event.get("form"), dict) else {}
            same_control = _same_xpath(event_target, related_control.get("target") if related_control else {})
            same_form = _same_xpath(event_form or event_target, related_form)
            submit_same_step = str(event.get("type") or "").lower() == "submit" and error_step == event_step
            if not (same_control or same_form or submit_same_step):
                continue
            tied.append(
                {
                    "step": error_step,
                    "event": event,
                    "error": error,
                    "related_control": related_control,
                    "related_form": related_form,
                    "quality": "observed",
                    "source": "runtime_event_plus_mutation",
                    "decisive": True,
                }
            )
            break
    return tied


def _contextual_help_checks(
    interactions: list[dict[str, Any]],
    control_states: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    interaction_steps = {_step(event) for event in interactions if _step(event) is not None}
    submit_steps = {
        _step(event)
        for event in interactions
        if str(event.get("type") or "").lower() == "submit" and _step(event) is not None
    }
    for state in control_states:
        step = _step(state)
        if step is None or step not in interaction_steps | submit_steps:
            continue
        if state.get("invalid") is not True and state.get("error_feedback_present") is not True:
            continue
        checks.append(
            {
                "step": step,
                "target": state.get("target") or {},
                "form": state.get("form") or {},
                "invalid_after_action": bool(state.get("invalid")),
                "error_feedback_present": bool(state.get("error_feedback_present")),
                "help_present": bool(state.get("help_present")),
                "suggestion_present": bool(state.get("suggestion_present")),
                "evaluation_complete": True,
                "quality": "computed",
                "source": "runtime_form_snapshot",
                "decisive": True,
            }
        )
    return checks


def _redundant_entry_candidates(interactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for event in interactions:
        target = event.get("target") if isinstance(event.get("target"), dict) else {}
        name = str(target.get("name") or target.get("id") or "").strip().lower()
        if not name:
            continue
        by_name.setdefault(name, []).append(event)
    return [
        {
            "field_name": name,
            "events": events[:10],
            "quality": "static_candidate",
            "source": "runtime_event_pattern",
            "decisive": False,
        }
        for name, events in by_name.items()
        if len({_step(event) for event in events}) >= 2
    ]


def _looks_like_auth_target(target: dict[str, Any]) -> bool:
    text = " ".join(
        str(target.get(key) or "").lower()
        for key in ("type", "autocomplete", "name", "id", "role")
    )
    return any(token in text for token in ("password", "otp", "one-time-code", "captcha", "verification"))


def _same_xpath(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_xpath = str(left.get("xpath") or "")
    right_xpath = str(right.get("xpath") or "")
    return bool(left_xpath and right_xpath and left_xpath == right_xpath)


def _step(item: dict[str, Any]) -> int | None:
    try:
        return int(item.get("step"))
    except (TypeError, ValueError):
        return None
