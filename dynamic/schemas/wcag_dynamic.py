from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DynamicStatus(str, Enum):
    pass_status = "pass"
    fail = "fail"
    warning = "warning"
    not_applicable = "not_applicable"
    incomplete = "incomplete"


class DynamicCriterionLevel(str, Enum):
    A = "A"
    AA = "AA"
    AAA = "AAA"


class DynamicCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    level: DynamicCriterionLevel
    version: str = "2.2"
    url: str | None = None
    probe: str
    required_evidence: list[str] = Field(default_factory=list)
    requires_llm: bool = False
    description: str | None = None


class DynamicViolation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    summary: str
    severity: str = "medium"
    evidence_refs: list[str] = Field(default_factory=list)


class FailValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_status: str
    validated_status: str
    reason: str
    evidence_strength: str
    rule: str
    validator: str = "fail_confidence_validator"


class CriterionEvidencePacket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    criterion_family: str
    applicability: str
    applicability_reason: str
    evidence_strength: str
    positive_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    contradictory_evidence: list[str] = Field(default_factory=list)
    step_consistency: str = "unknown"
    compact_runtime_facts: dict[str, Any] = Field(default_factory=dict)


class DynamicCriterionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    criterion_name: str
    status: DynamicStatus
    summary: str
    missing_evidence: list[str] = Field(default_factory=list)
    violations: list[DynamicViolation] = Field(default_factory=list)
    requires_llm: bool = False
    llm_error: str | None = None
    model: str | None = None
    fail_validation: FailValidation | None = None
    decision_level: str | None = None
    evidence_packet: CriterionEvidencePacket | None = None
    generated_at: datetime


class RuntimeEvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: str
    steps_count: int
    probe_evidence: dict[str, dict[str, Any]] = Field(default_factory=dict)
    flat_evidence: dict[str, Any] = Field(default_factory=dict)


class DynamicWCAGReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: str
    generated_at: datetime
    criteria_requested: list[str] = Field(default_factory=list)
    results: list[DynamicCriterionResult] = Field(default_factory=list)
    unsupported_criteria: list[str] = Field(default_factory=list)
