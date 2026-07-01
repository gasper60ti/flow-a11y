from __future__ import annotations

from typing import Any

from dynamic.probes.animation_flashing_probe import AnimationFlashingProbe
from dynamic.probes.consistency_navigation_probe import ConsistencyNavigationProbe
from dynamic.probes.forms_errors_auth_probe import FormsErrorsAuthProbe
from dynamic.probes.hover_focus_content_probe import HoverFocusContentProbe
from dynamic.probes.keyboard_focus_probe import KeyboardFocusProbe
from dynamic.probes.media_probe import MediaProbe
from dynamic.probes.pointer_gesture_probe import PointerGestureProbe
from dynamic.probes.status_message_probe import StatusMessageProbe
from dynamic.probes.timing_interruption_probe import TimingInterruptionProbe
from dynamic.schemas.runtime_state import RuntimeState


class DynamicProbeRegistry:
    def __init__(self) -> None:
        self._probes = {
            "keyboard_focus_probe": KeyboardFocusProbe(),
            "hover_focus_content_probe": HoverFocusContentProbe(),
            "timing_interruption_probe": TimingInterruptionProbe(),
            "animation_flashing_probe": AnimationFlashingProbe(),
            "pointer_gesture_probe": PointerGestureProbe(),
            "consistency_navigation_probe": ConsistencyNavigationProbe(),
            "forms_errors_auth_probe": FormsErrorsAuthProbe(),
            "status_message_probe": StatusMessageProbe(),
            "media_probe": MediaProbe(),
        }

    @property
    def probe_names(self) -> list[str]:
        return sorted(self._probes.keys())

    def collect_all(self, states: list[RuntimeState]) -> dict[str, dict[str, Any]]:
        return {name: self._probes[name].collect(states) for name in self.probe_names}

    def collect_for_probes(self, probe_names: list[str], states: list[RuntimeState]) -> dict[str, dict[str, Any]]:
        evidence: dict[str, dict[str, Any]] = {}
        for probe_name in sorted(set(probe_names)):
            probe = self._probes.get(probe_name)
            if probe is None:
                continue
            evidence[probe_name] = probe.collect(states)
        return evidence


__all__ = [
    "DynamicProbeRegistry",
]
