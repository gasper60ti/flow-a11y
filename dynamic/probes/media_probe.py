from __future__ import annotations

from typing import Any

from dynamic.schemas.runtime_state import RuntimeState


class MediaProbe:
    name = "media_probe"

    def collect(self, states: list[RuntimeState]) -> dict[str, Any]:
        media_elements: list[dict[str, Any]] = []
        caption_tracks: list[dict[str, Any]] = []
        transcript_indicators: list[dict[str, Any]] = []
        media_events: list[dict[str, Any]] = []

        for step, state in enumerate(states, start=1):
            media_state = state.media_state or {}
            media_list = media_state.get("media", []) if isinstance(media_state, dict) else []
            if isinstance(media_list, list):
                for media in media_list:
                    if not isinstance(media, dict):
                        continue
                    enriched = {
                        "step": step,
                        "url": state.url,
                        **media,
                        "quality": "observed",
                        "source": "media_api",
                        "decisive": True,
                    }
                    media_elements.append(enriched)
                    for track in media.get("caption_tracks", []) or []:
                        if isinstance(track, dict):
                            caption_tracks.append(
                                {
                                    "step": step,
                                    "url": state.url,
                                    **track,
                                    "quality": "observed",
                                    "source": "media_api",
                                    "decisive": True,
                                }
                            )
                    for track in media.get("captions_or_subtitles", []) or []:
                        if isinstance(track, dict):
                            caption_tracks.append(
                                {
                                    "step": step,
                                    "url": state.url,
                                    **track,
                                    "quality": "observed",
                                    "source": "media_api",
                                    "decisive": True,
                                }
                            )
                    if media.get("media_control_hints", {}).get("has_transcript_hint"):
                        transcript_indicators.append(
                            {
                                "step": step,
                                "url": state.url,
                                "source": "media_api",
                                "quality": "observed",
                                "decisive": True,
                                "hint_source": "media_control_hints",
                            }
                        )
            if isinstance(media_state, dict):
                for event in media_state.get("media_events", []) or []:
                    if isinstance(event, dict):
                        media_events.append(
                            {
                                "step": step,
                                "url": state.url,
                                **event,
                                "quality": "observed",
                                "source": "runtime_event",
                                "decisive": True,
                            }
                        )

            dom = (state.dom_html or "").lower()
            if "<track" in dom and any(token in dom for token in ["captions", "subtitles"]):
                caption_tracks.append(
                    {
                        "step": step,
                        "url": state.url,
                        "quality": "static_candidate",
                        "source": "dom_token",
                        "decisive": False,
                    }
                )
            if any(token in dom for token in ["transcript", "audio description", "sign language"]):
                transcript_indicators.append(
                    {
                        "step": step,
                        "url": state.url,
                        "quality": "static_candidate",
                        "source": "text_keyword",
                        "decisive": False,
                    }
                )

        return {
            "media_elements": media_elements,
            "caption_tracks": caption_tracks,
            "transcript_indicators": transcript_indicators,
            "media_events": media_events,
            "background_audio_indicators": [
                {**item, "quality": item.get("quality", "observed"), "source": item.get("source", "media_api"), "decisive": True}
                for item in media_elements
                if not bool(item.get("muted", True))
            ],
        }
