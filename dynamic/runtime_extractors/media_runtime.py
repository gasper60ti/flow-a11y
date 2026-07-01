from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from dynamic.runtime_extractors.utils import parse_json_value, write_json
from dynamic.runtime_extractors.instrumentation_runtime import runtime_script

if TYPE_CHECKING:
    from browser_use.actor.page import Page

MEDIA_SCRIPT = runtime_script(
    r"""
const store = a11yStore();
function trackPayload(trackEl) {
  return {
    kind: trackEl.kind || null,
    label: trackEl.label || null,
    srclang: trackEl.srclang || null,
    src: trackEl.src || null,
    ready_state: trackEl.readyState
  };
}
function textTrackPayload(track) {
  return {
    kind: track.kind || null,
    label: track.label || null,
    language: track.language || null,
    mode: track.mode || null,
    cues_count: track.cues ? track.cues.length : null,
    active_cues_count: track.activeCues ? track.activeCues.length : null
  };
}
function mediaKey(el, index) {
  if (!el.__a11yMediaKey) {
    el.__a11yMediaKey = `${el.tagName || "media"}:${index}:${Math.random().toString(36).slice(2)}`;
  }
  return el.__a11yMediaKey;
}
function rectPayload(el) {
  const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
  return rect ? {
    x: Math.round(rect.x),
    y: Math.round(rect.y),
    width: Math.round(rect.width),
    height: Math.round(rect.height)
  } : null;
}
const media = Array.from(document.querySelectorAll("video, audio")).slice(0, 50).map((el, index) => {
  const key = mediaKey(el, index);
  const previousTime = store.mediaPreviousTimes[key];
  const currentTime = Number.isFinite(el.currentTime) ? el.currentTime : null;
  const delta = typeof previousTime === "number" && currentTime !== null ? currentTime - previousTime : null;
  if (currentTime !== null) {
    store.mediaPreviousTimes[key] = currentTime;
  }
  const tracks = Array.from(el.querySelectorAll ? el.querySelectorAll("track") : []).slice(0, 20).map(trackPayload);
  const textTracks = Array.from(el.textTracks || []).slice(0, 20).map(textTrackPayload);
  const captionTracks = tracks.filter((track) => ["captions", "subtitles"].includes(String(track.kind || "").toLowerCase()));
  const textCaptionTracks = textTracks.filter((track) => ["captions", "subtitles"].includes(String(track.kind || "").toLowerCase()));
  const rect = rectPayload(el);
  const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
  const visible = Boolean(rect && rect.width > 0 && rect.height > 0 && (!style || (style.visibility !== "hidden" && style.display !== "none")));
  const shadowText = el.shadowRoot ? a11yTruncate((el.shadowRoot.textContent || "").trim(), 500) : "";
  return {
    media_key: key,
    tag: el.tagName ? el.tagName.toLowerCase() : null,
    current_time: currentTime,
    current_time_delta: delta,
    duration: Number.isFinite(el.duration) ? el.duration : null,
    paused: el.paused,
    muted: el.muted,
    volume: el.volume,
    audible: Boolean(!el.muted && el.volume > 0 && !el.paused),
    autoplay: Boolean(el.autoplay),
    loop: Boolean(el.loop),
    preload: el.preload || null,
    aria_label: el.getAttribute("aria-label"),
    title: el.getAttribute("title"),
    playback_rate: el.playbackRate,
    ready_state: el.readyState,
    network_state: el.networkState,
    src: el.currentSrc || el.src || null,
    tracks,
    text_tracks: textTracks,
    caption_tracks: captionTracks,
    captions_or_subtitles: captionTracks.concat(textCaptionTracks),
    dimensions: {
      video_width: el.videoWidth || null,
      video_height: el.videoHeight || null,
      client_width: el.clientWidth || null,
      client_height: el.clientHeight || null,
      rect
    },
    visible,
    controls: Boolean(el.controls),
    media_control_hints: {
      shadow_text: shadowText,
      has_caption_control_hint: /caption|subtitle|cc/i.test(shadowText),
      has_transcript_hint: /transcript|audio description|sign language/i.test(shadowText)
    }
  };
});
const events = (store.events.media || []).slice(-100);
store.events.media = [];
return {
  media,
  count: media.length,
  media_events: events,
  event_count: store.counters.media || events.length
};
"""
)


class MediaRuntimeExtractor:
    name = "media"

    async def extract(self, page: "Page", output_dir: Path, step_number: int) -> dict[str, Any]:
        raw = await page.evaluate(MEDIA_SCRIPT)
        data = parse_json_value(raw) or {"media": [], "count": 0}
        write_json(output_dir / f"media_state_step_{step_number}.json", data)
        return {"media_state": data}
