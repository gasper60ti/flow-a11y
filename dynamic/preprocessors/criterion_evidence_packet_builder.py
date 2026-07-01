from __future__ import annotations

import re
from collections import Counter
from typing import Any

from dynamic.schemas.wcag_dynamic import CriterionEvidencePacket, DynamicCriterion


FOCUS_KEYBOARD_CRITERIA = {"2.1.1", "2.1.2", "2.1.3", "2.4.3", "2.4.7", "2.4.13"}
FOCUS_APPEARANCE_CRITERIA = {"2.4.7", "2.4.13"}
KEYBOARD_OPERATION_CRITERIA = {"2.1.1", "2.1.2", "2.1.3"}
FORM_CRITERIA = {"3.3.1", "3.3.3", "3.3.4", "3.3.5", "3.3.6", "3.3.7", "3.3.8", "3.3.9"}
FORM_ERROR_CRITERIA = {"3.3.1", "3.3.3"}
FORM_PREVENTION_CRITERIA = {"3.3.4", "3.3.6"}
FORM_AUTH_CRITERIA = {"3.3.8", "3.3.9"}

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


class CriterionEvidencePacketBuilder:
    """Build compact, criterion-specific evidence packets for LLM and routing."""

    def build(self, criterion: DynamicCriterion, evidence: dict[str, Any]) -> CriterionEvidencePacket:
        family = criterion_family(criterion)
        if family == "focus_keyboard":
            return self._focus_keyboard_packet(criterion, evidence)
        if family == "media":
            return self._media_packet(criterion, evidence)
        if family == "pointer":
            return self._pointer_packet(criterion, evidence)
        if family == "forms":
            return self._forms_packet(criterion, evidence)
        return self._other_packet(criterion, evidence)

    def _focus_keyboard_packet(self, criterion: DynamicCriterion, evidence: dict[str, Any]) -> CriterionEvidencePacket:
        focus_sequence = [item for item in _list(evidence.get("focus_sequence")) if isinstance(item, dict)]
        focus_visible_trace = [item for item in _list(evidence.get("focus_visible_trace")) if isinstance(item, dict)]
        keyboard_events = [item for item in _list(evidence.get("keyboard_events")) if isinstance(item, dict)]
        focus_events = [item for item in _list(evidence.get("observed_focus_events")) if isinstance(item, dict)]
        transitions = [item for item in _list(evidence.get("keyboard_focus_transitions")) if isinstance(item, dict)]

        keyboard_navigation_events = [event for event in keyboard_events if _is_keyboard_navigation_event(event)]
        tab_events = [event for event in keyboard_events if str(event.get("key") or "").strip().lower() == "tab"]
        body_html_count = sum(1 for item in focus_sequence if is_body_or_html_focus(item))
        interactive_entries = [item for item in focus_sequence if is_interactive_focus(item)]
        visible_entries = [item for item in focus_sequence if has_visible_nonzero_rect(item)]
        visible_indicator_refs = [
            f"focus_visible_trace[{index}]"
            for index, item in enumerate(focus_visible_trace)
            if has_explicit_visible_focus_signal(item)
        ]
        visible_indicator_count = len(visible_indicator_refs)
        false_indicator_count = sum(
            1
            for item in focus_visible_trace
            if item.get("has_visible_indicator") is False and not has_explicit_visible_focus_signal(item)
        )
        screenshot_steps = screenshot_step_numbers(evidence)
        exact_missing_refs = direct_missing_focus_indicator_refs(evidence, require_same_step_screenshot=True)
        missing_without_screenshot_refs = direct_missing_focus_indicator_refs(
            evidence,
            require_same_step_screenshot=False,
        )
        recovery_observed = keyboard_recovery_observed(evidence)
        repeated_blocking = repeated_keyboard_blocking_pattern(evidence)
        terminal_repeated = terminal_repeated_focus_pattern(evidence)
        focus_changed = len(unique_focus_targets(focus_sequence)) >= 2
        only_body_html_once = len(focus_sequence) == 1 and body_html_count == 1
        only_body_html = bool(focus_sequence) and body_html_count == len(focus_sequence)
        transition_changed_count = sum(1 for item in transitions if item.get("focus_changed") is True)

        positive: list[str] = []
        missing: list[str] = []
        contradictory: list[str] = []
        if keyboard_events:
            positive.append("keyboard_events")
        if keyboard_navigation_events:
            positive.append("keyboard_navigation_events")
        if focus_sequence:
            positive.append("focus_sequence")
        if focus_events:
            positive.append("observed_focus_events")
        if interactive_entries:
            positive.append("focused_interactive_elements")
        if exact_missing_refs:
            positive.extend(exact_missing_refs)
        if missing_without_screenshot_refs and not exact_missing_refs:
            positive.extend(missing_without_screenshot_refs)
        if visible_indicator_count:
            contradictory.append("visible_focus_indicator_observed")
            contradictory.extend(visible_indicator_refs)
        if only_body_html_once:
            missing.append("focused_interactive_element")
        if criterion.id in FOCUS_APPEARANCE_CRITERIA and not screenshot_steps:
            missing.append("same_step_screenshot")
        if not keyboard_events and criterion.id in KEYBOARD_OPERATION_CRITERIA:
            missing.append("keyboard_events")

        applicability = "applicable"
        reason = "Focused interactive runtime evidence is available."
        strength = "direct"

        if not focus_sequence and not keyboard_events and not focus_events:
            applicability = "not_applicable"
            strength = "insufficient"
            reason = "No keyboard or focus interaction was observed."
        elif only_body_html_once:
            applicability = "insufficient_evidence"
            strength = "indirect"
            reason = "Only one body/html focus state was observed."
        elif only_body_html and not repeated_blocking:
            applicability = "insufficient_evidence"
            strength = "indirect"
            reason = "Only body/html focus states were observed without a repeated keyboard-blocking pattern."
        elif repeated_blocking or terminal_repeated:
            applicability = "applicable"
            strength = "behavioral_direct"
            reason = "Repeated keyboard actions show focus remaining blocked or cycling on too few targets."
        elif criterion.id in FOCUS_APPEARANCE_CRITERIA:
            if exact_missing_refs:
                applicability = "applicable"
                strength = "direct"
                reason = "Missing focus-indicator evidence is tied to a focused element and same-step screenshot."
            elif visible_indicator_refs:
                applicability = "applicable"
                strength = "contradicted"
                reason = (
                    "Interactive focus was observed, but explicit element or ancestor focus styling "
                    "contradicts a missing-indicator claim."
                )
            elif missing_without_screenshot_refs:
                applicability = "applicable"
                strength = "direct"
                reason = (
                    "Strong computed-style evidence shows no meaningful focus indicator; "
                    "same-step screenshot support is absent."
                )
            elif interactive_entries:
                applicability = "applicable"
                strength = "direct" if visible_indicator_count else "indirect"
                reason = "Interactive focus was observed; focus appearance can be judged from computed style evidence."
            else:
                applicability = "insufficient_evidence"
                strength = "indirect"
                reason = "Focus evidence lacks a visible interactive focused element."
        elif criterion.id in KEYBOARD_OPERATION_CRITERIA:
            if focus_changed and (tab_events or transition_changed_count):
                applicability = "applicable"
                strength = "direct"
                reason = "Keyboard interaction and focus movement were observed."
            elif missing_without_screenshot_refs and keyboard_events:
                applicability = "applicable"
                strength = "direct"
                reason = "Keyboard input and direct missing focus affordance evidence were observed."
            elif keyboard_events and focus_sequence:
                applicability = "applicable"
                strength = "indirect"
                reason = "Keyboard and focus evidence are present but do not show a repeated blocking pattern."
            else:
                applicability = "insufficient_evidence"
                strength = "insufficient"
                reason = "Keyboard operation evidence is incomplete."
        elif criterion.id == "2.4.3":
            if focus_changed and keyboard_events:
                applicability = "applicable"
                strength = "direct"
                reason = "Keyboard-driven focus traversal across multiple targets was observed."
            elif focus_sequence:
                applicability = "applicable"
                strength = "indirect"
                reason = "Focus sequence exists, but keyboard causality is weak."
            else:
                applicability = "insufficient_evidence"
                strength = "insufficient"
                reason = "No focus sequence is available for order analysis."

        if visible_indicator_count >= 3 and len(exact_missing_refs) <= 1:
            contradictory.append("mostly_visible_focus_indicators")
            if criterion.id in FOCUS_APPEARANCE_CRITERIA and strength == "direct":
                strength = "contradicted"
                reason = "A small missing-indicator signal is contradicted by multiple visible focus indicators."

        facts = {
            "keyboard_event_count": len(keyboard_events),
            "keyboard_navigation_event_count": len(keyboard_navigation_events),
            "tab_event_count": len(tab_events),
            "focus_event_count": len(focus_events),
            "focus_sequence_count": len(focus_sequence),
            "focused_interactive_count": len(interactive_entries),
            "visible_nonzero_focus_count": len(visible_entries),
            "body_html_focus_count": body_html_count,
            "focus_changed": focus_changed,
            "transition_focus_changed_count": transition_changed_count,
            "direct_missing_focus_indicator_count": len(exact_missing_refs),
            "missing_focus_indicator_without_screenshot_count": len(missing_without_screenshot_refs),
            "visible_focus_indicator_count": visible_indicator_count,
            "focus_indicator_contradiction_count": len(visible_indicator_refs),
            "false_focus_indicator_count": false_indicator_count,
            "repeated_keyboard_blocking": repeated_blocking,
            "terminal_repeated_focus": terminal_repeated,
            "keyboard_recovery_observed": recovery_observed,
            "strong_computed_missing_focus_indicator_count": len(missing_without_screenshot_refs),
            "same_step_screenshot_steps": sorted(screenshot_steps),
            "focus_entries": compact_focus_entries(focus_sequence, focus_visible_trace, screenshot_steps),
            "keyboard_focus_transitions": compact_keyboard_transitions(transitions),
        }
        return CriterionEvidencePacket(
            criterion_id=criterion.id,
            criterion_family="focus_keyboard",
            applicability=applicability,
            applicability_reason=reason,
            evidence_strength=strength,
            positive_evidence=_dedupe(positive),
            missing_evidence=_dedupe(missing),
            contradictory_evidence=_dedupe(contradictory),
            step_consistency=step_consistency_for_refs(focus_sequence, screenshot_steps),
            compact_runtime_facts=facts,
        )

    def _media_packet(self, criterion: DynamicCriterion, evidence: dict[str, Any]) -> CriterionEvidencePacket:
        media_elements = [item for item in _list(evidence.get("media_elements")) if isinstance(item, dict)]
        media_events = [item for item in _list(evidence.get("media_events")) if isinstance(item, dict)]
        caption_tracks = _list(evidence.get("caption_tracks"))
        transcript_indicators = _list(evidence.get("transcript_indicators"))
        playing = [item for item in media_elements if media_is_playing_or_advancing(item, media_events)]
        strongly_relevant = [item for item in playing if media_has_strong_relevance(item, media_events)]
        support_key = "caption_tracks" if criterion.id in {"1.2.2", "1.2.4"} else "transcript_indicators"
        support_items = caption_tracks if support_key == "caption_tracks" else transcript_indicators

        if not media_elements:
            applicability = "not_applicable"
            strength = "insufficient"
            reason = "No media elements were observed in the runtime flow."
        elif not strongly_relevant:
            applicability = "not_applicable"
            strength = "indirect"
            reason = (
                "Media exists, but no audible, user-activated, controlled, meaningful-audio, "
                "or visible unmuted advancing media was observed."
            )
        else:
            applicability = "applicable"
            strength = "behavioral_direct"
            reason = "Runtime media has a direct task-relevance signal and is playing/time-advancing."

        positive = ["media_elements"] if media_elements else []
        if media_events:
            positive.append("media_events")
        if strongly_relevant:
            positive.append("playing_relevant_media")
        missing = [] if support_items else [support_key]
        facts = {
            "media_element_count": len(media_elements),
            "media_event_count": len(media_events),
            "playing_relevant_media_count": len(strongly_relevant),
            "muted_autoplay_only_count": sum(
                1
                for item in playing
                if bool(item.get("muted"))
                and not bool(item.get("controls"))
                and not bool(item.get("user_activated"))
                and not bool(item.get("meaningful_audio"))
            ),
            "caption_track_count": len(caption_tracks),
            "transcript_indicator_count": len(transcript_indicators),
            "media": compact_media_entries(media_elements),
        }
        return CriterionEvidencePacket(
            criterion_id=criterion.id,
            criterion_family="media",
            applicability=applicability,
            applicability_reason=reason,
            evidence_strength=strength,
            positive_evidence=positive,
            missing_evidence=missing,
            contradictory_evidence=[],
            step_consistency=step_consistency_for_refs(media_elements, set()),
            compact_runtime_facts=facts,
        )

    def _pointer_packet(self, criterion: DynamicCriterion, evidence: dict[str, Any]) -> CriterionEvidencePacket:
        pointer_handlers = [item for item in _list(evidence.get("pointer_handlers")) if isinstance(item, dict)]
        pointer_events = [item for item in _list(evidence.get("observed_pointer_events")) if isinstance(item, dict)]
        dragging = [item for item in _list(evidence.get("dragging_indicators")) if isinstance(item, dict)]
        gesture_requirements = observed_pointer_gesture_requirements(evidence)
        cancellation_failure = observed_pointer_cancellation_failure(evidence)
        listener_only = bool(pointer_handlers) and not pointer_events and not gesture_requirements

        if criterion.id == "2.5.2" and cancellation_failure:
            applicability = "applicable"
            strength = "behavioral_direct"
            reason = "A pointer down action was observed without a matching cancellation-safe completion event."
        elif gesture_requirements:
            applicability = "applicable"
            strength = "behavioral_direct"
            reason = "A task-relevant drag/path/multipointer gesture requirement was observed."
        elif listener_only:
            applicability = "not_applicable"
            strength = "indirect"
            reason = "Only pointer/click listener registrations or static pointer hints were observed."
        elif pointer_events:
            applicability = "not_applicable"
            strength = "indirect"
            reason = "Ordinary pointer events were observed, but no task-relevant gesture requirement was captured."
        else:
            applicability = "not_applicable"
            strength = "insufficient"
            reason = "No pointer interaction evidence was observed."

        facts = {
            "pointer_handler_count": len(pointer_handlers),
            "observed_pointer_event_count": len(pointer_events),
            "dragging_indicator_count": len(dragging),
            "observed_gesture_requirement_count": len(gesture_requirements),
            "observed_pointer_cancellation_failure": cancellation_failure,
            "listener_only": listener_only,
            "pointer_events": compact_event_entries(pointer_handlers + pointer_events),
        }
        return CriterionEvidencePacket(
            criterion_id=criterion.id,
            criterion_family="pointer",
            applicability=applicability,
            applicability_reason=reason,
            evidence_strength=strength,
            positive_evidence=["observed_pointer_cancellation_failure"]
            if cancellation_failure
            else ["observed_pointer_gesture_requirement"]
            if gesture_requirements
            else ["pointer_handlers"]
            if pointer_handlers
            else [],
            missing_evidence=[]
            if gesture_requirements or cancellation_failure
            else ["task_relevant_pointer_gesture"],
            contradictory_evidence=[],
            step_consistency=step_consistency_for_refs(pointer_handlers + pointer_events + dragging, set()),
            compact_runtime_facts=facts,
        )

    def _forms_packet(self, criterion: DynamicCriterion, evidence: dict[str, Any]) -> CriterionEvidencePacket:
        form_snapshots = [item for item in _list(evidence.get("form_snapshots")) if isinstance(item, dict)]
        control_states = [item for item in _list(evidence.get("form_control_states")) if isinstance(item, dict)]
        interactions = [
            item
            for key in ("submit_events", "input_events", "change_events", "form_interaction_events")
            for item in _list(evidence.get(key))
            if isinstance(item, dict)
        ]
        observed_errors = [
            item for item in _list(evidence.get("observed_error_mutations")) if isinstance(item, dict)
        ]
        error_messages = [item for item in _list(evidence.get("error_messages")) if isinstance(item, dict)]
        action_error_feedback = [
            item for item in _list(evidence.get("form_action_error_feedback")) if isinstance(item, dict)
        ]
        help_checks = [item for item in _list(evidence.get("contextual_help_checks")) if isinstance(item, dict)]
        help_failures = [
            item for item in _list(evidence.get("contextual_help_failures")) if isinstance(item, dict)
        ]
        prevention_failures = [
            item for item in _list(evidence.get("error_prevention_failures")) if isinstance(item, dict)
        ]
        redundant_candidates = [
            item for item in _list(evidence.get("redundant_entry_indicators")) if isinstance(item, dict)
        ]
        redundant_failures = [
            item for item in _list(evidence.get("observed_redundant_entry_failures")) if isinstance(item, dict)
        ]
        observed_auth_events = [
            item for item in _list(evidence.get("observed_auth_events")) if isinstance(item, dict)
        ]
        auth_barrier_failures = [
            item for item in _list(evidence.get("auth_barrier_failures")) if isinstance(item, dict)
        ]
        real_action = any(is_form_control_event(item) for item in interactions)
        tied_error = bool(action_error_feedback) or (
            real_action
            and any(item.get("decisive") is True for item in observed_errors)
            and evidence.get("has_error_semantics") is True
        )
        generic_error = bool(error_messages or observed_errors)
        missing_error_identification = [
            item
            for item in help_checks
            if item.get("evaluation_complete") is True
            and item.get("invalid_after_action") is True
            and item.get("error_feedback_present") is False
        ]
        missing_error_suggestion = [
            item
            for item in help_checks
            if item.get("evaluation_complete") is True
            and item.get("error_feedback_present") is True
            and item.get("suggestion_present") is False
        ]
        missing_contextual_help_candidates = [
            item
            for item in help_checks
            if item.get("evaluation_complete") is True
            and item.get("invalid_after_action") is True
            and item.get("help_present") is False
        ]

        applicability = "insufficient_evidence"
        strength = "insufficient"
        reason = "A form is present, but criterion-specific runtime behavior was not observed."
        positive: list[str] = []
        missing: list[str] = []

        if real_action:
            positive.append("form_interaction_events")
        if control_states:
            positive.append("form_control_states")
        if action_error_feedback:
            positive.append("form_action_error_feedback")
        if help_checks:
            positive.append("contextual_help_checks")
        if help_failures:
            positive.append("contextual_help_failures")

        if criterion.id == "3.3.1":
            if help_checks or tied_error:
                applicability = "applicable"
                strength = "behavioral_direct"
                reason = "A real form action produced a complete invalid/error-feedback check."
            elif generic_error:
                strength = "indirect"
                reason = "Error-like evidence exists but is not tied to a complete form-action check."
            elif not form_snapshots:
                applicability = "not_applicable"
                reason = "No form interaction or error-identification context was observed."
            missing = [] if help_checks or tied_error else ["form_action_error_feedback"]
        elif criterion.id == "3.3.3":
            if tied_error and help_checks:
                applicability = "applicable"
                strength = "behavioral_direct"
                reason = "A real form error was observed with criterion-specific suggestion evidence."
            elif tied_error or generic_error:
                strength = "indirect"
                reason = "An error signal exists, but suggestion completeness is not directly established."
            elif not form_snapshots:
                applicability = "not_applicable"
                reason = "No form error requiring a suggestion was observed."
            missing = [] if help_checks else ["contextual_help_checks"]
        elif criterion.id == "3.3.5":
            if help_failures:
                applicability = "applicable"
                strength = "behavioral_direct"
                reason = "A requested contextual-help interaction failed to provide task-relevant help."
            elif help_checks:
                applicability = "applicable"
                strength = "indirect"
                reason = (
                    "Associated help was checked for controls involved in a real form action, "
                    "but whole-interaction contextual-help absence was not established."
                )
            elif real_action or form_snapshots:
                strength = "indirect"
                reason = "A form interaction exists, but contextual-help absence was not directly evaluated."
            else:
                applicability = "not_applicable"
                reason = "No form context requiring contextual help was observed."
            missing = [] if help_failures else ["contextual_help_failures"]
        elif criterion.id in FORM_PREVENTION_CRITERIA:
            if prevention_failures:
                applicability = "applicable"
                strength = "behavioral_direct"
                reason = "A consequential submission lacked an observed prevention, review, or reversal mechanism."
                positive.append("error_prevention_failures")
            elif submit_events := [item for item in interactions if str(item.get("type") or "").lower() == "submit"]:
                applicability = "applicable"
                strength = "indirect"
                reason = "Submission was observed, but legal/financial/data consequences and prevention controls are incomplete."
                positive.append("submit_events")
            elif form_snapshots:
                reason = "A form exists, but no consequential submission was observed."
            else:
                applicability = "not_applicable"
                reason = "No applicable consequential form submission was observed."
            missing = [] if prevention_failures else ["error_prevention_failures"]
        elif criterion.id == "3.3.7":
            if redundant_failures:
                applicability = "applicable"
                strength = "behavioral_direct"
                reason = "Repeated entry of previously supplied information was directly observed without an alternative."
                positive.append("observed_redundant_entry_failures")
            elif redundant_candidates:
                strength = "indirect"
                reason = "Repeated field interaction is a candidate signal, but repeated information was not directly established."
                positive.append("redundant_entry_indicators")
            elif real_action or form_snapshots:
                reason = "Form interaction was observed without direct redundant-entry evidence."
            else:
                applicability = "not_applicable"
                reason = "No multi-step form entry process was observed."
            missing = [] if redundant_failures else ["observed_redundant_entry_failures"]
        elif criterion.id in FORM_AUTH_CRITERIA:
            if auth_barrier_failures:
                applicability = "applicable"
                strength = "behavioral_direct"
                reason = "An authentication barrier requiring a cognitive-function test was observed without an alternative."
                positive.append("auth_barrier_failures")
            elif observed_auth_events:
                applicability = "applicable"
                strength = "indirect"
                reason = "Authentication interaction was observed, but barrier and alternative evidence is incomplete."
                positive.append("observed_auth_events")
            elif _list(evidence.get("auth_snapshots")):
                strength = "indirect"
                reason = "Authentication-related DOM content exists without an observed authentication action."
                positive.append("auth_snapshots")
            else:
                applicability = "not_applicable"
                reason = "No authentication interaction was observed."
            missing = [] if auth_barrier_failures else ["auth_barrier_failures"]

        if (
            applicability == "insufficient_evidence"
            and not real_action
            and not generic_error
            and not help_checks
            and not help_failures
            and not prevention_failures
            and not redundant_failures
            and not observed_auth_events
            and not auth_barrier_failures
        ):
            applicability = "not_applicable"
            reason = "The observed flow did not trigger criterion-specific form behavior."

        facts = {
            "form_snapshot_count": len(form_snapshots),
            "form_control_state_count": len(control_states),
            "form_interaction_event_count": len(interactions),
            "real_form_action": real_action,
            "observed_error_mutation_count": len(observed_errors),
            "error_message_count": len(error_messages),
            "has_error_semantics": bool(evidence.get("has_error_semantics")),
            "tied_error_feedback": tied_error,
            "form_action_error_feedback_count": len(action_error_feedback),
            "contextual_help_check_count": len(help_checks),
            "missing_error_identification_count": len(missing_error_identification),
            "missing_error_suggestion_count": len(missing_error_suggestion),
            "missing_contextual_help_count": len(help_failures),
            "missing_contextual_help_candidate_count": len(missing_contextual_help_candidates),
            "error_prevention_failure_count": len(prevention_failures),
            "redundant_entry_candidate_count": len(redundant_candidates),
            "observed_redundant_entry_failure_count": len(redundant_failures),
            "observed_auth_event_count": len(observed_auth_events),
            "auth_barrier_failure_count": len(auth_barrier_failures),
            "form_events": compact_event_entries(interactions),
        }
        return CriterionEvidencePacket(
            criterion_id=criterion.id,
            criterion_family="forms",
            applicability=applicability,
            applicability_reason=reason,
            evidence_strength=strength,
            positive_evidence=_dedupe(positive),
            missing_evidence=_dedupe(missing),
            contradictory_evidence=[],
            step_consistency=step_consistency_for_refs(
                form_snapshots + control_states + interactions + observed_errors + error_messages,
                set(),
            ),
            compact_runtime_facts=facts,
        )

    def _other_packet(self, criterion: DynamicCriterion, evidence: dict[str, Any]) -> CriterionEvidencePacket:
        positive = [key for key, value in evidence.items() if key != "evidence_quality_summary" and _has_value(value)]
        quality = evidence.get("evidence_quality_summary") if isinstance(evidence.get("evidence_quality_summary"), dict) else {}
        strength_map = {
            "strong": "direct",
            "moderate": "direct",
            "weak": "indirect",
            "absent": "insufficient",
        }
        strength = strength_map.get(str(quality.get("evidence_strength") or ""), "indirect" if positive else "insufficient")
        direct_signal = criterion_specific_direct_signal(criterion, evidence)
        if direct_signal:
            strength = "direct"
            positive = _dedupe([direct_signal, *positive])
        if not positive:
            applicability = "not_applicable"
        elif strength in {"direct", "behavioral_direct"}:
            applicability = "applicable"
        else:
            applicability = "insufficient_evidence"
        return CriterionEvidencePacket(
            criterion_id=criterion.id,
            criterion_family="other",
            applicability=applicability,
            applicability_reason=(
                "Criterion-specific direct runtime evidence is available."
                if applicability == "applicable"
                else "Only indirect fallback evidence is available; criterion-specific applicability is not established."
                if positive
                else "No non-empty runtime evidence was available."
            ),
            evidence_strength=strength,
            positive_evidence=positive[:20],
            missing_evidence=[] if positive else list(criterion.required_evidence),
            contradictory_evidence=[],
            step_consistency="unknown",
            compact_runtime_facts={
                "criterion_specific_direct_signal": direct_signal or None,
                "non_empty_evidence_keys": positive[:40],
            },
        )


