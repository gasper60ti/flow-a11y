from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

from dynamic.analyzers.evidence_refs import resolve_evidence_ref
from dynamic.preprocessors.criterion_evidence_packet_builder import (
    has_explicit_visible_focus_signal,
    keyboard_recovery_observed,
    media_has_strong_relevance,
    observed_pointer_cancellation_failure,
)
from dynamic.schemas.wcag_dynamic import DynamicCriterion, DynamicCriterionResult, DynamicStatus, FailValidation


VALIDATOR_NAME = "fail_confidence_validator"

FOCUS_APPEARANCE_CRITERIA = {"2.4.7", "2.4.13"}
KEYBOARD_OPERATION_CRITERIA = {"2.1.1", "2.1.3"}
KEYBOARD_TRAP_CRITERIA = {"2.1.2"}
FOCUS_ORDER_CRITERIA = {"2.4.3"}
FORM_CRITERIA = {"3.3.1", "3.3.3", "3.3.4", "3.3.5", "3.3.6", "3.3.7", "3.3.8", "3.3.9"}

INTERACTIVE_TAGS = {
    "a",
    "audio",
    "button",
    "details",
    "input",
    "select",
    "summary",
    "textarea",
    "video",
}
INTERACTIVE_ROLES = {
    "button",
    "checkbox",
    "combobox",
    "link",
    "listbox",
    "menuitem",
    "option",
    "radio",
    "searchbox",
    "slider",
    "spinbutton",
    "switch",
    "tab",
    "textbox",
}
DIRECT_SOURCES = {
    "accessibility_tree",
    "computed_style",
    "media_api",
    "mutation_observer",
    "observed_pointer_event",
    "runtime_event_plus_mutation",
    "runtime_event",
    "runtime_form_snapshot",
}
DIRECT_QUALITIES = {"computed", "observed"}


@dataclass(frozen=True)
class FailConfidenceDecision:
    status: DynamicStatus
    reason: str
    evidence_strength: Literal["direct", "indirect", "insufficient"]
    rule: str


