from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dynamic.schemas.runtime_state import RuntimeState


class RuntimeTraceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_time: datetime
    end_time: datetime | None = None
    duration_seconds: float | None = None
    browser_use_history: list[dict[str, Any]] = Field(default_factory=list)
    artifacts_dir: str | None = None
    scenario_attempts: int = 1
    retry_reasons: list[str] = Field(default_factory=list)


class RuntimeTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: str
    metadata: RuntimeTraceMetadata
    steps: list[RuntimeState] = Field(default_factory=list)
