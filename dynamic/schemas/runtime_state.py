from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dynamic.schemas.probe_result import ProbeResult


class RuntimeState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    url: str
    title: str | None = None
    current_action: dict[str, Any] | None = None
    dom_html: str | None = None
    accessibility_tree: dict[str, Any] | None = None
    focused_element: dict[str, Any] | None = None
    keyboard_state: dict[str, Any] | None = None
    console_logs: list[dict[str, Any]] = Field(default_factory=list)
    screenshot_path: str | None = None
    dom_mutations: list[dict[str, Any]] = Field(default_factory=list)
    timing_info: dict[str, Any] | None = None
    media_state: dict[str, Any] | None = None
    interaction_metadata: dict[str, Any] | None = None
    evaluator_result: ProbeResult | None = None