class FailConfidenceValidator:
    """Post-LLM fail validator that preserves only evidence-backed failures."""

    def validate(
        self,
        criterion: DynamicCriterion,
        result: DynamicCriterionResult,
        evidence: dict[str, Any],
    ) -> DynamicCriterionResult:
        if result.status != DynamicStatus.fail:
            return result

        decision = self._decision(criterion, result, evidence)
        validation = FailValidation(
            original_status=DynamicStatus.fail.value,
            validated_status=decision.status.value,
            reason=decision.reason,
            evidence_strength=decision.evidence_strength,
            rule=decision.rule,
            validator=VALIDATOR_NAME,
        )
        return result.model_copy(update={"status": decision.status, "fail_validation": validation})

    def _decision(
        self,
        criterion: DynamicCriterion,
        result: DynamicCriterionResult,
        evidence: dict[str, Any],
    ) -> FailConfidenceDecision:
        if criterion.id in FOCUS_APPEARANCE_CRITERIA:
            return self._validate_focus_appearance(evidence)
        if criterion.id in KEYBOARD_OPERATION_CRITERIA:
            return self._validate_keyboard_operation(evidence)
        if criterion.id in KEYBOARD_TRAP_CRITERIA:
            return self._validate_keyboard_trap(evidence)
        if criterion.id in FOCUS_ORDER_CRITERIA:
            return self._validate_focus_order(evidence)
        if criterion.probe == "media_probe":
            return self._validate_media(criterion, evidence)
        if criterion.probe == "pointer_gesture_probe":
            return self._validate_pointer(criterion, evidence)
        if criterion.id in FORM_CRITERIA and criterion.probe == "forms_errors_auth_probe":
            return self._validate_form_criterion(criterion, evidence)
        return self._validate_generic(result, evidence)

    def _validate_focus_appearance(self, evidence: dict[str, Any]) -> FailConfidenceDecision:
        if has_repeated_keyboard_blocking_pattern(evidence):
            return _preserve(
                "Repeated keyboard navigation is blocked or trapped on the same focus target.",
                "focus_keyboard_blocking_pattern",
            )
        if is_body_or_html_focus_only(evidence):
            return _downgrade(
                "Only body/html focus was observed; that is not enough for a focus appearance fail.",
                "indirect",
                "body_html_focus_guard",
            )
        missing_indicator_refs = missing_focus_indicator_refs(evidence)
        computed_missing_refs = missing_focus_indicator_refs(evidence, require_screenshot=False)
        computed_missing_steps = {
            _int_or_none(item.get("step"))
            for item in _list(evidence.get("focus_visible_trace"))
            if isinstance(item, dict) and has_missing_focus_indicator_evidence(item)
        }
        computed_missing_steps.discard(None)

        if not _list(evidence.get("screenshot_paths")) and not (
            computed_missing_refs
            and has_keyboard_caused_focus_event(evidence, target_steps=computed_missing_steps)
        ):
            return _downgrade(
                "Focus appearance fail lacks screenshot evidence or same-step keyboard-linked computed-style evidence.",
                "insufficient",
                "focus_appearance_missing_screenshot",
            )

        if not missing_indicator_refs and computed_missing_refs and not _list(evidence.get("screenshot_paths")):
            return _preserve(
                "Strong same-step computed-style evidence shows no meaningful indicator on the keyboard-focused element.",
                "focus_appearance_computed_style",
            )

        if not missing_indicator_refs:
            if any(
                isinstance(item, dict) and has_explicit_visible_focus_signal(item)
                for key in ("focus_visible_trace", "focus_sequence")
                for item in _list(evidence.get(key))
            ):
                return _downgrade(
                    "Explicit element or ancestor focus styling contradicts the missing-indicator fail.",
                    "indirect",
                    "focus_appearance_direct_indicator",
                )
            return _downgrade(
                "No visible, non-body interactive focused element has direct missing-indicator evidence.",
                "indirect",
                "focus_appearance_direct_indicator",
            )
        missing_steps = {
            _int_or_none(item.get("step"))
            for item in _list(evidence.get("focus_visible_trace"))
            if isinstance(item, dict) and has_missing_focus_indicator_evidence(item)
        }
        missing_steps.discard(None)
        if not has_keyboard_caused_focus_event(evidence, target_steps=missing_steps):
            return _downgrade(
                "Missing focus styling was observed, but keyboard-caused focus was not captured for the same state.",
                "indirect",
                "focus_appearance_keyboard_causality",
            )
        return _preserve(
            "Direct missing focus-indicator evidence is tied to a visible interactive element and captured state.",
            "focus_appearance_direct_indicator",
        )

    def _validate_keyboard_trap(self, evidence: dict[str, Any]) -> FailConfidenceDecision:
        if keyboard_recovery_observed(evidence):
            return _downgrade(
                "A temporary focus stall was followed by successful keyboard escape or focus recovery.",
                "indirect",
                "keyboard_trap_recovered",
            )
        if has_repeated_keyboard_blocking_pattern(evidence):
            return _preserve(
                "Repeated keyboard attempts remain trapped on the same small focus set with no later recovery.",
                "keyboard_trap_persistent",
            )
        if not has_keyboard_events(evidence):
            return _downgrade(
                "Keyboard-trap fail lacks observed keyboard navigation evidence.",
                "insufficient",
                "keyboard_trap_missing_events",
            )
        return _downgrade(
            "Keyboard interaction was observed, but no persistent trap after repeated attempts was captured.",
            "indirect",
            "keyboard_trap_persistent",
        )

    def _validate_keyboard_operation(self, evidence: dict[str, Any]) -> FailConfidenceDecision:
        if has_repeated_keyboard_blocking_pattern(evidence):
            return _preserve(
                "Repeated keyboard navigation remains blocked on body/html or one focus target.",
                "keyboard_blocking_pattern",
            )
        if is_body_or_html_focus_only(evidence):
            return _downgrade(
                "Body/html focus alone does not prove that keyboard operation is blocked.",
                "indirect",
                "body_html_focus_guard",
            )
        if not has_keyboard_events(evidence):
            return _downgrade(
                "Keyboard fail lacks observed keyboard event evidence.",
                "insufficient",
                "keyboard_missing_keyboard_event",
            )
        return _downgrade(
            "Keyboard events were observed, but no repeated trap, dead end, or unreachable-control pattern was captured.",
            "indirect",
            "keyboard_blocking_pattern",
        )

    def _validate_focus_order(self, evidence: dict[str, Any]) -> FailConfidenceDecision:
        focus_sequence = _list(evidence.get("focus_sequence"))
        if has_repeated_keyboard_blocking_pattern(evidence):
            return _preserve(
                "Repeated keyboard navigation remains blocked on body/html or one focus target.",
                "focus_order_keyboard_blocking_pattern",
            )
        if focus_escaped_active_dialog(focus_sequence):
            return _preserve(
                "Focus moved from an active dialog to background page content.",
                "focus_order_dialog_escape",
            )
        if looks_like_out_of_order_form_focus(focus_sequence):
            return _preserve(
                "Focus order visibly contradicts the form control sequence.",
                "focus_order_form_sequence",
            )
        if has_focus_order_interruption_sequence(evidence):
            return _preserve(
                "Observed focus traversal includes multiple interactive targets interrupted by repeated body/html focus.",
                "focus_order_interruption_sequence",
            )
        if is_body_or_html_focus_only(evidence):
            return _downgrade(
                "Body/html focus alone does not prove illogical focus order.",
                "indirect",
                "body_html_focus_guard",
            )
        return _downgrade(
            "Focus sequence does not show a direct trap, dialog escape, or order contradiction.",
            "indirect",
            "focus_order_direct_sequence",
        )

    def _validate_media(self, criterion: DynamicCriterion, evidence: dict[str, Any]) -> FailConfidenceDecision:
        media_elements = _list(evidence.get("media_elements"))
        if not media_elements:
            return _downgrade("No runtime media element is present.", "insufficient", "media_runtime_relevance")
        if not has_playing_relevant_media(evidence):
            return _downgrade(
                "Media exists, but playback, audibility, visibility, or time advancement was not observed.",
                "indirect",
                "media_runtime_relevance",
            )
        if not missing_required_media_support(criterion, evidence):
            return _downgrade(
                "Runtime media is relevant, but the required accessibility support is not directly shown as missing.",
                "indirect",
                "media_required_support_missing",
            )
        return _preserve(
            "Relevant runtime media is playing/audible/time-advancing and required support is missing.",
            "media_runtime_relevance",
        )

    def _validate_pointer(
        self,
        criterion: DynamicCriterion,
        evidence: dict[str, Any],
    ) -> FailConfidenceDecision:
        if criterion.id == "2.5.2" and observed_pointer_cancellation_failure(evidence):
            return _preserve(
                "A pointer down action was observed without a matching cancellation-safe completion event.",
                "pointer_cancellation_observed_failure",
            )
        if has_observed_pointer_gesture_requirement(evidence):
            return _preserve(
                "A real drag, path, or multipointer requirement was observed without an alternative.",
                "pointer_observed_gesture_requirement",
            )
        if has_only_pointer_listener_or_click_evidence(evidence):
            return _downgrade(
                "Only pointer/click listeners or ordinary clicks were observed.",
                "indirect",
                "pointer_listener_only",
            )
        return _downgrade(
            "No observed task-relevant pointer-only gesture requirement was captured.",
            "insufficient",
            "pointer_observed_gesture_requirement",
        )

    def _validate_form_criterion(
        self,
        criterion: DynamicCriterion,
        evidence: dict[str, Any],
    ) -> FailConfidenceDecision:
        if criterion.id in {"3.3.1", "3.3.3"} and has_form_error_after_real_user_action(evidence):
            return _preserve(
                "Observed form interaction produced error feedback tied to a real control.",
                "form_error_after_user_action",
            )
        if criterion.id == "3.3.5" and has_missing_contextual_help_after_real_action(evidence):
            return _preserve(
                "A real invalid form action was evaluated and the involved control had no associated contextual help.",
                "form_contextual_help_missing_after_action",
            )
        if criterion.id in {"3.3.4", "3.3.6"} and _has_direct_observed_records(
            evidence,
            "error_prevention_failures",
        ):
            return _preserve(
                "A consequential submission lacked an observed prevention, review, or reversal mechanism.",
                "form_error_prevention_observed_failure",
            )
        if criterion.id == "3.3.7" and _has_direct_observed_records(
            evidence,
            "observed_redundant_entry_failures",
        ):
            return _preserve(
                "Repeated entry of previously supplied information was observed without an allowed alternative.",
                "form_redundant_entry_observed_failure",
            )
        if criterion.id in {"3.3.8", "3.3.9"} and _has_direct_observed_records(
            evidence,
            "auth_barrier_failures",
        ):
            return _preserve(
                "An observed authentication barrier required a cognitive-function test without an alternative.",
                "form_authentication_observed_barrier",
            )
        if has_generic_form_error_text(evidence):
            return _downgrade(
                "Form-related evidence exists but does not directly prove this criterion-specific violation.",
                "indirect",
                "form_generic_error_text",
            )
        return _downgrade(
            "No criterion-specific observed form failure was captured.",
            "insufficient",
            "form_criterion_direct_evidence",
        )

    def _validate_generic(
        self,
        result: DynamicCriterionResult,
        evidence: dict[str, Any],
    ) -> FailConfidenceDecision:
        packet = evidence.get("criterion_evidence_packet")
        if (
            isinstance(packet, dict)
            and packet.get("criterion_family") == "other"
            and packet.get("evidence_strength") not in {"direct", "behavioral_direct"}
        ):
            return _downgrade(
                "Generic criterion evidence is indirect and lacks a criterion-specific runtime violation signal.",
                "insufficient",
                "generic_indirect_packet",
            )
        if has_direct_cited_evidence(result, evidence):
            return _preserve("The fail cites direct observed or computed evidence.", "generic_direct_cited_evidence")
        return _downgrade(
            "The fail cites evidence, but not direct observed or computed violation evidence.",
            "indirect",
            "generic_direct_cited_evidence",
        )


