from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from dynamic.runtime_extractors.utils import parse_json_value, write_json
from dynamic.runtime_extractors.instrumentation_runtime import runtime_script

if TYPE_CHECKING:
    from browser_use.actor.page import Page

MUTATION_SCRIPT = runtime_script(
    r"""
const store = a11yStore();
const observed = (store.events.mutations || []).slice(-200);
const liveObserved = (store.events.liveRegionMutations || []).slice(-100);
const staticLiveRegions = Array.from(document.querySelectorAll('[aria-live], [role="status"], [role="alert"], [role="log"], [role="progressbar"], [role="timer"]'))
  .slice(0, 100)
  .map((el) => ({
    mutation_type: "static_live_region",
    type: "static_live_region",
    target: a11yTarget(el),
    role: el.getAttribute("role"),
    aria_live: el.getAttribute("aria-live"),
    text: a11yTruncate((el.textContent || "").trim(), 300),
    observed: false
  }));
store.events.mutations = [];
store.events.liveRegionMutations = [];
return {
  observed_mutations: observed,
  observed_live_region_mutations: liveObserved,
  static_live_regions: staticLiveRegions,
  all_events: observed.concat(staticLiveRegions),
  event_count: store.counters.mutations || observed.length,
  live_region_event_count: store.counters.liveRegionMutations || liveObserved.length
};
"""
)


class MutationRuntimeExtractor:
    name = "dom_mutations"

    async def extract(self, page: "Page", output_dir: Path, step_number: int) -> dict[str, Any]:
        raw = await page.evaluate(MUTATION_SCRIPT)
        data = parse_json_value(raw) or {}
        if isinstance(data, list):
            payload = data
        else:
            payload = data.get("all_events", []) if isinstance(data, dict) else []
        write_json(output_dir / f"dom_mutations_step_{step_number}.json", data)
        return {"dom_mutations": payload}
