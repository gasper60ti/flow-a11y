from __future__ import annotations

from typing import Any

from dynamic.probes.common import collect_screenshot_paths
from dynamic.schemas.runtime_state import RuntimeState


class AnimationFlashingProbe:
    name = "animation_flashing_probe"

    def collect(self, states: list[RuntimeState]) -> dict[str, Any]:
        animation_events: list[dict[str, Any]] = []
        flash_events: list[dict[str, Any]] = []

        for step, state in enumerate(states, start=1):
            dom_text = (state.dom_html or "").lower()
            mutation_text = " ".join(str(item).lower() for item in (state.dom_mutations or []))
            text = f"{dom_text} {mutation_text}"

            if any(token in text for token in ["animation", "transition", "autoplay", "marquee", "blink"]):
                animation_events.append({"step": step, "url": state.url})
            if any(token in text for token in ["flash", "strobe", "flicker"]):
                flash_events.append(
                    {
                        "step": step,
                        "url": state.url,
                        "below_threshold": any(
                            token in text
                            for token in ["below-threshold", "below threshold", "safe-flash", "two-flash"]
                        ),
                    }
                )

        return {
            "animation_events": animation_events,
            "flash_events": flash_events,
            "interaction_animation_events": animation_events,
            "screenshot_paths": collect_screenshot_paths(states),
        }
