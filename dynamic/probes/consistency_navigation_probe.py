from __future__ import annotations

from typing import Any

from dynamic.schemas.runtime_state import RuntimeState


class ConsistencyNavigationProbe:
    name = "consistency_navigation_probe"

    def collect(self, states: list[RuntimeState]) -> dict[str, Any]:
        nav_snapshots: list[dict[str, Any]] = []
        help_snapshots: list[dict[str, Any]] = []

        for step, state in enumerate(states, start=1):
            dom_html = (state.dom_html or "").lower()
            if dom_html:
                nav_snapshots.append(
                    {
                        "step": step,
                        "url": state.url,
                        "has_nav": "<nav" in dom_html,
                        "has_search": "search" in dom_html,
                    }
                )
                help_snapshots.append(
                    {
                        "step": step,
                        "url": state.url,
                        "has_help": any(token in dom_html for token in ["help", "support", "contact"]),
                    }
                )

        return {
            "navigation_snapshots": nav_snapshots,
            "identification_snapshots": nav_snapshots,
            "help_snapshots": help_snapshots,
            "url_sequence": [state.url for state in states],
        }
