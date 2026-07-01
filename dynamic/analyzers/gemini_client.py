from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import requests

from dynamic.runtime_extractors.utils import ensure_dir

GEMINI_GENERATION_CONFIG = {
    "temperature": 0.0,
    "topP": 1.0,
    "maxOutputTokens": 8192,
    "responseMimeType": "application/json",
}


def normalize_model_name(model_name: str) -> str:
    return model_name.replace("models/", "")


class GeminiCriterionModelClient:
    def __init__(
        self,
        api_key: str,
        model_name: str,
        cache_dir: Path,
        max_retries: int = 3,
        base_delay_s: float = 1.0,
        max_delay_s: float = 20.0,
    ) -> None:
        self.api_key = api_key
        self.model_name = normalize_model_name(model_name)
        self.cache_dir = cache_dir
        self.max_retries = max_retries
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        ensure_dir(self.cache_dir)

    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        payload = {
            "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
            "generationConfig": GEMINI_GENERATION_CONFIG,
        }
        text, _ = await asyncio.to_thread(self._call_api, payload)
        return text

    def _cache_key(self, payload: dict[str, Any]) -> str:
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256((self.model_name + serialized).encode("utf-8")).hexdigest()

    def _call_api(self, payload: dict[str, Any]) -> tuple[str, str | None]:
        cache_path = self.cache_dir / f"{self._cache_key(payload)}.json"
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8"), None

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"

        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(url, params={"key": self.api_key}, json=payload, timeout=60)
                resp.raise_for_status()
                text = self._extract_text(resp.json())
                cache_path.write_text(text, encoding="utf-8")
                return text, None
            except Exception as exc:
                if attempt < self.max_retries and self._is_retryable(exc):
                    wait_s = min(self.base_delay_s * (2**attempt), self.max_delay_s)
                    time.sleep(wait_s)
                    continue
                error_text = self._redact(str(exc))
                return json.dumps({"error": error_text}, ensure_ascii=True), error_text

        return json.dumps({"error": "unknown"}, ensure_ascii=True), "unknown"

    def _extract_text(self, data: dict[str, Any]) -> str:
        candidates = data.get("candidates", [])
        if not candidates:
            return json.dumps(data, ensure_ascii=True)
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts)
        return text or json.dumps(data, ensure_ascii=True)

    def _redact(self, text: str) -> str:
        redacted = text.replace(self.api_key, "[REDACTED]")
        return re.sub(r"key=([^&\s]+)", "key=[REDACTED]", redacted)

    def _is_retryable(self, exc: Exception) -> bool:
        if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
            return True
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            return exc.response.status_code in {429, 500, 502, 503, 504}
        return False