def has_keyboard_caused_focus_event(
    evidence: dict[str, Any],
    *,
    target_steps: set[int] | None = None,
) -> bool:
    def matches_step(item: dict[str, Any]) -> bool:
        if not target_steps:
            return True
        step = _int_or_none(item.get("step"))
        return step in target_steps

    if any(
        isinstance(event, dict) and _is_keyboard_navigation_event(event) and matches_step(event)
        for event in _list(evidence.get("keyboard_events"))
    ):
        return True
    if any(
        isinstance(item, dict) and _is_keyboard_navigation_event(item) and matches_step(item)
        for item in _list(evidence.get("keyboard_focus_transitions"))
    ):
        return True
    return any(
        isinstance(item, dict) and item.get("focus_visible_match") is True and matches_step(item)
        for item in _list(evidence.get("focus_visible_trace"))
    )


def has_keyboard_events(evidence: dict[str, Any]) -> bool:
    return any(_is_keyboard_navigation_event(event) for event in _list(evidence.get("keyboard_events")))


def has_valid_focused_interactive_element(entry: dict[str, Any]) -> bool:
    if is_body_or_html_focus(entry):
        return False
    tag = str(entry.get("tag") or "").strip().lower()
    role = str(entry.get("role") or "").strip().lower()
    attrs = entry.get("attributes") if isinstance(entry.get("attributes"), dict) else {}
    if tag in INTERACTIVE_TAGS:
        if tag == "a":
            return bool(attrs.get("href") or entry.get("aria_label") or entry.get("active_element_text"))
        return True
    if role in INTERACTIVE_ROLES:
        return True
    tabindex = str(attrs.get("tabindex") or "").strip()
    return bool(tabindex and tabindex != "-1" and (entry.get("aria_label") or entry.get("active_element_name")))


