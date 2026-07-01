from __future__ import annotations

from typing import Any

from dynamic.probes.common import collect_runtime_instrumentation
from dynamic.schemas.runtime_state import RuntimeState


class PointerGestureProbe:
    name = "pointer_gesture_probe"

    def collect(self, states: list[RuntimeState]) -> dict[str, Any]:
        pointer_handlers: list[dict[str, Any]] = []
        dragging_indicators: list[dict[str, Any]] = []
        cancellation_indicators: list[dict[str, Any]] = []
        observed_pointer_events = collect_runtime_instrumentation(states, "pointer_events")
        event_listener_registrations = collect_runtime_instrumentation(states, "event_listener_registrations")

        for event in observed_pointer_events:
            pointer_handlers.append(
                {
                    "step": event.get("step"),
                    "url": event.get("url", ""),
                    "source": "observed_pointer_event",
                    "quality": "observed",
                    "decisive": True,
                    "event_type": event.get("type"),
                    "pointer_type": event.get("pointer_type"),
                    "target": event.get("target"),
                }
            )
            if event.get("type") in {"pointercancel", "pointerup", "mouseup", "touchend"}:
                cancellation_indicators.append(
                    {
                        "step": event.get("step"),
                        "url": event.get("url", ""),
                        "source": "observed_pointer_event",
                        "quality": "observed",
                        "decisive": True,
                        "event_type": event.get("type"),
                        "target": event.get("target"),
                    }
                )
            if event.get("type") in {"dragstart", "dragend", "drop"}:
                dragging_indicators.append(
                    {
                        "step": event.get("step"),
                        "url": event.get("url", ""),
                        "source": "observed_pointer_event",
                        "quality": "observed",
                        "decisive": True,
                        "event_type": event.get("type"),
                        "has_keyboard_alternative": False,
                    }
                )

        for registration in event_listener_registrations:
            event_type = str(registration.get("type") or "")
            if event_type.startswith(("pointer", "mouse", "touch", "click", "drag", "drop")):
                pointer_handlers.append(
                    {
                        "step": registration.get("step"),
                        "url": registration.get("url", ""),
                        "source": "event_listener_registration",
                        "quality": "event_listener",
                        "decisive": True,
                        "event_type": event_type,
                        "target": registration.get("target"),
                    }
                )

        for step, state in enumerate(states, start=1):
            dom = (state.dom_html or "").lower()
            if any(token in dom for token in ["onpointer", "ontouch", "touch-action", "pointerdown", "pointerup"]):
                pointer_handlers.append(
                    {
                        "step": step,
                        "url": state.url,
                        "source": "dom_token",
                        "quality": "static_candidate",
                        "decisive": False,
                    }
                )
            if "draggable" in dom or "dragstart" in dom or "ondrag" in dom:
                dragging_indicators.append(
                    {
                        "step": step,
                        "url": state.url,
                        "source": "dom_token",
                        "quality": "static_candidate",
                        "decisive": False,
                        "has_keyboard_alternative": any(
                            token in dom
                            for token in [
                                "keyboard-alternative",
                                "keyboard alternative",
                                "move up",
                                "move down",
                                "aria-keyshortcuts",
                            ]
                        ),
                    }
                )
            if "pointercancel" in dom or "oncancel" in dom or "mouseup" in dom:
                cancellation_indicators.append(
                    {
                        "step": step,
                        "url": state.url,
                        "source": "dom_token",
                        "quality": "static_candidate",
                        "decisive": False,
                    }
                )

        return {
            "pointer_handlers": pointer_handlers,
            "dragging_indicators": dragging_indicators,
            "pointer_cancellation_indicators": cancellation_indicators,
            "observed_pointer_events": observed_pointer_events,
            "event_listener_registrations": event_listener_registrations,
            "concurrent_input_indicators": pointer_handlers,
            "motion_actuation_indicators": [item for item in pointer_handlers if "motion" in item.get("url", "")],
        }
