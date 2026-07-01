from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from dynamic.runtime_extractors.utils import parse_json_value, write_json

if TYPE_CHECKING:
    from browser_use.actor.page import Page

CONSOLE_SCRIPT = """
() => {
  if (!window.__a11yConsole) {
    window.__a11yConsole = { logs: [] };
    const levels = ["log", "info", "warn", "error"];
    for (const level of levels) {
      const original = console[level];
      console[level] = function(...args) {
        try {
          window.__a11yConsole.logs.push({
            level,
            text: args.map((arg) => String(arg)).join(" "),
            timestamp: new Date().toISOString(),
            source: location.href
          });
        } catch (e) {
        }
        return original.apply(console, args);
      };
    }
  }
  const logs = window.__a11yConsole.logs || [];
  window.__a11yConsole.logs = [];
  return logs;
}
"""


class ConsoleRuntimeExtractor:
    name = "console"

    async def extract(self, page: "Page", output_dir: Path, step_number: int) -> dict[str, Any]:
        raw = await page.evaluate(CONSOLE_SCRIPT)
        data = parse_json_value(raw) or []
        write_json(output_dir / f"console_logs_step_{step_number}.json", data)
        return {"console_logs": data}
