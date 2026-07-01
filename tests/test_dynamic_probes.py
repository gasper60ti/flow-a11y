from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dynamic.probes import DynamicProbeRegistry
from dynamic.runtime_extractors.focus_runtime import FocusRuntimeExtractor
from dynamic.runtime_extractors.media_runtime import MediaRuntimeExtractor
from dynamic.schemas.runtime_state import RuntimeState


def _state(
    ts: datetime,
    *,
    url: str,
    dom_html: str,
    screenshot_path: str,
    focused_xpath: str | None = None,
    focused_style: str = "",
    focused_class: str = "",
    keyboard_events: list[dict] | None = None,
    dom_mutations: list[dict] | None = None,
    media_state: dict | None = None,
    timing_info: dict | None = None,
    focused_extra: dict | None = None,
    interaction_metadata: dict | None = None,
) -> RuntimeState:
    focused_element = None
    if focused_xpath is not None:
        focused_element = {
            "xpath": focused_xpath,
            "tag": "button",
            "attributes": {
                "style": focused_style,
                "class": focused_class,
            },
        }
        if focused_extra:
            focused_element.update(focused_extra)

    return RuntimeState(
        timestamp=ts,
        url=url,
        dom_html=dom_html,
        screenshot_path=screenshot_path,
        focused_element=focused_element,
        keyboard_state={"events": keyboard_events or []},
        dom_mutations=dom_mutations or [],
        media_state=media_state,
        timing_info=timing_info,
        console_logs=[],
        interaction_metadata=interaction_metadata,
    )


def _build_states(tmp_path: Path) -> list[RuntimeState]:
    shot1 = tmp_path / "step_1.png"
    shot2 = tmp_path / "step_2.png"
    shot3 = tmp_path / "step_3.png"
    shot1.write_bytes(b"a")
    shot2.write_bytes(b"b")
    shot3.write_bytes(b"c")

    base = datetime.now(timezone.utc)
    return [
        _state(
            base,
            url="https://example.com/search",
            dom_html=(
                '<form><input required></form>'
                '<div class="tooltip">Hover help</div>'
                '<div onpointerdown="x" pointercancel="y" draggable="true"></div>'
                '<video></video><track kind="captions" />'
                '<div role="status">Updated</div>'
                'session expired modal reauth sign in password error required'
            ),
            screenshot_path=str(shot1),
            focused_xpath="/html/body/button[1]",
            focused_style="outline: 2px solid #000;",
            keyboard_events=[{"key": "Tab"}, {"key": "a"}],
            dom_mutations=[
                {"mutation_type": "observed_live_region_change", "text": "Updated"},
                {"kind": "animation", "text": "flash"},
            ],
            media_state={
                "media": [
                    {
                        "tag": "video",
                        "muted": False,
                        "caption_tracks": [{"kind": "captions", "label": "English"}],
                        "text_tracks": [{"kind": "captions", "label": "English", "mode": "showing"}],
                        "current_time_delta": 1.25,
                        "audible": True,
                        "ready_state": 4,
                    }
                ],
                "media_events": [{"type": "play", "current_time": 1.25}],
            },
            timing_info={"deadline_seconds": 60},
            focused_extra={
                "computed_style": {
                    "outline": "2px solid rgb(0, 0, 0)",
                    "outlineWidth": "2px",
                    "outlineStyle": "solid",
                    "outlineColor": "rgb(0, 0, 0)",
                    "boxShadow": "none",
                    "border": "0px none rgb(0, 0, 0)",
                    "backgroundColor": "rgb(255, 255, 255)",
                    "color": "rgb(0, 0, 0)",
                },
                "bounding_client_rect": {"x": 10, "y": 20, "width": 80, "height": 30},
                "viewport": {"width": 1280, "height": 720, "deviceScaleFactor": 1},
                "visible_focus_candidate": True,
            },
            interaction_metadata={
                "runtime_instrumentation": {
                    "pointer_events": [{"type": "pointerdown", "pointer_type": "mouse"}],
                    "focus_events": [{"type": "focus", "target": {"name": "Submit"}}],
                    "event_listener_registrations": [{"type": "pointerdown"}],
                }
            },
        ),
        _state(
            base + timedelta(seconds=1),
            url="https://example.com/search",
            dom_html='animation transition flash flicker <nav>help support contact</nav>',
            screenshot_path=str(shot2),
            focused_xpath="/html/body/button[1]",
            focused_style="",
            focused_class="",
            keyboard_events=[{"key": "Tab"}],
            dom_mutations=[{"mutation_type": "static_live_region", "kind": "alert", "text": "interrupt"}],
            media_state={"media": [{"tag": "audio", "muted": True}]},
        ),
        _state(
            base + timedelta(seconds=2),
            url="https://example.com/checkout",
            dom_html='transcript audio description sign language login captcha motion',
            screenshot_path=str(shot3),
            focused_xpath="/html/body/button[2]",
            focused_style="box-shadow: 0 0 0 2px blue;",
            keyboard_events=[{"key": "Tab"}],
            dom_mutations=[{"kind": "log", "text": "status"}],
            media_state={"media": [{"tag": "video", "muted": False}]},
        ),
    ]


def test_probe_outputs_are_deterministic(tmp_path: Path) -> None:
    states = _build_states(tmp_path)
    registry = DynamicProbeRegistry()

    first = registry.collect_all(states)
    second = registry.collect_all(states)

    assert first == second


