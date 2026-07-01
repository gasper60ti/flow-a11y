from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from dynamic.runtime_extractors.utils import parse_json_value, write_json
from dynamic.runtime_extractors.instrumentation_runtime import runtime_script

if TYPE_CHECKING:
    from browser_use.actor.page import Page

KEYBOARD_SCRIPT = runtime_script(
    """
const store = a11yStore();
const events = (store.events.keyboard || []).slice(-100);
const snapshot = {
  events,
  event_count: store.counters.keyboard || events.length,
  buffered_count: events.length,
  source: "runtime_instrumentation"
};
store.events.keyboard = [];
return snapshot;
"""
)


class KeyboardRuntimeExtractor:
    name = "keyboard"

    async def extract(self, page: "Page", output_dir: Path, step_number: int) -> dict[str, Any]:
        raw = await page.evaluate(KEYBOARD_SCRIPT)
        data = parse_json_value(raw) or {}
        write_json(output_dir / f"keyboard_state_step_{step_number}.json", data)
        return {"keyboard_state": data}
