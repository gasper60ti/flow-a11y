"""Dynamic benchmark baseline declarations.

This module is intentionally metadata-only. The paper-facing dynamic comparison
includes a naive browser-use global audit, a generic matched-trace judge, and a
matched global-evidence ablation. Synthetic fixtures and human labels remain
oracle sources only.
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
    MATCHED_TRACE_GENERIC_JUDGE = "matched_trace_generic_judge"
    MATCHED_GLOBAL_EVIDENCE_ABLATION = "matched_global_evidence_ablation"


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

MATCHED_TRACE_GENERIC_JUDGE_BASELINE = DynamicBaselineSpec(
    baseline_id="baseline-2.0-matched-trace-generic-judge",
    name="Baseline 2.0: matched-trace generic WCAG judge",
    kind=DynamicBaselineKind.MATCHED_TRACE_GENERIC_JUDGE,
    optional=True,
    notes=(
        "Receives the same completed scenario, criterion targets, and runtime trace as FlowA11y, "
        "but judges a bounded generic rendering of the raw trace without FlowA11y probes, evidence gates, "
        "criterion evidence packets, or evidence-reference verification."
    ),
)

MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE = DynamicBaselineSpec(
    baseline_id="baseline-3.0-matched-global-evidence-ablation",
    name="Baseline 3.0: matched global-evidence ablation",
    kind=DynamicBaselineKind.MATCHED_GLOBAL_EVIDENCE_ABLATION,
    optional=True,
    notes=(
        "Receives the same completed scenario, requested criterion ids, runtime trace, Gemini configuration, "
        "prompt templates, evidence-gate decisions, and response verification as FlowA11y. "
        "For each criterion it receives one unfiltered packet containing evidence from every runtime probe, "
        "rather than FlowA11y's criterion-specific evidence packet."
    ),
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
        MATCHED_TRACE_GENERIC_JUDGE_BASELINE,
        MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE,
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