def has_same_step_screenshot(entry: dict[str, Any], evidence: dict[str, Any]) -> bool:
    paths = [str(path) for path in _list(evidence.get("screenshot_paths")) if str(path).strip()]
    if not paths:
        return False
    step = _int_or_none(entry.get("step"))
    if step is None:
        return True
    step_tokens = (f"step_{step}", f"step-{step}", f"step{step}")
    return any(any(token in path for token in step_tokens) for path in paths)


def has_visible_nonzero_rect(entry: dict[str, Any]) -> bool:
    rect = entry.get("bounding_client_rect") or entry.get("rect")
    if not isinstance(rect, dict):
        return False
    width = _float_or_zero(rect.get("width"))
    height = _float_or_zero(rect.get("height"))
    return width > 0 and height > 0


def has_missing_focus_indicator_evidence(entry: dict[str, Any]) -> bool:
    if entry.get("has_visible_indicator") is not False:
        return False
    if has_explicit_visible_focus_signal(entry):
        return False
    if entry.get("decisive") is False:
        return False
    source = str(entry.get("source") or "").strip()
    quality = str(entry.get("quality") or "").strip()
    return source == "computed_style" or quality == "computed"


def has_contradictory_visible_focus_signal(entry: dict[str, Any], evidence: dict[str, Any]) -> bool:
    if has_explicit_visible_focus_signal(entry):
        return True
    xpath = str(entry.get("xpath") or "")
    step = entry.get("step")
    for key in ("focus_visible_trace", "focus_sequence"):
        for candidate in _list(evidence.get(key)):
            if not isinstance(candidate, dict):
                continue
            same_xpath = xpath and str(candidate.get("xpath") or "") == xpath
            same_step = step is not None and candidate.get("step") == step
            if not (same_xpath or same_step):
                continue
            if has_explicit_visible_focus_signal(candidate):
                return True
    return False


