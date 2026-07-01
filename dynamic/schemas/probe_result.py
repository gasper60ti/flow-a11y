from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluator: str
    model: str | None = None
    captured_at: datetime
    request_payload: dict[str, Any] | None = None
    response_text: str | None = None
    error: str | None = None
    request_path: str | None = None
    response_path: str | None = None
