from __future__ import annotations

from typing import Any

from dynamic.probes.common import (
    collect_focus_sequence,
    collect_keyboard_events,
    collect_keyboard_focus_transitions,
    collect_runtime_instrumentation,
    collect_screenshot_paths,
)
from dynamic.schemas.runtime_state import RuntimeState


class KeyboardFocusProbe:
    name = "keyboard_focus_probe"

    def collect(self, states: list[RuntimeState]) -> dict[str, Any]:
        focus_sequence = collect_focus_sequence(states)
        keyboard_events = collect_keyboard_events(states)
        keyboard_focus_transitions = collect_keyboard_focus_transitions(states)
        observed_focus_events = [
            {**event, "quality": "observed", "source": "runtime_event", "decisive": True}
            for event in collect_runtime_instrumentation(states, "focus_events")
        ]
        screenshot_paths = collect_screenshot_paths(states)

        focus_visible_trace: list[dict[str, Any]] = []
        for entry in focus_sequence:
            attrs = entry.get("attributes") or {}
            class_name = str(attrs.get("class") or "").lower()
            computed = entry.get("computed_style") or {}
            visible_focus_candidate = entry.get("visible_focus_candidate")
            visible_signals = entry.get("visible_focus_signals") or {}
            computed_indicator = bool(visible_focus_candidate) or any(
                visible_signals.get(key) is True
                for key in ("outline", "boxShadow", "focusRule", "ancestorFocusWithin")
            )
            static_indicator = any(token in class_name for token in ["focus", "active", "selected"])
            if computed_indicator:
                has_visible_indicator: bool | None = True
                quality = "computed"
                source = "computed_style"
                decisive = True
            elif visible_focus_candidate is False:
                has_visible_indicator: bool | None = False
                quality = "computed"
                source = "computed_style"
                decisive = True
            else:
                has_visible_indicator = None
                quality = "static_candidate" if static_indicator else "weak_keyword"
                source = "dom_token" if static_indicator else "text_keyword"
                decisive = False
            focus_visible_trace.append(
                {
                    "step": entry.get("step"),
                    "xpath": entry.get("xpath"),
                    "has_visible_indicator": has_visible_indicator,
                    "computed_style": computed,
                    "bounding_client_rect": entry.get("bounding_client_rect"),
                    "viewport": entry.get("viewport"),
                    "visible_focus_candidate": visible_focus_candidate,
                    "focus_visible_match": entry.get("focus_visible_match"),
                    "focus_css_rules": entry.get("focus_css_rules") or [],
                    "ancestor_focus_within": entry.get("ancestor_focus_within") or [],
                    "visible_focus_signals": visible_signals,
                    "quality": quality,
                    "source": source,
                    "decisive": decisive,
                }
            )

        character_key_shortcuts = [
            event
            for event in keyboard_events
            if isinstance(event.get("key"), str)
            and len(str(event.get("key"))) == 1
            and not any(bool(event.get(modifier)) for modifier in ["ctrl_key", "alt_key", "meta_key"])
        ]

        return {
            "focus_sequence": focus_sequence,
            "keyboard_events": keyboard_events,
            "keyboard_focus_transitions": keyboard_focus_transitions,
            "focus_visible_trace": focus_visible_trace,
            "focus_obscured_trace": [],
            "character_key_shortcuts": [
                {**event, "quality": "observed", "source": "runtime_event", "decisive": True}
                for event in character_key_shortcuts
            ],
            "focus_change_events": [
                {
                    "xpath": entry.get("xpath"),
                    "step": entry.get("step"),
                    "quality": entry.get("quality", "observed"),
                    "source": entry.get("source", "accessibility_tree"),
                    "decisive": True,
                }
                for entry in focus_sequence
                if entry.get("xpath")
            ]
            + observed_focus_events,
            "observed_focus_events": observed_focus_events,
            "screenshot_paths": screenshot_paths,
        }