def is_body_or_html_focus_only(evidence: dict[str, Any]) -> bool:
    focus_sequence = [entry for entry in _list(evidence.get("focus_sequence")) if isinstance(entry, dict)]
    if not focus_sequence:
        return False
    return all(is_body_or_html_focus(entry) for entry in focus_sequence)


def is_body_or_html_focus(entry: dict[str, Any]) -> bool:
    tag = str(entry.get("tag") or "").strip().lower()
    xpath = str(entry.get("xpath") or "").strip().lower()
    role = str(entry.get("role") or "").strip().lower()
    if tag in {"body", "html"} or role in {"document"}:
        return True
    return xpath in {"/html", "/html[1]", "/html/body", "/html[1]/body[1]"}


def has_repeated_keyboard_blocking_pattern(evidence: dict[str, Any]) -> bool:
    focus_sequence = [entry for entry in _list(evidence.get("focus_sequence")) if isinstance(entry, dict)]
    if len(focus_sequence) < 2 or not has_keyboard_events(evidence):
        return False
    if keyboard_recovery_observed(evidence):
        return False

    body_count = sum(1 for entry in focus_sequence if is_body_or_html_focus(entry))
    interactive_count = sum(1 for entry in focus_sequence if has_valid_focused_interactive_element(entry))
    if body_count >= 2 and interactive_count == 0:
        return True

    targets = [
        str(entry.get("xpath") or entry.get("active_element_name") or entry.get("active_element_text") or "")
        for entry in focus_sequence
    ]
    targets = [target for target in targets if target]
    repeated = Counter(targets)
    return bool(targets and max(repeated.values()) >= 3 and len(set(targets)) <= 2)


