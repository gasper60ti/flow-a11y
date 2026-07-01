from __future__ import annotations

from typing import Any

from dynamic.schemas.wcag_dynamic import DynamicCriterion


class DynamicEvidenceValidator:
    """Validate required runtime evidence keys for a criterion."""

    LIST_EVIDENCE_KEYS = {
        "auth_snapshots",
        "background_audio_indicators",
        "caption_tracks",
        "character_key_shortcuts",
        "concurrent_input_indicators",
        "dragging_indicators",
        "error_messages",
        "flash_events",
        "focus_change_events",
        "focus_obscured_trace",
        "focus_sequence",
        "focus_visible_trace",
        "form_action_error_feedback",
        "form_control_states",
        "form_snapshots",
        "help_for_forms",
        "contextual_help_checks",
        "contextual_help_failures",
        "help_snapshots",
        "identification_snapshots",
        "interaction_animation_events",
        "interruption_signals",
        "keyboard_events",
        "keyboard_focus_transitions",
        "media_elements",
        "motion_actuation_indicators",
        "navigation_snapshots",
        "pointer_cancellation_indicators",
        "pointer_handlers",
        "reauthentication_events",
        "redundant_entry_indicators",
        "observed_redundant_entry_failures",
        "observed_auth_events",
        "auth_barrier_failures",
        "error_prevention_failures",
        "screenshot_paths",
        "status_message_mutations",
        "timeout_warnings",
        "timing_snapshots",
        "transcript_indicators",
        "url_sequence",
    }

    NON_EMPTY_STRING_LIST_KEYS = {"screenshot_paths"}

    def missing_evidence(self, criterion: DynamicCriterion, criterion_evidence: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        for key in criterion.required_evidence:
            if key not in criterion_evidence:
                missing.append(key)
                continue
            value = criterion_evidence.get(key)
            if not self._is_structurally_usable(key, value):
                missing.append(key)
        return missing

    def _is_structurally_usable(self, key: str, value: Any) -> bool:
        if value is None:
            return False
        if key in self.NON_EMPTY_STRING_LIST_KEYS:
            return isinstance(value, list) and any(isinstance(item, str) and item.strip() for item in value)
        if key in self.LIST_EVIDENCE_KEYS:
            return isinstance(value, list)
        return True
