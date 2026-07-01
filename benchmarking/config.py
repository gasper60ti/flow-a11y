"""Benchmark harness configuration and paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BENCHMARK_ROOT = Path(__file__).resolve().parent
APP_ROOT = BENCHMARK_ROOT.parent

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_GEMINI_MAX_RETRIES = 3
DEFAULT_GEMINI_RETRY_BASE_S = 1.0
DEFAULT_GEMINI_RETRY_MAX_S = 20.0


@dataclass(frozen=True)
class ModelPricing:
    """Per-million token pricing model."""

    input_per_million: float | None
    output_per_million: float | None
    currency: str = "USD"


# Intentionally conservative defaults: unknown pricing is encoded as None.
MODEL_PRICING: dict[str, ModelPricing] = {
    "gemini-2.5-flash": ModelPricing(input_per_million=None, output_per_million=None),
    "gemini-2.5-pro": ModelPricing(input_per_million=None, output_per_million=None),
    "gemini-1.5-flash": ModelPricing(input_per_million=None, output_per_million=None),
    "gemini-1.5-pro": ModelPricing(input_per_million=None, output_per_million=None),
}


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from a .env-like file into os.environ if absent."""

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and os.environ.get(key) is None:
            os.environ[key] = value


def load_local_env() -> None:
    """Load `a11y-agent/.env` for local runs."""

    load_env_file(APP_ROOT / ".env")


def resolve_model_name(explicit: str | None = None) -> str:
    """Resolve GEMINI model name, preserving repo default behavior."""

    model_name = explicit or os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    return model_name.replace("models/", "")


def resolve_model_pricing(model_name: str) -> ModelPricing:
    """Resolve pricing by env override first, then configured model map."""

    env_in = os.environ.get("BENCHMARK_INPUT_COST_PER_MTOKEN")
    env_out = os.environ.get("BENCHMARK_OUTPUT_COST_PER_MTOKEN")
    if env_in is not None or env_out is not None:
        try:
            input_cost = float(env_in) if env_in is not None else None
            output_cost = float(env_out) if env_out is not None else None
            return ModelPricing(input_per_million=input_cost, output_per_million=output_cost)
        except ValueError:
            pass

    key = model_name.replace("models/", "")
    return MODEL_PRICING.get(key, ModelPricing(input_per_million=None, output_per_million=None))
