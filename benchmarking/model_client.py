"""Gemini-compatible model clients with caching, telemetry, and dry-run support."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

GEMINI_GENERATION_CONFIG = {
    "temperature": 0.0,
    "topP": 1.0,
    "maxOutputTokens": 8192,
    "responseMimeType": "application/json",
}


@dataclass
class TokenUsage:
    """Model token accounting per call."""

    prompt_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    estimated: bool


class BenchmarkModelClient:
    """Common model client interface used by benchmark analyzers."""

    def set_context(self, **kwargs: str) -> None:
        raise NotImplementedError

    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        raise NotImplementedError

    def summary(self) -> dict[str, Any]:
        raise NotImplementedError


class CachedGeminiModelClient(BenchmarkModelClient):
    """Gemini API client with on-disk caching and per-call telemetry."""

    def __init__(
        self,
        api_key: str,
        model_name: str,
        cache_dir: Path,
        max_retries: int = 3,
        base_delay_s: float = 1.0,
        max_delay_s: float = 20.0,
        timeout_s: float = 90.0,
    ) -> None:
        self.api_key = api_key
        self.model_name = model_name.replace("models/", "")
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.max_retries = max_retries
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self.timeout_s = timeout_s

        self._context: dict[str, str] = {}
        self.call_records: list[dict[str, Any]] = []

    def set_context(self, **kwargs: str) -> None:
        self._context = {key: value for key, value in kwargs.items() if value is not None}

    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        cache_material = {
            "model": self.model_name,
            "generation_config": GEMINI_GENERATION_CONFIG,
            "prompt": full_prompt,
        }
        cache_key = hashlib.sha256(json.dumps(cache_material, sort_keys=True).encode("utf-8")).hexdigest()
        cache_path = self.cache_dir / f"{cache_key}.json"

        start = time.perf_counter()
        if cache_path.exists():
            cached_text, usage = self._read_cache(cache_path, full_prompt)
            duration_s = time.perf_counter() - start
            self._record_call(
                prompt=full_prompt,
                response_text=cached_text,
                usage=usage,
                duration_s=duration_s,
                cache_hit=True,
                error=None,
            )
            return cached_text

        result = await asyncio.to_thread(self._call_api, full_prompt)
        duration_s = time.perf_counter() - start

        usage = result.get("usage")
        usage_obj = self._normalize_usage(usage, full_prompt, result.get("text", ""))

        cache_payload = {
            "text": result.get("text", ""),
            "usage": {
                "prompt_tokens": usage_obj.prompt_tokens,
                "output_tokens": usage_obj.output_tokens,
                "total_tokens": usage_obj.total_tokens,
                "estimated": usage_obj.estimated,
            },
            "error": result.get("error"),
            "model": self.model_name,
        }
        if result.get("cacheable", True):
            cache_path.write_text(json.dumps(cache_payload, ensure_ascii=True), encoding="utf-8")

        self._record_call(
            prompt=full_prompt,
            response_text=result.get("text", ""),
            usage=usage_obj,
            duration_s=duration_s,
            cache_hit=False,
            error=result.get("error"),
            raw_usage=usage,
        )
        return result.get("text", "")

    def _read_cache(self, cache_path: Path, prompt: str) -> tuple[str, TokenUsage]:
        """Read both legacy (text-only) and structured cache formats."""

        payload_text = cache_path.read_text(encoding="utf-8")
        try:
            payload = json.loads(payload_text)
            if isinstance(payload, dict) and "text" in payload:
                usage_obj = self._normalize_usage(payload.get("usage"), prompt, str(payload.get("text", "")))
                return str(payload.get("text", "")), usage_obj
        except json.JSONDecodeError:
            pass

        usage_obj = self._estimate_usage(prompt, payload_text)
        return payload_text, usage_obj

    def _call_api(self, full_prompt: str) -> dict[str, Any]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
            "generationConfig": GEMINI_GENERATION_CONFIG,
        }

        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    url,
                    params={"key": self.api_key},
                    json=payload,
                    timeout=self.timeout_s,
                )
                response.raise_for_status()
                data = response.json()
                text = _extract_text(data)
                return {
                    "text": text,
                    "usage": data.get("usageMetadata"),
                    "error": None,
                    "cacheable": True,
                }
            except Exception as exc:  # noqa: BLE001
                last_error = self._redact(str(exc))
                if attempt < self.max_retries and self._is_retryable(exc):
                    time.sleep(min(self.base_delay_s * (2**attempt), self.max_delay_s))
                    continue
                return {
                    "text": self._error_response_text(full_prompt, last_error),
                    "usage": None,
                    "error": last_error,
                    "cacheable": False,
                }

        return {
            "text": self._error_response_text(full_prompt, last_error or "unknown_error"),
            "usage": None,
            "error": last_error or "unknown_error",
            "cacheable": False,
        }

    def _error_response_text(self, prompt: str, error: str) -> str:
        if _is_dynamic_wcag_prompt(prompt):
            criterion = _extract_criterion(prompt) or "unknown"
            return json.dumps(
                {
                    "criterion_id": criterion,
                    "status": "incomplete",
                    "summary": "Gemini API call failed before a dynamic WCAG judgment could be produced.",
                    "missing_evidence": ["llm_evaluation"],
                    "violations": [],
                    "error": error,
                },
                ensure_ascii=True,
            )
        return json.dumps(
            {
                "overall_violation": "No",
                "violated_elements_and_reasons": [],
                "error": error,
            },
            ensure_ascii=True,
        )

    def _is_retryable(self, exc: Exception) -> bool:
        if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
            return True
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            return exc.response.status_code in {429, 500, 502, 503, 504}
        return False

    def _redact(self, text: str) -> str:
        if not text:
            return text
        redacted = text.replace(self.api_key, "[REDACTED]")
        return re.sub(r"key=([^&\s]+)", "key=[REDACTED]", redacted)

    def _record_call(
        self,
        prompt: str,
        response_text: str,
        usage: TokenUsage,
        duration_s: float,
        cache_hit: bool,
        error: str | None,
        raw_usage: dict[str, Any] | None = None,
    ) -> None:
        context = dict(self._context)
        criterion = context.get("criterion") or _extract_criterion(prompt)
        record = {
            "run_id": context.get("run_id", ""),
            "dataset": context.get("dataset", ""),
            "case_id": context.get("case_id", ""),
            "criterion": criterion or "",
            "model": self.model_name,
            "cache_hit": cache_hit,
            "error": error,
            "duration_s": round(duration_s, 6),
            "prompt_chars": len(prompt),
            "response_chars": len(response_text),
            "prompt_tokens": usage.prompt_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            "usage_estimated": usage.estimated,
            "raw_usage": raw_usage,
        }
        self.call_records.append(record)

    def _estimate_usage(self, prompt: str, response_text: str) -> TokenUsage:
        # Conservative approximation for Gemini-compatible token counting.
        prompt_tokens = max(1, len(prompt) // 4)
        output_tokens = max(1, len(response_text) // 4)
        return TokenUsage(
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            total_tokens=prompt_tokens + output_tokens,
            estimated=True,
        )

    def _normalize_usage(
        self,
        usage: dict[str, Any] | None,
        prompt: str,
        response_text: str,
    ) -> TokenUsage:
        if not usage:
            return self._estimate_usage(prompt, response_text)

        prompt_tokens = _safe_int(usage.get("promptTokenCount"))
        output_tokens = _safe_int(usage.get("candidatesTokenCount"))
        total_tokens = _safe_int(usage.get("totalTokenCount"))

        if total_tokens is None and prompt_tokens is not None and output_tokens is not None:
            total_tokens = prompt_tokens + output_tokens

        if prompt_tokens is None or output_tokens is None or total_tokens is None:
            estimated = self._estimate_usage(prompt, response_text)
            return TokenUsage(
                prompt_tokens=prompt_tokens if prompt_tokens is not None else estimated.prompt_tokens,
                output_tokens=output_tokens if output_tokens is not None else estimated.output_tokens,
                total_tokens=total_tokens if total_tokens is not None else estimated.total_tokens,
                estimated=True,
            )

        return TokenUsage(
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated=False,
        )

    def summary(self) -> dict[str, Any]:
        total_calls = len(self.call_records)
        cache_hits = sum(1 for record in self.call_records if record.get("cache_hit"))
        errors = sum(1 for record in self.call_records if record.get("error"))
        total_prompt = sum((record.get("prompt_tokens") or 0) for record in self.call_records)
        total_output = sum((record.get("output_tokens") or 0) for record in self.call_records)
        total_tokens = sum((record.get("total_tokens") or 0) for record in self.call_records)
        total_time = sum(float(record.get("duration_s") or 0.0) for record in self.call_records)
        estimated_usage_calls = sum(1 for record in self.call_records if record.get("usage_estimated"))

        return {
            "model": self.model_name,
            "total_calls": total_calls,
            "api_calls": total_calls - cache_hits,
            "cache_hits": cache_hits,
            "errors": errors,
            "prompt_tokens": total_prompt,
            "output_tokens": total_output,
            "total_tokens": total_tokens,
            "estimated_usage_calls": estimated_usage_calls,
            "total_model_time_s": round(total_time, 6),
        }


class DeterministicDryRunModelClient(BenchmarkModelClient):
    """Deterministic fake model client for smoke tests without API usage."""

    def __init__(self, model_name: str = "dry-run-deterministic") -> None:
        self.model_name = model_name
        self._context: dict[str, str] = {}
        self.call_records: list[dict[str, Any]] = []

    def set_context(self, **kwargs: str) -> None:
        self._context = {key: value for key, value in kwargs.items() if value is not None}

    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        start = time.perf_counter()
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        criterion = self._context.get("criterion") or _extract_criterion(full_prompt) or "unknown"

        key = f"{criterion}:{full_prompt}".encode("utf-8")
        digest = hashlib.sha256(key).hexdigest()
        if _is_dynamic_wcag_prompt(full_prompt):
            payload = _dynamic_dry_run_payload(criterion, digest)
        else:
            hit = int(digest[:2], 16) % 4 == 0

            if hit:
                payload = {
                    "overall_violation": "Yes",
                    "violated_elements_and_reasons": [
                        {
                            "element": f"<div data-dry-run='sc-{criterion}'></div>",
                            "reason": f"Deterministic dry-run violation for SC {criterion}.",
                            "recommendation": "Validate with real model for authoritative results.",
                        }
                    ],
                }
            else:
                payload = {"overall_violation": "No", "violated_elements_and_reasons": []}

        text = json.dumps(payload, ensure_ascii=True)
        duration_s = time.perf_counter() - start
        usage = _estimate_simple_usage(full_prompt, text)
        self.call_records.append(
            {
                "run_id": self._context.get("run_id", ""),
                "dataset": self._context.get("dataset", ""),
                "case_id": self._context.get("case_id", ""),
                "criterion": criterion,
                "model": self.model_name,
                "cache_hit": False,
                "error": None,
                "duration_s": round(duration_s, 6),
                "prompt_chars": len(full_prompt),
                "response_chars": len(text),
                "prompt_tokens": usage.prompt_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                "usage_estimated": True,
                "raw_usage": None,
            }
        )
        return text

    def summary(self) -> dict[str, Any]:
        total_calls = len(self.call_records)
        errors = sum(1 for record in self.call_records if record.get("error"))
        return {
            "model": self.model_name,
            "total_calls": total_calls,
            "api_calls": 0,
            "cache_hits": 0,
            "errors": errors,
            "prompt_tokens": sum((record.get("prompt_tokens") or 0) for record in self.call_records),
            "output_tokens": sum((record.get("output_tokens") or 0) for record in self.call_records),
            "total_tokens": sum((record.get("total_tokens") or 0) for record in self.call_records),
            "estimated_usage_calls": total_calls,
            "total_model_time_s": round(sum(float(record.get("duration_s") or 0.0) for record in self.call_records), 6),
        }


def _extract_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates", [])
    if not candidates:
        return json.dumps(data, ensure_ascii=True)
    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(str(part.get("text", "")) for part in parts)
    return text or json.dumps(data, ensure_ascii=True)


def _extract_criterion(text: str) -> str | None:
    for pattern in (
        r"SC\s*([0-9]+\.[0-9]+\.[0-9]+)",
        r"Criterion:\s*([0-9]+\.[0-9]+\.[0-9]+)",
        r"criterion\s+([0-9]+\.[0-9]+\.[0-9]+)",
        r"criterion id:\s*([0-9]+\.[0-9]+\.[0-9]+)",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _is_dynamic_wcag_prompt(text: str) -> bool:
    lower_text = text.lower()
    return (
        "you are verifying wcag success criterion" in lower_text
        and '"criterion_id"' in text
        and "evidence json:" in lower_text
    )


def _dynamic_dry_run_payload(criterion: str, digest: str) -> dict[str, Any]:
    statuses = ["pass", "fail", "warning", "not_applicable", "incomplete"]
    status = statuses[int(digest[:2], 16) % len(statuses)]
    payload: dict[str, Any] = {
        "criterion_id": criterion,
        "status": status,
        "summary": f"Deterministic dry-run dynamic WCAG judgment for SC {criterion}.",
        "missing_evidence": [],
        "violations": [],
    }
    if status == "incomplete":
        payload["missing_evidence"] = ["dry_run_evidence"]
    if status in {"fail", "warning"}:
        payload["violations"] = [
            {
                "title": "Dry-run dynamic WCAG finding",
                "summary": f"Deterministic dry-run {status} outcome for SC {criterion}.",
                "severity": "low",
                "evidence_refs": ["dry_run"],
            }
        ]
    return payload


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _estimate_simple_usage(prompt: str, text: str) -> TokenUsage:
    prompt_tokens = max(1, len(prompt) // 4)
    output_tokens = max(1, len(text) // 4)
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        total_tokens=prompt_tokens + output_tokens,
        estimated=True,
    )