def has_focus_order_interruption_sequence(evidence: dict[str, Any]) -> bool:
    focus_sequence = [entry for entry in _list(evidence.get("focus_sequence")) if isinstance(entry, dict)]
    if len(focus_sequence) < 5:
        return False
    if not (_list(evidence.get("keyboard_events")) or _list(evidence.get("observed_focus_events"))):
        return False
    body_count = sum(1 for entry in focus_sequence if is_body_or_html_focus(entry))
    interactive_targets = {
        str(entry.get("xpath") or "")
        for entry in focus_sequence
        if has_valid_focused_interactive_element(entry) and str(entry.get("xpath") or "")
    }
    return body_count >= 2 and len(interactive_targets) >= 2


def missing_focus_indicator_refs(
    evidence: dict[str, Any],
    *,
    require_screenshot: bool = True,
) -> list[str]:
    refs: list[str] = []
    for index, item in enumerate(_list(evidence.get("focus_visible_trace"))):
        if not isinstance(item, dict):
            continue
        focus_entry = _matching_focus_entry(item, evidence) or item
        if not has_missing_focus_indicator_evidence(item):
            continue
        if not has_valid_focused_interactive_element(focus_entry):
            continue
        if not has_visible_nonzero_rect(item):
            continue
        if require_screenshot and not has_same_step_screenshot(item, evidence):
            continue
        if has_contradictory_visible_focus_signal(item, evidence):
            continue
        refs.append(f"focus_visible_trace[{index}]")
    return refs


def focus_escaped_active_dialog(focus_sequence: list[dict[str, Any]]) -> bool:
    saw_dialog_focus = False
    for item in focus_sequence:
        if not isinstance(item, dict):
            continue
        xpath = str(item.get("xpath") or "").lower()
        role = str(item.get("role") or "").lower()
        in_dialog = "dialog" in xpath or role == "dialog"
        if in_dialog:
            saw_dialog_focus = True
            continue
        if saw_dialog_focus and xpath and "dialog" not in xpath:
            return True
    return False


def looks_like_out_of_order_form_focus(focus_sequence: list[dict[str, Any]]) -> bool:
    form_controls: list[tuple[str, int]] = []
    for item in focus_sequence:
        if not isinstance(item, dict):
            continue
        xpath = str(item.get("xpath") or "").lower()
        if "/form" not in xpath:
            continue
        if "/button" in xpath:
            form_controls.append(("button", 0))
            continue
        input_index = _xpath_index(xpath, "input")
        if input_index is not None:
            form_controls.append(("input", input_index))

    if len(form_controls) < 3:
        return False

    seen_button = False
    highest_input_before_button = 0
    for kind, index in form_controls:
        if kind == "button":
            seen_button = True
            continue
        if not seen_button:
            highest_input_before_button = max(highest_input_before_button, index)
            continue
        if index > highest_input_before_button:
            return True
    return False


def has_playing_relevant_media(evidence: dict[str, Any]) -> bool:
    events = _list(evidence.get("media_events"))
    event_types = {str(event.get("type") or "").lower() for event in events if isinstance(event, dict)}
    has_play_event = bool(event_types & {"play", "playing", "timeupdate"})
    for media in _list(evidence.get("media_elements")):
        if not isinstance(media, dict):
            continue
        time_advancing = _float_or_zero(media.get("current_time_delta")) > 0
        audible = bool(media.get("audible"))
        playing = media.get("paused") is False and (audible or time_advancing or has_play_event)
        if (playing or audible or time_advancing) and media_has_strong_relevance(media, events):
            return True
    return False


def missing_required_media_support(criterion: DynamicCriterion, evidence: dict[str, Any]) -> bool:
    if criterion.id in {"1.2.2", "1.2.4"}:
        return not _list(evidence.get("caption_tracks"))
    if criterion.id in {"1.2.3", "1.2.5", "1.2.6", "1.2.7", "1.2.8"}:
        return not _list(evidence.get("transcript_indicators"))
    return True


