from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from dynamic.runtime_extractors.utils import sha256_text, write_json, write_text

if TYPE_CHECKING:
    from browser_use.actor.page import Page


class DomRuntimeExtractor:
    name = "dom_html"

    def __init__(self, max_chars: int = 50000) -> None:
        self.max_chars = max_chars

    async def extract(self, page: "Page", output_dir: Path, step_number: int) -> dict[str, Any]:
        html = await page.evaluate(
            "() => document.documentElement ? document.documentElement.outerHTML : ''"
        )
        truncated = False
        if isinstance(html, str) and len(html) > self.max_chars:
            html = html[: self.max_chars] + "\n<!-- truncated by runtime extractor -->"
            truncated = True
        dom_path = output_dir / f"dom_snapshot_step_{step_number}.html"
        write_text(dom_path, html)
        checksum = sha256_text(html)
        evidence = {"dom_path": str(dom_path), "checksum": checksum, "truncated": truncated}
        write_json(output_dir / f"dom_evidence_step_{step_number}.json", evidence)
        return {"dom_html": html, "dom_path": str(dom_path), "checksum": checksum, "dom_truncated": truncated}
