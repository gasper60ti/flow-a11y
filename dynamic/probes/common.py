from __future__ import annotations

from typing import Any

from dynamic.schemas.runtime_state import RuntimeState


def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def collect_screenshot_paths(states: list[RuntimeState]) -> list[str]:
    return [state.screenshot_path for state in states if state.screenshot_path]


def collect_keyboard_events(states: list[RuntimeState]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for step, state in enumerate(states, start=1):
        keyboard_state = state.keyboard_state or {}
        raw_events = keyboard_state.get("events", []) if isinstance(keyboard_state, dict) else []
        if not isinstance(raw_events, list):
            continue
        for event in raw_events:
            if not isinstance(event, dict):
                continue
            enriched = dict(event)
            enriched["step"] = step
            enriched.setdefault("quality", "observed")
            enriched.setdefault("source", "runtime_event")
            enriched.setdefault("decisive", True)
            events.append(enriched)
    return events


def collect_keyboard_focus_transitions(states: list[RuntimeState]) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    for event in collect_keyboard_events(states):
        if not isinstance(event, dict):
            continue
        before = event.get("active_element_before")
        after = event.get("active_element_after")
        if not isinstance(before, dict) and not isinstance(after, dict):
            continue
        transitions.append(
            {
                "step": event.get("step"),
                "type": event.get("type"),
                "key": event.get("key"),
                "code": event.get("code"),
                "active_element_before": before if isinstance(before, dict) else {},
                "active_element_after": after if isinstance(after, dict) else {},
                "focus_changed": event.get("focus_changed"),
                "quality": "observed",
                "source": "runtime_event",
                "decisive": True,
            }
        )
    return transitions


def collect_runtime_instrumentation(states: list[RuntimeState], key: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for step, state in enumerate(states, start=1):
        metadata = state.interaction_metadata or {}
        instrumentation = metadata.get("runtime_instrumentation", {}) if isinstance(metadata, dict) else {}
        raw_events = instrumentation.get(key, []) if isinstance(instrumentation, dict) else []
        if not isinstance(raw_events, list):
            continue
        for event in raw_events:
            if not isinstance(event, dict):
                continue
            enriched = dict(event)
            enriched.setdefault("step", step)
            enriched.setdefault("url", state.url)
            events.append(enriched)
    return events


def collect_focus_sequence(states: list[RuntimeState]) -> list[dict[str, Any]]:
    sequence: list[dict[str, Any]] = []
    for step, state in enumerate(states, start=1):
        focused = state.focused_element
        if not isinstance(focused, dict) or not focused:
            continue
        focused_quality = "observed"
        focused_source = "accessibility_tree"
        if focused.get("computed_style"):
            focused_quality = "computed"
            focused_source = "computed_style"
        sequence.append(
            {
                "step": step,
                "xpath": focused.get("xpath"),
                "tag": focused.get("tag"),
                "id": focused.get("id"),
                "role": focused.get("role"),
                "aria_label": focused.get("aria_label"),
                "attributes": focused.get("attributes") or {},
                "computed_style": focused.get("computed_style") or {},
                "bounding_client_rect": focused.get("bounding_client_rect"),
                "viewport": focused.get("viewport"),
                "active_element_name": focused.get("active_element_name") or focused.get("name"),
                "active_element_text": focused.get("active_element_text") or focused.get("text"),
                "visible_focus_candidate": focused.get("visible_focus_candidate"),
                "focus_visible_match": focused.get("focus_visible_match"),
                "focus_css_rules": focused.get("focus_css_rules") or [],
                "ancestor_focus_within": focused.get("ancestor_focus_within") or [],
                "visible_focus_signals": focused.get("visible_focus_signals") or {},
                "quality": focused_quality,
                "source": focused_source,
                "decisive": True,
            }
        )
    return sequence


def flatten_probe_evidence(probe_evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for evidence in probe_evidence.values():
        for key, value in evidence.items():
            flat[key] = value
    return flat
