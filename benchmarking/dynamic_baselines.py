"""Dynamic benchmark baseline declarations.

This module is intentionally metadata-only. The paper-facing dynamic comparison
uses FLOW-A11y's dynamic judge against a naive browser-use global audit
ablation. Synthetic fixtures and human labels remain oracle sources only.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import Enum


class DynamicBaselineKind(str, Enum):
    """Supported dynamic benchmark baseline families."""

    SYNTHETIC_FIXTURE = "synthetic_fixture"
    HUMAN_ORACLE = "human_oracle"
    NON_LLM_DYNAMIC_PROBE = "non_llm_dynamic_probe"
    RULES_DYNAMIC_WCAG = "rules_dynamic_wcag"
    GEMINI_DYNAMIC_WCAG = "gemini_dynamic_wcag"
    HYBRID_DYNAMIC_WCAG = "hybrid_dynamic_wcag"
    NAIVE_BROWSER_USE_GLOBAL_AUDIT = "naive_browser_use_global_audit"


class BaselineAvailabilityStatus(str, Enum):
    """Availability state for optional baselines."""

    AVAILABLE = "available"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class DynamicBaselineSpec:
    """Metadata for one baseline option."""

    baseline_id: str
    name: str
    kind: DynamicBaselineKind
    optional: bool = True
    command: str | None = None
    notes: str = ""


@dataclass(frozen=True)
class DynamicBaselineAvailability:
    """Resolved availability for one baseline option."""

    baseline_id: str
    kind: DynamicBaselineKind
    status: BaselineAvailabilityStatus
    available: bool
    reason: str
    command: str | None = None


SYNTHETIC_FIXTURE_BASELINE = DynamicBaselineSpec(
    baseline_id="synthetic-fixture",
    name="Synthetic dynamic fixture oracle",
    kind=DynamicBaselineKind.SYNTHETIC_FIXTURE,
    optional=False,
    notes="Primary deterministic baseline with authored pass/fail ground truth for dynamic WCAG scenarios.",
)

HUMAN_ORACLE_BASELINE = DynamicBaselineSpec(
    baseline_id="human-oracle",
    name="Human-labelled dynamic scenario oracle",
    kind=DynamicBaselineKind.HUMAN_ORACLE,
    optional=True,
    notes="Validation baseline for reviewer-labelled real scenarios.",
)

NON_LLM_DYNAMIC_PROBE_BASELINE = DynamicBaselineSpec(
    baseline_id="non-llm-dynamic-probes",
    name="Dynamic probes without LLM verification",
    kind=DynamicBaselineKind.NON_LLM_DYNAMIC_PROBE,
    optional=False,
    notes="Ablation baseline for deterministic runtime probe rules over the same runtime traces.",
)

RULES_DYNAMIC_WCAG_BASELINE = DynamicBaselineSpec(
    baseline_id="rules-dynamic-wcag",
    name="FLOW-A11y dynamic rules judge",
    kind=DynamicBaselineKind.RULES_DYNAMIC_WCAG,
    optional=True,
    notes="Primary dynamic analyzer using deterministic runtime evidence rules.",
)

GEMINI_DYNAMIC_WCAG_BASELINE = DynamicBaselineSpec(
    baseline_id="gemini-dynamic-wcag",
    name="Gemini dynamic WCAG judge",
    kind=DynamicBaselineKind.GEMINI_DYNAMIC_WCAG,
    optional=True,
    notes="Formal dynamic WCAG judge using criterion-specific Gemini prompts.",
)

NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE = DynamicBaselineSpec(
    baseline_id="naive-browser-use-global-audit",
    name="Naive browser-use global accessibility audit",
    kind=DynamicBaselineKind.NAIVE_BROWSER_USE_GLOBAL_AUDIT,
    optional=True,
    notes="Single generic browser-use accessibility task, no scenario script, no per-criterion prompts.",
)

HYBRID_DYNAMIC_WCAG_BASELINE = DynamicBaselineSpec(
    baseline_id="hybrid-dynamic-wcag",
    name="Hybrid dynamic WCAG judge",
    kind=DynamicBaselineKind.HYBRID_DYNAMIC_WCAG,
    optional=True,
    notes="Uses Gemini prompt judgments where available and deterministic dynamic rules as fallback.",
)

def get_baseline_specs() -> tuple[DynamicBaselineSpec, ...]:
    """Return paper-facing dynamic baselines and oracle sources."""

    return (
        SYNTHETIC_FIXTURE_BASELINE,
        HUMAN_ORACLE_BASELINE,
        RULES_DYNAMIC_WCAG_BASELINE,
        GEMINI_DYNAMIC_WCAG_BASELINE,
        HYBRID_DYNAMIC_WCAG_BASELINE,
        NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE,
    )

def baseline_availability(spec: DynamicBaselineSpec) -> DynamicBaselineAvailability:
    """Resolve baseline availability without running the baseline."""

    if spec.command is None:
        if spec.optional:
            return DynamicBaselineAvailability(
                baseline_id=spec.baseline_id,
                kind=spec.kind,
                status=BaselineAvailabilityStatus.SKIPPED,
                available=False,
                reason="optional baseline has no executable command configured",
                command=None,
            )
        return DynamicBaselineAvailability(
            baseline_id=spec.baseline_id,
            kind=spec.kind,
            status=BaselineAvailabilityStatus.AVAILABLE,
            available=True,
            reason="metadata baseline does not require an executable command",
            command=None,
        )

    resolved = shutil.which(spec.command)
    if resolved:
        return DynamicBaselineAvailability(
            baseline_id=spec.baseline_id,
            kind=spec.kind,
            status=BaselineAvailabilityStatus.AVAILABLE,
            available=True,
            reason=f"found executable: {resolved}",
            command=spec.command,
        )

    return DynamicBaselineAvailability(
        baseline_id=spec.baseline_id,
        kind=spec.kind,
        status=BaselineAvailabilityStatus.SKIPPED,
        available=False,
        reason=f"optional executable not found: {spec.command}",
        command=spec.command,
    )