def criterion_family(criterion: DynamicCriterion) -> str:
    if criterion.id in FOCUS_KEYBOARD_CRITERIA or criterion.probe == "keyboard_focus_probe":
        return "focus_keyboard"
    if criterion.probe == "media_probe":
        return "media"
    if criterion.probe == "pointer_gesture_probe":
        return "pointer"
    if criterion.probe == "forms_errors_auth_probe" and criterion.id in FORM_CRITERIA:
        return "forms"
    return "other"


def compact_focus_entries(
    focus_sequence: list[dict[str, Any]],
    focus_visible_trace: list[dict[str, Any]],
    screenshot_steps: set[int],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    visible_by_step = {item.get("step"): item for item in focus_visible_trace if isinstance(item, dict)}
    for item in focus_sequence[:16]:
        step = _int_or_none(item.get("step"))
        visible = visible_by_step.get(item.get("step"), {})
        entries.append(
            {
                "step": step,
                "tag": item.get("tag"),
                "role": item.get("role"),
                "name": _short(item.get("active_element_name") or item.get("aria_label") or item.get("active_element_text")),
                "xpath": item.get("xpath"),
                "is_body_html": is_body_or_html_focus(item),
                "is_interactive": is_interactive_focus(item),
                "visible_nonzero_rect": has_visible_nonzero_rect(item),
                "has_visible_indicator": visible.get("has_visible_indicator"),
                "visible_focus_candidate": item.get("visible_focus_candidate"),
                "focus_visible_match": visible.get("focus_visible_match", item.get("focus_visible_match")),
                "visible_focus_signals": visible.get("visible_focus_signals")
                or item.get("visible_focus_signals")
                or {},
                "focus_css_rules": visible.get("focus_css_rules") or item.get("focus_css_rules") or [],
                "ancestor_focus_within": visible.get("ancestor_focus_within")
                or item.get("ancestor_focus_within")
                or [],
                "same_step_screenshot": step in screenshot_steps if step is not None else False,
                "computed_style": _compact_focus_style(item.get("computed_style") if isinstance(item.get("computed_style"), dict) else {}),
            }
        )
    return entries


def compact_keyboard_transitions(transitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in transitions[:20]:
        before = item.get("active_element_before") if isinstance(item.get("active_element_before"), dict) else {}
        after = item.get("active_element_after") if isinstance(item.get("active_element_after"), dict) else {}
        output.append(
            {
                "step": item.get("step"),
                "key": item.get("key"),
                "focus_changed": item.get("focus_changed"),
                "before": _target_summary(before),
                "after": _target_summary(after),
            }
        )
    return output


def compact_media_entries(media_elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "step": item.get("step"),
            "tag": item.get("tag"),
            "visible": item.get("visible"),
            "paused": item.get("paused"),
            "muted": item.get("muted"),
            "audible": item.get("audible"),
            "current_time": item.get("current_time"),
            "current_time_delta": item.get("current_time_delta"),
            "controls": item.get("controls"),
            "autoplay": item.get("autoplay"),
            "loop": item.get("loop"),
            "user_activated": item.get("user_activated"),
            "meaningful_audio": item.get("meaningful_audio"),
        }
        for item in media_elements[:12]
    ]


def compact_event_entries(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in events[:20]:
        target = item.get("target") if isinstance(item.get("target"), dict) else {}
        output.append(
            {
                "step": item.get("step"),
                "type": item.get("type") or item.get("event_type"),
                "source": item.get("source"),
                "quality": item.get("quality"),
                "decisive": item.get("decisive"),
                "target": _target_summary(target),
            }
        )
    return output


def direct_missing_focus_indicator_refs(evidence: dict[str, Any], *, require_same_step_screenshot: bool) -> list[str]:
    refs: list[str] = []
    screenshot_steps = screenshot_step_numbers(evidence)
    focus_sequence = [item for item in _list(evidence.get("focus_sequence")) if isinstance(item, dict)]
    for index, item in enumerate(_list(evidence.get("focus_visible_trace"))):
        if not isinstance(item, dict):
            continue
        focus_entry = matching_focus_entry(item, focus_sequence) or item
        step = _int_or_none(item.get("step"))
        if item.get("has_visible_indicator") is not False:
            continue
        if has_explicit_visible_focus_signal(item) or has_explicit_visible_focus_signal(focus_entry):
            continue
        if not is_interactive_focus(focus_entry):
            continue
        if not has_visible_nonzero_rect(item):
            continue
        if require_same_step_screenshot and (step is None or step not in screenshot_steps):
            continue
        refs.append(f"focus_visible_trace[{index}]")
    return refs


def repeated_keyboard_blocking_pattern(evidence: dict[str, Any]) -> bool:
    focus_sequence = [item for item in _list(evidence.get("focus_sequence")) if isinstance(item, dict)]
    keyboard_events = [item for item in _list(evidence.get("keyboard_events")) if isinstance(item, dict)]
    nav_events = [event for event in keyboard_events if _is_keyboard_navigation_event(event)]
    if len(focus_sequence) < 2 or not nav_events:
        return False
    if keyboard_recovery_observed(evidence):
        return False
    body_count = sum(1 for item in focus_sequence if is_body_or_html_focus(item))
    interactive_count = sum(1 for item in focus_sequence if is_interactive_focus(item))
    if body_count >= 3 and interactive_count <= 1:
        return True
    targets = [focus_target_key(item) for item in focus_sequence if focus_target_key(item)]
    counts = Counter(targets)
    if len(nav_events) >= 2 and len(targets) >= 2 and len(set(targets)) == 1:
        return True
    return bool(targets and max(counts.values()) >= 3 and len(set(targets)) <= 2)


def terminal_repeated_focus_pattern(evidence: dict[str, Any]) -> bool:
    focus_sequence = [item for item in _list(evidence.get("focus_sequence")) if isinstance(item, dict)]
    nav_events = [item for item in _list(evidence.get("keyboard_events")) if isinstance(item, dict) and _is_keyboard_navigation_event(item)]
    if len(focus_sequence) < 2 or len(nav_events) < 2:
        return False
    if keyboard_recovery_observed(evidence):
        return False
    last_targets = [focus_target_key(item) for item in focus_sequence[-3 if len(focus_sequence) >= 3 else -2:]]
    return bool(last_targets[0] and len(set(last_targets)) == 1)


def observed_pointer_gesture_requirements(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    for item in _list(evidence.get("dragging_indicators")):
        if not isinstance(item, dict):
            continue
        if item.get("has_keyboard_alternative") is True:
            continue
        event_type = str(item.get("event_type") or item.get("type") or "").lower()
        if _is_observed(item) and event_type in {"dragstart", "dragend", "drop"}:
            requirements.append(item)
    for item in _list(evidence.get("observed_pointer_events")):
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("event_type") or item.get("type") or "").lower()
        if event_type in {"dragstart", "dragend", "drop"}:
            requirements.append(item)
        elif event_type in {"pointermove", "mousemove", "touchmove"} and _float_or_zero(item.get("buttons")) > 0:
            requirements.append(item)
        elif item.get("is_multipointer") is True or _int_or_none(item.get("pointer_count")) and int(item.get("pointer_count")) > 1:
            requirements.append(item)
    return requirements


def observed_pointer_cancellation_failure(evidence: dict[str, Any]) -> bool:
    event_types = [
        str(item.get("event_type") or item.get("type") or "").lower()
        for item in _list(evidence.get("observed_pointer_events"))
        if isinstance(item, dict)
    ]
    down_count = sum(event_type in {"pointerdown", "mousedown", "touchstart"} for event_type in event_types)
    completion_count = sum(
        event_type in {"pointerup", "mouseup", "touchend", "pointercancel"}
        for event_type in event_types
    )
    return down_count > 0 and completion_count == 0


def criterion_specific_direct_signal(
    criterion: DynamicCriterion,
    evidence: dict[str, Any],
) -> str:
    if criterion.id == "2.3.1" and any(
        isinstance(item, dict) and isinstance(item.get("below_threshold"), bool)
        for item in _list(evidence.get("flash_events"))
    ):
        return "flash_events"
    if criterion.id == "2.2.6" and any(
        isinstance(item, dict)
        and isinstance(item.get("timing_info"), dict)
        and _float_or_zero(item["timing_info"].get("deadline_seconds")) > 0
        and isinstance(item["timing_info"].get("user_extend_control"), bool)
        for item in _list(evidence.get("timing_snapshots"))
    ):
        return "timing_snapshots"
    if (
        criterion.id == "4.1.3"
        and _list(evidence.get("status_message_mutations"))
        and isinstance(evidence.get("live_region_presence"), bool)
    ):
        return "status_message_mutations"
    return ""


def media_is_playing_or_advancing(item: dict[str, Any], media_events: list[dict[str, Any]]) -> bool:
    event_types = {str(event.get("type") or "").lower() for event in media_events}
    has_play_event = bool(event_types & {"play", "playing", "timeupdate"})
    time_advancing = _float_or_zero(item.get("current_time_delta")) > 0
    has_media_api_play_state = item.get("paused") is False and _float_or_zero(item.get("duration")) > 0
    return item.get("paused") is False and (
        time_advancing or has_play_event or bool(item.get("audible")) or has_media_api_play_state
    )


def media_is_visible_or_audible(item: dict[str, Any]) -> bool:
    tag = str(item.get("tag") or "").lower()
    return item.get("visible") is not False or tag == "audio" or bool(item.get("audible"))


def media_has_strong_relevance(item: dict[str, Any], media_events: list[dict[str, Any]]) -> bool:
    if not media_is_visible_or_audible(item):
        return False
    if bool(item.get("audible")) or bool(item.get("user_activated")) or bool(item.get("meaningful_audio")):
        return True
    if bool(item.get("controls")):
        return True
    event_user_activated = any(
        isinstance(event, dict)
        and event.get("user_activated") is True
        and (
            not item.get("media_key")
            or not event.get("media_key")
            or event.get("media_key") == item.get("media_key")
        )
        for event in media_events
    )
    if event_user_activated:
        return True
    return (
        item.get("visible") is not False
        and not bool(item.get("muted"))
        and _float_or_zero(item.get("volume")) > 0
        and (
            _float_or_zero(item.get("current_time_delta")) > 0
            or item.get("paused") is False
        )
    )


def keyboard_recovery_observed(evidence: dict[str, Any]) -> bool:
    transitions = [
        item for item in _list(evidence.get("keyboard_focus_transitions")) if isinstance(item, dict)
    ]
    stalled_steps = [
        _int_or_none(item.get("step"))
        for item in transitions
        if item.get("focus_changed") is False and _is_keyboard_navigation_event(item)
    ]
    stalled_steps = [step for step in stalled_steps if step is not None]
    if any(
        item.get("focus_changed") is True
        and _is_keyboard_navigation_event(item)
        and _int_or_none(item.get("step")) is not None
        and any(_int_or_none(item.get("step")) > step for step in stalled_steps)
        for item in transitions
    ):
        return True

    escape_steps = {
        _int_or_none(item.get("step"))
        for item in _list(evidence.get("keyboard_events"))
        if isinstance(item, dict) and str(item.get("key") or "").strip().lower() == "escape"
    }
    escape_steps.discard(None)
    if not escape_steps:
        return False

    focus_sequence = [
        item for item in _list(evidence.get("focus_sequence")) if isinstance(item, dict)
    ]
    for escape_step in sorted(escape_steps):
        before = [
            item
            for item in focus_sequence
            if _int_or_none(item.get("step")) is not None
            and _int_or_none(item.get("step")) <= escape_step
            and focus_target_key(item)
        ]
        after = [
            item
            for item in focus_sequence
            if _int_or_none(item.get("step")) is not None
            and _int_or_none(item.get("step")) > escape_step
            and is_interactive_focus(item)
            and focus_target_key(item)
        ]
        if before and any(focus_target_key(item) != focus_target_key(before[-1]) for item in after):
            return True
    return False


def screenshot_step_numbers(evidence: dict[str, Any]) -> set[int]:
    steps: set[int] = set()
    for path in _list(evidence.get("screenshot_paths")):
        match = re.search(r"(?:^|[/_-])(?:screenshot[_-]step|step)[_-](\d+)\.png$", str(path))
        if match:
            steps.add(int(match.group(1)))
    return steps


def step_consistency_for_refs(items: list[dict[str, Any]], screenshot_steps: set[int]) -> str:
    steps = {_int_or_none(item.get("step")) for item in items if _int_or_none(item.get("step")) is not None}
    if not steps:
        return "unknown"
    if screenshot_steps and steps <= screenshot_steps:
        return "same_step"
    if screenshot_steps and steps & screenshot_steps:
        return "mixed_steps"
    if screenshot_steps:
        return "mixed_steps"
    return "not_applicable"


def is_body_or_html_focus(item: dict[str, Any]) -> bool:
    tag = str(item.get("tag") or "").strip().lower()
    role = str(item.get("role") or "").strip().lower()
    xpath = str(item.get("xpath") or "").strip().lower()
    return tag in {"body", "html"} or role == "document" or xpath in {"/html", "/html[1]", "/html/body", "/html[1]/body[1]"}


def is_interactive_focus(item: dict[str, Any]) -> bool:
    if is_body_or_html_focus(item):
        return False
    tag = str(item.get("tag") or "").strip().lower()
    role = str(item.get("role") or "").strip().lower()
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    if tag in INTERACTIVE_TAGS:
        if tag == "a":
            return bool(attrs.get("href") or item.get("aria_label") or item.get("active_element_name") or item.get("active_element_text"))
        return True
    if role in INTERACTIVE_ROLES:
        return True
    tabindex = str(attrs.get("tabindex") or "").strip()
    if tabindex and tabindex != "-1":
        return True
    xpath = str(item.get("xpath") or "").lower()
    return any(token in xpath for token in ("/button", "/input", "/select", "/textarea", "/a[", "/summary"))


def has_visible_nonzero_rect(item: dict[str, Any]) -> bool:
    rect = item.get("bounding_client_rect") or item.get("rect")
    if not isinstance(rect, dict):
        return False
    return _float_or_zero(rect.get("width")) > 0 and _float_or_zero(rect.get("height")) > 0


def has_explicit_visible_focus_signal(item: dict[str, Any]) -> bool:
    if item.get("has_visible_indicator") is True or item.get("visible_focus_candidate") is True:
        return True
    signals = item.get("visible_focus_signals") if isinstance(item.get("visible_focus_signals"), dict) else {}
    if any(signals.get(key) is True for key in ("outline", "boxShadow", "focusRule", "ancestorFocusWithin")):
        return True
    return bool(_list(item.get("focus_css_rules")) or _list(item.get("ancestor_focus_within")))


def matching_focus_entry(item: dict[str, Any], focus_sequence: list[dict[str, Any]]) -> dict[str, Any] | None:
    item_step = item.get("step")
    item_xpath = str(item.get("xpath") or "")
    for candidate in focus_sequence:
        same_step = item_step is not None and candidate.get("step") == item_step
        same_xpath = item_xpath and str(candidate.get("xpath") or "") == item_xpath
        if same_step or same_xpath:
            return candidate
    return None


def unique_focus_targets(focus_sequence: list[dict[str, Any]]) -> set[str]:
    return {target for item in focus_sequence for target in [focus_target_key(item)] if target}


def focus_target_key(item: dict[str, Any]) -> str:
    return str(item.get("xpath") or item.get("active_element_name") or item.get("active_element_text") or "").strip()


def is_form_control_event(item: dict[str, Any]) -> bool:
    target = item.get("target") if isinstance(item.get("target"), dict) else {}
    tag = str(target.get("tag") or item.get("tag") or "").lower()
    role = str(target.get("role") or item.get("role") or "").lower()
    xpath = str(target.get("xpath") or item.get("xpath") or "").lower()
    return tag in {"input", "select", "textarea", "button", "form"} or role in INTERACTIVE_ROLES or any(
        token in xpath for token in ("/input", "/select", "/textarea", "/button", "/form")
    )


def _target_summary(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag": target.get("tag"),
        "role": target.get("role"),
        "name": _short(target.get("name")),
        "xpath": target.get("xpath"),
        "rect": target.get("rect"),
    }


def _compact_focus_style(style: dict[str, Any]) -> dict[str, Any]:
    return {
        "outlineStyle": style.get("outlineStyle"),
        "outlineWidth": style.get("outlineWidth"),
        "outlineColor": style.get("outlineColor"),
        "boxShadow": style.get("boxShadow"),
        "border": style.get("border"),
        "backgroundColor": style.get("backgroundColor"),
        "textDecoration": style.get("textDecoration"),
    }


def _is_keyboard_navigation_event(event: dict[str, Any]) -> bool:
    key = str(event.get("key") or event.get("code") or "").strip().lower()
    event_type = str(event.get("type") or "").strip().lower()
    if event_type and event_type not in {"keydown", "keyup", "keypress"}:
        return False
    return key in {"tab", "arrowdown", "arrowleft", "arrowright", "arrowup", "enter", "space", " "}


def _is_observed(item: dict[str, Any]) -> bool:
    source = str(item.get("source") or "").strip()
    quality = str(item.get("quality") or "").strip()
    return bool(item.get("decisive")) and source in {"observed_pointer_event", "runtime_event"} or quality == "observed"


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


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


def _short(value: Any, limit: int = 120) -> str | None:
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    return text if len(text) <= limit else text[:limit] + "..."


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


__all__ = [
    "CriterionEvidencePacketBuilder",
    "criterion_family",
    "direct_missing_focus_indicator_refs",
    "has_explicit_visible_focus_signal",
    "keyboard_recovery_observed",
    "media_has_strong_relevance",
    "observed_pointer_cancellation_failure",
    "screenshot_step_numbers",
]
