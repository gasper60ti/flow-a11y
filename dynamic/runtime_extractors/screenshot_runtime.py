from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dynamic.runtime_extractors.utils import sha256_bytes, write_bytes, write_json

if TYPE_CHECKING:
    from browser_use.actor.page import Page


class ScreenshotRuntimeExtractor:
    name = "screenshot"

    async def extract(self, page: "Page", output_dir: Path, step_number: int) -> dict[str, Any]:
        raw = await page.screenshot(format="png")
        image_bytes = base64.b64decode(raw)
        image_path = output_dir / f"screenshot_step_{step_number}.png"
        write_bytes(image_path, image_bytes)
        checksum = sha256_bytes(image_bytes)
        evidence = {"image_path": str(image_path), "image_sha256": checksum}
        write_json(output_dir / f"screenshot_evidence_step_{step_number}.json", evidence)
        return {"screenshot_path": str(image_path), "image_sha256": checksum}
