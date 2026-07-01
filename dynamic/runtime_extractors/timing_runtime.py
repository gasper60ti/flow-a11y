from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from dynamic.runtime_extractors.utils import parse_json_value, write_json

if TYPE_CHECKING:
    from browser_use.actor.page import Page

TIMING_SCRIPT = """
() => {
  const timing = performance.timing && performance.timing.toJSON ? performance.timing.toJSON() : null;
  const navigation = performance.getEntriesByType("navigation");
  const navigationEntry = navigation && navigation.length ? navigation[0].toJSON() : null;
  return {
    timing,
    navigation: navigationEntry,
    now: performance.now(),
    time_origin: performance.timeOrigin
  };
}
"""


class TimingRuntimeExtractor:
    name = "timing"

    async def extract(self, page: "Page", output_dir: Path, step_number: int) -> dict[str, Any]:
        raw = await page.evaluate(TIMING_SCRIPT)
        data = parse_json_value(raw) or {}
        write_json(output_dir / f"timing_info_step_{step_number}.json", data)
        return {"timing_info": data}