def has_observed_pointer_gesture_requirement(evidence: dict[str, Any]) -> bool:
    dragging = [item for item in _list(evidence.get("dragging_indicators")) if isinstance(item, dict)]
    for item in dragging:
        if item.get("has_keyboard_alternative") is True:
            continue
        if _is_observed_source(item) and str(item.get("event_type") or "").lower() in {"dragstart", "dragend", "drop"}:
            return True

    for event in _list(evidence.get("observed_pointer_events")):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or event.get("event_type") or "").lower()
        if event_type in {"dragstart", "dragend", "drop"}:
            return True
        if event_type in {"pointermove", "touchmove", "mousemove"} and _float_or_zero(event.get("buttons")) > 0:
            return True
        if _int_or_none(event.get("pointer_count")) and int(event.get("pointer_count")) > 1:
            return True
        if event.get("is_multipointer") is True:
            return True
    return False


def has_only_pointer_listener_or_click_evidence(evidence: dict[str, Any]) -> bool:
    pointer_items = [
        item
        for key in ("pointer_handlers", "observed_pointer_events", "event_listener_registrations")
        for item in _list(evidence.get(key))
        if isinstance(item, dict)
    ]
    if not pointer_items:
        return False
    weak_types = {"click", "mousedown", "mouseup", "pointerdown", "pointerup", "touchstart", "touchend"}
    for item in pointer_items:
        event_type = str(item.get("event_type") or item.get("type") or "").lower()
        source = str(item.get("source") or "").lower()
        quality = str(item.get("quality") or "").lower()
        listener = source in {"dom_token", "event_listener_registration", "event_listener"} or quality in {
            "event_listener",
            "static_candidate",
        }
        ordinary_click = not event_type or event_type in weak_types
        if not (listener or ordinary_click):
            return False
    return True


def has_form_error_after_real_user_action(evidence: dict[str, Any]) -> bool:
    if _has_direct_observed_records(evidence, "form_action_error_feedback"):
        return True
    interactions = [
        event
        for key in ("submit_events", "input_events", "change_events", "form_interaction_events")
        for event in _list(evidence.get(key))
        if isinstance(event, dict)
    ]
    if not any(_is_form_control_event(event) for event in interactions):
        return False
    observed_errors = [
        item
        for item in _list(evidence.get("observed_error_mutations"))
        if isinstance(item, dict) and _is_observed_source(item)
    ]
    if observed_errors:
        return True
    error_messages = [
        item
        for item in _list(evidence.get("error_messages"))
        if isinstance(item, dict) and _is_observed_source(item)
    ]
    return bool(error_messages and evidence.get("has_error_semantics") is True)


def has_missing_contextual_help_after_real_action(evidence: dict[str, Any]) -> bool:
    return _has_direct_observed_records(evidence, "contextual_help_failures")


def has_generic_form_error_text(evidence: dict[str, Any]) -> bool:
    if _list(evidence.get("error_messages")):
        return True
    for mutation in _list(evidence.get("observed_error_mutations")):
        text = str(mutation).lower()
        if any(token in text for token in ("error", "required", "invalid")):
            return True
    return False


def _has_direct_observed_records(evidence: dict[str, Any], key: str) -> bool:
    return any(
        isinstance(item, dict) and _is_observed_source(item)
        for item in _list(evidence.get(key))
    )


def has_direct_cited_evidence(result: DynamicCriterionResult, evidence: dict[str, Any]) -> bool:
    for ref in _cited_evidence_refs(result):
        for candidate_ref in _ref_and_parent(ref):
            resolution = resolve_evidence_ref(evidence, candidate_ref)
            if resolution.resolved and _is_direct_value(resolution.value):
                return True
    return False


def _preserve(reason: str, rule: str) -> FailConfidenceDecision:
    return FailConfidenceDecision(
        status=DynamicStatus.fail,
        reason=reason,
        evidence_strength="direct",
        rule=rule,
    )


