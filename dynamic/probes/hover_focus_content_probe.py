from __future__ import annotations

from typing import Any

from dynamic.probes.common import collect_focus_sequence, collect_screenshot_paths
from dynamic.schemas.runtime_state import RuntimeState


class HoverFocusContentProbe:
    name = "hover_focus_content_probe"

    def collect(self, states: list[RuntimeState]) -> dict[str, Any]:
        hover_focus_reveals: list[dict[str, Any]] = []
        for step, state in enumerate(states, start=1):
            dom_html = (state.dom_html or "").lower()
            if not dom_html:
                continue
            has_hover_ui = any(token in dom_html for token in ["tooltip", "popover", "aria-describedby", ":hover"])
            if has_hover_ui:
                hover_focus_reveals.append(
                    {
                        "step": step,
                        "url": state.url,
                        "has_dismiss_control": "close" in dom_html or "dismiss" in dom_html,
                        "has_persistent_content_hint": "tooltip" in dom_html or "popover" in dom_html,
                    }
                )

        return {
            "hover_focus_revealed_content": hover_focus_reveals,
            "focus_sequence": collect_focus_sequence(states),
            "screenshot_paths": collect_screenshot_paths(states),
        }