def test_probe_outputs_cover_expected_dynamic_evidence(tmp_path: Path) -> None:
    states = _build_states(tmp_path)
    registry = DynamicProbeRegistry()
    evidence = registry.collect_all(states)

    keyboard = evidence["keyboard_focus_probe"]
    assert keyboard["focus_sequence"]
    assert keyboard["keyboard_events"]
    assert keyboard["focus_visible_trace"]
    assert keyboard["observed_focus_events"]
    assert {"quality", "source", "decisive"}.issubset(keyboard["focus_sequence"][0])
    assert {"quality", "source", "decisive"}.issubset(keyboard["keyboard_events"][0])
    assert {"quality", "source", "decisive"}.issubset(keyboard["focus_visible_trace"][0])
    assert keyboard["focus_visible_trace"][0]["computed_style"]["outlineWidth"] == "2px"
    assert keyboard["focus_visible_trace"][0]["visible_focus_candidate"] is True

    hover = evidence["hover_focus_content_probe"]
    assert hover["hover_focus_revealed_content"]

    timing = evidence["timing_interruption_probe"]
    assert timing["timeout_warnings"]
    assert timing["interruption_signals"]

    animation = evidence["animation_flashing_probe"]
    assert animation["flash_events"]
    assert animation["interaction_animation_events"]

    pointer = evidence["pointer_gesture_probe"]
    assert pointer["pointer_handlers"]
    assert pointer["pointer_cancellation_indicators"]
    assert pointer["dragging_indicators"]
    assert pointer["observed_pointer_events"]
    assert {"quality", "source", "decisive"}.issubset(pointer["pointer_handlers"][0])
    assert any(item["source"] == "observed_pointer_event" for item in pointer["pointer_handlers"])

    consistency = evidence["consistency_navigation_probe"]
    assert consistency["navigation_snapshots"]
    assert len(consistency["url_sequence"]) == len(states)

    forms = evidence["forms_errors_auth_probe"]
    assert forms["form_snapshots"]
    assert forms["error_messages"]
    assert forms["auth_snapshots"]
    assert {"quality", "source", "decisive"}.issubset(forms["form_snapshots"][0])

    status = evidence["status_message_probe"]
    assert status["status_message_mutations"]
    assert status["observed_status_mutations"]
    assert status["observed_live_region_mutations"]
    assert status["static_live_regions"]
    assert {"quality", "source", "decisive"}.issubset(status["status_message_mutations"][0])

    media = evidence["media_probe"]
    assert media["media_elements"]
    assert media["caption_tracks"]
    assert media["transcript_indicators"]
    assert {"quality", "source", "decisive"}.issubset(media["media_elements"][0])
    assert {"quality", "source", "decisive"}.issubset(media["caption_tracks"][0])
    assert media["media_elements"][0]["current_time_delta"] == 1.25
    assert media["media_elements"][0]["audible"] is True
    assert media["media_events"][0]["type"] == "play"


def test_screenshot_paths_are_collected_for_visual_probes(tmp_path: Path) -> None:
    states = _build_states(tmp_path)
    registry = DynamicProbeRegistry()
    evidence = registry.collect_all(states)

    expected_paths = [state.screenshot_path for state in states]
    assert evidence["keyboard_focus_probe"]["screenshot_paths"] == expected_paths
    assert evidence["hover_focus_content_probe"]["screenshot_paths"] == expected_paths
    assert evidence["animation_flashing_probe"]["screenshot_paths"] == expected_paths


class FakePage:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def evaluate(self, script: str):
        return self.payload


def test_focus_runtime_extractor_emits_computed_style_keys(tmp_path: Path) -> None:
    payload = {
        "tag": "button",
        "xpath": "/html/body/button[1]",
        "computed_style": {
            "outline": "2px solid black",
            "outlineWidth": "2px",
            "outlineStyle": "solid",
            "outlineColor": "rgb(0, 0, 0)",
            "boxShadow": "none",
            "border": "0px none rgb(0, 0, 0)",
            "backgroundColor": "rgb(255, 255, 255)",
            "color": "rgb(0, 0, 0)",
        },
        "bounding_client_rect": {"x": 1, "y": 2, "width": 3, "height": 4},
        "viewport": {"width": 1280, "height": 720, "deviceScaleFactor": 1},
        "active_element_text": "Submit",
        "active_element_name": "Submit",
        "visible_focus_candidate": True,
    }

    result = asyncio.run(FocusRuntimeExtractor().extract(FakePage(payload), tmp_path, 1))

    focused = result["focused_element"]
    assert focused["computed_style"]["outlineWidth"] == "2px"
    assert focused["bounding_client_rect"]["width"] == 3
    assert focused["viewport"]["width"] == 1280
    assert focused["visible_focus_candidate"] is True


def test_media_runtime_extractor_emits_track_and_delta_keys(tmp_path: Path) -> None:
    payload = {
        "media": [
            {
                "tag": "video",
                "tracks": [{"kind": "captions"}],
                "text_tracks": [{"kind": "captions", "mode": "showing"}],
                "captions_or_subtitles": [{"kind": "captions"}],
                "dimensions": {"video_width": 640, "video_height": 360},
                "visible": True,
                "current_time_delta": 0.5,
                "audible": True,
                "ready_state": 4,
                "paused": False,
                "duration": 10,
                "media_control_hints": {"has_caption_control_hint": True},
            }
        ],
        "count": 1,
        "media_events": [{"type": "timeupdate", "current_time": 1.5}],
    }

    result = asyncio.run(MediaRuntimeExtractor().extract(FakePage(payload), tmp_path, 1))

    media = result["media_state"]["media"][0]
    assert media["text_tracks"][0]["kind"] == "captions"
    assert media["current_time_delta"] == 0.5
    assert media["audible"] is True
    assert result["media_state"]["media_events"][0]["type"] == "timeupdate"