def _downgrade(reason: str, evidence_strength: Literal["indirect", "insufficient"], rule: str) -> FailConfidenceDecision:
    return FailConfidenceDecision(
        status=DynamicStatus.warning,
        reason=reason,
        evidence_strength=evidence_strength,
        rule=rule,
    )


def _matching_focus_entry(item: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any] | None:
    item_step = item.get("step")
    item_xpath = str(item.get("xpath") or "")
    for candidate in _list(evidence.get("focus_sequence")):
        if not isinstance(candidate, dict):
            continue
        same_step = item_step is not None and candidate.get("step") == item_step
        same_xpath = item_xpath and str(candidate.get("xpath") or "") == item_xpath
        if same_step or same_xpath:
            return candidate
    return None


def _is_keyboard_navigation_event(event: Any) -> bool:
    if not isinstance(event, dict):
        return False
    key = str(event.get("key") or event.get("code") or "").strip().lower()
    event_type = str(event.get("type") or "").strip().lower()
    if event_type and event_type not in {"keydown", "keyup", "keypress"}:
        return False
    return key in {"tab", "arrowdown", "arrowleft", "arrowright", "arrowup", "enter", "space", " "}


def _is_form_control_event(event: dict[str, Any]) -> bool:
    target = event.get("target") if isinstance(event.get("target"), dict) else {}
    tag = str(target.get("tag") or event.get("tag") or "").lower()
    role = str(target.get("role") or event.get("role") or "").lower()
    xpath = str(target.get("xpath") or event.get("xpath") or "").lower()
    if tag in {"input", "select", "textarea", "button", "form"} or role in INTERACTIVE_ROLES:
        return True
    return any(token in xpath for token in ("/input", "/select", "/textarea", "/button", "/form"))


def _is_direct_value(value: Any) -> bool:
    if isinstance(value, list):
        return any(_is_direct_value(item) for item in value)
    if not isinstance(value, dict):
        return False
    if _is_observed_source(value):
        return True
    return any(_is_direct_value(item) for item in value.values() if isinstance(item, (dict, list)))


def _is_observed_source(item: dict[str, Any]) -> bool:
    source = str(item.get("source") or "").strip()
    quality = str(item.get("quality") or "").strip()
    decisive = bool(item.get("decisive"))
    if source in {"event_listener", "event_listener_registration", "dom_token", "text_keyword"}:
        return False
    if quality in {"event_listener", "static_candidate", "weak_keyword"}:
        return False
    return decisive and (source in DIRECT_SOURCES or quality in DIRECT_QUALITIES)


def _cited_evidence_refs(result: DynamicCriterionResult) -> list[str]:
    refs: list[str] = []
    for violation in result.violations:
        refs.extend(str(ref) for ref in violation.evidence_refs if str(ref).strip())
    return refs


def _ref_and_parent(ref: str) -> list[str]:
    refs = [ref]
    if "." in ref:
        refs.append(ref.rsplit(".", 1)[0])
    return refs


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _xpath_index(xpath: str, element_name: str) -> int | None:
    token = f"/{element_name}["
    if token not in xpath:
        return None
    try:
        return int(xpath.rsplit(token, 1)[1].split("]", 1)[0])
    except (IndexError, ValueError):
        return None


__all__ = [
    "FailConfidenceValidator",
    "has_form_error_after_real_user_action",
    "has_missing_contextual_help_after_real_action",
    "has_keyboard_caused_focus_event",
    "has_focus_order_interruption_sequence",
    "has_observed_pointer_gesture_requirement",
    "has_playing_relevant_media",
    "has_repeated_keyboard_blocking_pattern",
    "has_same_step_screenshot",
    "has_valid_focused_interactive_element",
    "has_visible_nonzero_rect",
    "is_body_or_html_focus_only",
    "missing_focus_indicator_refs",
]
