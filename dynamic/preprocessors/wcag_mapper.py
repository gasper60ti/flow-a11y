"""Dynamic WCAG criterion mapping helpers."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from dynamic.schemas.wcag_dynamic import DynamicCriterion, DynamicCriterionLevel


WCAG_DYNAMIC_CRITERIA: dict[str, DynamicCriterion] = {
    "1.2.1": DynamicCriterion(
        id="1.2.1",
        name="Audio-only and Video-only (Prerecorded)",
        level=DynamicCriterionLevel.A,
        probe="media_probe",
        required_evidence=["media_elements"],
    ),
    "1.2.2": DynamicCriterion(
        id="1.2.2",
        name="Captions (Prerecorded)",
        level=DynamicCriterionLevel.A,
        probe="media_probe",
        required_evidence=["media_elements", "caption_tracks"],
        requires_llm=True,
    ),
    "1.2.3": DynamicCriterion(
        id="1.2.3",
        name="Audio Description or Media Alternative (Prerecorded)",
        level=DynamicCriterionLevel.A,
        probe="media_probe",
        required_evidence=["media_elements", "transcript_indicators"],
    ),
    "1.2.4": DynamicCriterion(
        id="1.2.4",
        name="Captions (Live)",
        level=DynamicCriterionLevel.AA,
        probe="media_probe",
        required_evidence=["media_elements", "caption_tracks"],
    ),
    "1.2.5": DynamicCriterion(
        id="1.2.5",
        name="Audio Description (Prerecorded)",
        level=DynamicCriterionLevel.AA,
        probe="media_probe",
        required_evidence=["media_elements", "transcript_indicators"],
        requires_llm=True,
    ),
    "1.2.6": DynamicCriterion(
        id="1.2.6",
        name="Sign Language (Prerecorded)",
        level=DynamicCriterionLevel.AAA,
        probe="media_probe",
        required_evidence=["media_elements", "transcript_indicators"],
    ),
    "1.2.7": DynamicCriterion(
        id="1.2.7",
        name="Extended Audio Description (Prerecorded)",
        level=DynamicCriterionLevel.AAA,
        probe="media_probe",
        required_evidence=["media_elements", "transcript_indicators"],
    ),
    "1.2.8": DynamicCriterion(
        id="1.2.8",
        name="Media Alternative (Prerecorded)",
        level=DynamicCriterionLevel.AAA,
        probe="media_probe",
        required_evidence=["media_elements", "transcript_indicators"],
    ),
    "1.2.9": DynamicCriterion(
        id="1.2.9",
        name="Audio-only (Live)",
        level=DynamicCriterionLevel.AAA,
        probe="media_probe",
        required_evidence=["media_elements"],
    ),
    "1.4.7": DynamicCriterion(
        id="1.4.7",
        name="Low or No Background Audio",
        level=DynamicCriterionLevel.AAA,
        probe="media_probe",
        required_evidence=["media_elements", "background_audio_indicators"],
        requires_llm=True,
    ),
    "1.4.13": DynamicCriterion(
        id="1.4.13",
        name="Content on Hover or Focus",
        level=DynamicCriterionLevel.AA,
        probe="hover_focus_content_probe",
        required_evidence=["hover_focus_revealed_content", "focus_sequence"],
        requires_llm=True,
    ),
    "2.1.1": DynamicCriterion(
        id="2.1.1",
        name="Keyboard",
        level=DynamicCriterionLevel.A,
        probe="keyboard_focus_probe",
        required_evidence=["keyboard_events", "focus_sequence"],
    ),
    "2.1.2": DynamicCriterion(
        id="2.1.2",
        name="No Keyboard Trap",
        level=DynamicCriterionLevel.A,
        probe="keyboard_focus_probe",
        required_evidence=["keyboard_events", "focus_sequence"],
    ),
    "2.1.3": DynamicCriterion(
        id="2.1.3",
        name="Keyboard (No Exception)",
        level=DynamicCriterionLevel.AAA,
        probe="keyboard_focus_probe",
        required_evidence=["keyboard_events", "focus_sequence"],
    ),
    "2.1.4": DynamicCriterion(
        id="2.1.4",
        name="Character Key Shortcuts",
        level=DynamicCriterionLevel.A,
        probe="keyboard_focus_probe",
        required_evidence=["character_key_shortcuts", "keyboard_events"],
    ),
    "2.2.3": DynamicCriterion(
        id="2.2.3",
        name="No Timing",
        level=DynamicCriterionLevel.AAA,
        probe="timing_interruption_probe",
        required_evidence=["timing_snapshots"],
        requires_llm=True,
    ),
    "2.2.4": DynamicCriterion(
        id="2.2.4",
        name="Interruptions",
        level=DynamicCriterionLevel.AAA,
        probe="timing_interruption_probe",
        required_evidence=["interruption_signals"],
        requires_llm=True,
    ),
    "2.2.5": DynamicCriterion(
        id="2.2.5",
        name="Re-authenticating",
        level=DynamicCriterionLevel.AAA,
        probe="timing_interruption_probe",
        required_evidence=["reauthentication_events"],
        requires_llm=True,
    ),
    "2.2.6": DynamicCriterion(
        id="2.2.6",
        name="Timeouts",
        level=DynamicCriterionLevel.AAA,
        probe="timing_interruption_probe",
        required_evidence=["timeout_warnings"],
        requires_llm=True,
    ),
    "2.3.1": DynamicCriterion(
        id="2.3.1",
        name="Three Flashes or Below Threshold",
        level=DynamicCriterionLevel.A,
        probe="animation_flashing_probe",
        required_evidence=["flash_events"],
    ),
    "2.3.2": DynamicCriterion(
        id="2.3.2",
        name="Three Flashes",
        level=DynamicCriterionLevel.AAA,
        probe="animation_flashing_probe",
        required_evidence=["flash_events"],
    ),
    "2.3.3": DynamicCriterion(
        id="2.3.3",
        name="Animation from Interactions",
        level=DynamicCriterionLevel.AAA,
        probe="animation_flashing_probe",
        required_evidence=["interaction_animation_events"],
        requires_llm=True,
    ),
    "2.4.3": DynamicCriterion(
        id="2.4.3",
        name="Focus Order",
        level=DynamicCriterionLevel.A,
        probe="keyboard_focus_probe",
        required_evidence=["focus_sequence"],
    ),
    "2.4.7": DynamicCriterion(
        id="2.4.7",
        name="Focus Visible",
        level=DynamicCriterionLevel.AA,
        probe="keyboard_focus_probe",
        required_evidence=["focus_visible_trace", "screenshot_paths"],
    ),
    "2.4.11": DynamicCriterion(
        id="2.4.11",
        name="Focus Not Obscured (Minimum)",
        level=DynamicCriterionLevel.AA,
        probe="keyboard_focus_probe",
        required_evidence=["focus_obscured_trace", "focus_sequence"],
    ),
    "2.4.12": DynamicCriterion(
        id="2.4.12",
        name="Focus Not Obscured (Enhanced)",
        level=DynamicCriterionLevel.AAA,
        probe="keyboard_focus_probe",
        required_evidence=["focus_obscured_trace", "focus_sequence"],
    ),
    "2.4.13": DynamicCriterion(
        id="2.4.13",
        name="Focus Appearance",
        level=DynamicCriterionLevel.AAA,
        probe="keyboard_focus_probe",
        required_evidence=["focus_visible_trace", "screenshot_paths"],
    ),
    "2.5.1": DynamicCriterion(
        id="2.5.1",
        name="Pointer Gestures",
        level=DynamicCriterionLevel.A,
        probe="pointer_gesture_probe",
        required_evidence=["pointer_handlers"],
        requires_llm=True,
    ),
    "2.5.2": DynamicCriterion(
        id="2.5.2",
        name="Pointer Cancellation",
        level=DynamicCriterionLevel.A,
        probe="pointer_gesture_probe",
        required_evidence=["pointer_cancellation_indicators"],
    ),
    "2.5.4": DynamicCriterion(
        id="2.5.4",
        name="Motion Actuation",
        level=DynamicCriterionLevel.A,
        probe="pointer_gesture_probe",
        required_evidence=["motion_actuation_indicators"],
    ),
    "2.5.6": DynamicCriterion(
        id="2.5.6",
        name="Concurrent Input Mechanisms",
        level=DynamicCriterionLevel.AAA,
        probe="pointer_gesture_probe",
        required_evidence=["concurrent_input_indicators"],
    ),
    "2.5.7": DynamicCriterion(
        id="2.5.7",
        name="Dragging Movements",
        level=DynamicCriterionLevel.AA,
        probe="pointer_gesture_probe",
        required_evidence=["dragging_indicators"],
    ),
    "3.2.1": DynamicCriterion(
        id="3.2.1",
        name="On Focus",
        level=DynamicCriterionLevel.A,
        probe="keyboard_focus_probe",
        required_evidence=["focus_change_events", "focus_sequence"],
    ),
    "3.2.3": DynamicCriterion(
        id="3.2.3",
        name="Consistent Navigation",
        level=DynamicCriterionLevel.AA,
        probe="consistency_navigation_probe",
        required_evidence=["navigation_snapshots", "url_sequence"],
        requires_llm=True,
    ),
    "3.2.4": DynamicCriterion(
        id="3.2.4",
        name="Consistent Identification",
        level=DynamicCriterionLevel.AA,
        probe="consistency_navigation_probe",
        required_evidence=["identification_snapshots", "url_sequence"],
        requires_llm=True,
    ),
    "3.2.6": DynamicCriterion(
        id="3.2.6",
        name="Consistent Help",
        level=DynamicCriterionLevel.A,
        probe="consistency_navigation_probe",
        required_evidence=["help_snapshots", "url_sequence"],
        requires_llm=True,
    ),
    "3.3.1": DynamicCriterion(
        id="3.3.1",
        name="Error Identification",
        level=DynamicCriterionLevel.A,
        probe="forms_errors_auth_probe",
        required_evidence=["form_snapshots", "error_messages"],
    ),
    "3.3.3": DynamicCriterion(
        id="3.3.3",
        name="Error Suggestion",
        level=DynamicCriterionLevel.AA,
        probe="forms_errors_auth_probe",
        required_evidence=["form_snapshots", "error_messages"],
    ),
    "3.3.4": DynamicCriterion(
        id="3.3.4",
        name="Error Prevention (Legal, Financial, Data)",
        level=DynamicCriterionLevel.AA,
        probe="forms_errors_auth_probe",
        required_evidence=["form_snapshots"],
        requires_llm=True,
    ),
    "3.3.5": DynamicCriterion(
        id="3.3.5",
        name="Help",
        level=DynamicCriterionLevel.AAA,
        probe="forms_errors_auth_probe",
        required_evidence=["help_for_forms"],
        requires_llm=True,
    ),
    "3.3.6": DynamicCriterion(
        id="3.3.6",
        name="Error Prevention (All)",
        level=DynamicCriterionLevel.AAA,
        probe="forms_errors_auth_probe",
        required_evidence=["form_snapshots"],
        requires_llm=True,
    ),
    "3.3.7": DynamicCriterion(
        id="3.3.7",
        name="Redundant Entry",
        level=DynamicCriterionLevel.A,
        probe="forms_errors_auth_probe",
        required_evidence=["redundant_entry_indicators", "form_snapshots"],
        requires_llm=True,
    ),
    "3.3.8": DynamicCriterion(
        id="3.3.8",
        name="Accessible Authentication (Minimum)",
        level=DynamicCriterionLevel.AA,
        probe="forms_errors_auth_probe",
        required_evidence=["auth_snapshots"],
        requires_llm=True,
    ),
    "3.3.9": DynamicCriterion(
        id="3.3.9",
        name="Accessible Authentication (Enhanced)",
        level=DynamicCriterionLevel.AAA,
        probe="forms_errors_auth_probe",
        required_evidence=["auth_snapshots"],
        requires_llm=True,
    ),
    "4.1.3": DynamicCriterion(
        id="4.1.3",
        name="Status Messages",
        level=DynamicCriterionLevel.AA,
        probe="status_message_probe",
        required_evidence=["status_message_mutations"],
    ),
}


class DynamicWCAGCriterionRegistry:
    """Criterion registry used by the dynamic formal WCAG analyzer."""

    def __init__(self, criteria: dict[str, DynamicCriterion]) -> None:
        self._criteria = dict(criteria)
        probe_map: dict[str, list[str]] = defaultdict(list)
        for criterion_id, criterion in self._criteria.items():
            probe_map[criterion.probe].append(criterion_id)
        self._probe_map = {probe: sorted(values) for probe, values in probe_map.items()}

    def get(self, criterion_id: str) -> DynamicCriterion:
        if criterion_id not in self._criteria:
            raise KeyError(f"Unknown dynamic WCAG criterion: {criterion_id}")
        return self._criteria[criterion_id]

    def is_supported(self, criterion_id: str) -> bool:
        return criterion_id in self._criteria

    def supported_ids(self) -> list[str]:
        return sorted(self._criteria.keys())

    def supported_probes(self) -> list[str]:
        return sorted(self._probe_map.keys())

    def criteria_for_probe(self, probe_name: str) -> list[str]:
        return list(self._probe_map.get(probe_name, []))

    def resolve(self, criteria: Iterable[str] | None = None) -> tuple[list[str], list[str]]:
        if criteria is None:
            return self.supported_ids(), []
        requested = [str(criterion_id) for criterion_id in criteria]
        unsupported = [criterion_id for criterion_id in requested if not self.is_supported(criterion_id)]
        supported = [criterion_id for criterion_id in requested if self.is_supported(criterion_id)]
        return supported, unsupported


DYNAMIC_WCAG_REGISTRY = DynamicWCAGCriterionRegistry(WCAG_DYNAMIC_CRITERIA)
SUPPORTED_DYNAMIC_CRITERIA = tuple(DYNAMIC_WCAG_REGISTRY.supported_ids())


def get_criterion(criterion_id: str) -> DynamicCriterion:
    return DYNAMIC_WCAG_REGISTRY.get(criterion_id)


def prompt_filename(criterion_id: str) -> str:
    return f"sc_{criterion_id.replace('.', '_')}.txt"
