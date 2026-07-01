"""Naive browser-use global accessibility audit baseline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from benchmarking.dynamic_baselines import NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE
from benchmarking.dynamic_datasets import DynamicBenchmarkScenario
from dynamic.preprocessors.wcag_mapper import DYNAMIC_WCAG_REGISTRY
from dynamic.runtime_extractors.utils import ensure_dir, write_json
from dynamic.schemas.runtime_trace import RuntimeTrace
from dynamic.schemas.wcag_dynamic import (
    DynamicCriterionResult,
    DynamicStatus,
    DynamicViolation,
    DynamicWCAGReport,
)

NaiveStatus = Literal["pass", "fail", "warning", "not_applicable", "incomplete"]
VALID_DYNAMIC_STATUSES = {"pass", "fail", "warning", "not_applicable", "incomplete"}


class NaiveBrowserUseViolation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="Short issue title.")
    summary: str = Field(description="Specific accessibility impact or behavior observed.")
    severity: str = Field(default="medium", description="Severity such as low, medium, high, or critical.")
    evidence_refs: list[str] = Field(default_factory=list, description="References to browser-use steps or artifacts.")
    step_numbers: list[int] = Field(default_factory=list, description="Browser-use step numbers supporting the issue.")


class NaiveBrowserUseCriterionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str = Field(description="WCAG criterion id, for example 2.4.7.")
    status: NaiveStatus = Field(description="One of pass, fail, warning, not_applicable, incomplete.")
    summary: str = Field(description="Concise criterion-level conclusion from the global audit.")
    missing_evidence: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    step_numbers: list[int] = Field(default_factory=list)
    violations: list[NaiveBrowserUseViolation] = Field(default_factory=list)


class NaiveBrowserUseObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    step_numbers: list[int] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class NaiveBrowserUseAuditOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_summary: str = Field(description="Brief summary of the global accessibility audit.")
    observations: list[NaiveBrowserUseObservation] = Field(default_factory=list)
    assessments: list[NaiveBrowserUseCriterionAssessment] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class NaiveBrowserUseBaselineResult:
    baseline_id: str
    baseline_dir: Path
    report: DynamicWCAGReport
    formal_report_path: Path
    runtime_trace: RuntimeTrace
    runtime_trace_path: Path
    raw_final_result_path: Path | None = None
    structured_output_path: Path | None = None
    invalid_criterion_ids: tuple[str, ...] = ()
    ignored_criterion_ids: tuple[str, ...] = ()
    unsupported_requested_criteria: tuple[str, ...] = ()


def build_naive_browser_use_task(target_url: str, max_steps: int, allowed_criteria: list[str]) -> str:
    """Build the single generic browser-use task for the naive baseline."""

    criteria = _supported_requested_criteria(allowed_criteria)
    criteria_text = ", ".join(criteria) if criteria else "none"
    return (
        f"Open {target_url}. Within {max(1, int(max_steps))} browser-use steps, explore the page freely as an "
        "accessibility auditor. Interact with navigation, keyboard focus, forms, media, dialogs, menus, "
        "hover/focus content, status messages, timing/session behavior, and pointer/drag controls when "
        "discoverable. Report dynamic/interaction-dependent accessibility observations. Return structured JSON "
        "only. Use only these WCAG criterion_id values when you can map an observation confidently: "
        f"{criteria_text}. If a criterion is not directly assessed, omit it rather than guessing."
    )


def normalize_naive_output_to_report(
    output: NaiveBrowserUseAuditOutput | dict[str, Any] | str | None,
    *,
    scenario_id: str,
    requested_criteria: list[str],
    runtime_trace: RuntimeTrace | None = None,
    runtime_trace_path: Path | str | None = None,
    final_output_path: Path | str | None = None,
    history_path: Path | str | None = None,
) -> DynamicWCAGReport:
    """Normalize browser-use's final global-audit output into DynamicWCAGReport."""

    payload = _coerce_output_payload(output)
    allowed_criteria = _supported_requested_criteria(requested_criteria)
    allowed_set = set(allowed_criteria)
    now = datetime.now(timezone.utc)
    assessments = _assessment_payloads(payload)
    by_criterion: dict[str, dict[str, Any]] = {}

    for assessment in assessments:
        criterion_id = str(assessment.get("criterion_id", "")).strip()
        if criterion_id not in allowed_set or criterion_id in by_criterion:
            continue
        by_criterion[criterion_id] = assessment

    results: list[DynamicCriterionResult] = []
    for criterion_id in allowed_criteria:
        criterion = DYNAMIC_WCAG_REGISTRY.get(criterion_id)
        assessment = by_criterion.get(criterion_id)
        if assessment is None:
            results.append(
                DynamicCriterionResult(
                    criterion_id=criterion.id,
                    criterion_name=criterion.name,
                    status=DynamicStatus.incomplete,
                    summary="Naive browser-use global audit did not assess this requested criterion.",
                    missing_evidence=["naive_baseline_not_assessed"],
                    requires_llm=True,
                    model="browser-use-global-audit",
                    generated_at=now,
                )
            )
            continue

        status = str(assessment.get("status", "")).strip().lower()
        missing_evidence = _string_list(assessment.get("missing_evidence"))
        llm_error = None
        if status not in VALID_DYNAMIC_STATUSES:
            status = "incomplete"
            missing_evidence = [*missing_evidence, "naive_baseline_invalid_status"]
            llm_error = "invalid_status"

        summary = str(assessment.get("summary", "")).strip()
        if not summary:
            summary = "Naive browser-use global audit returned no summary for this criterion."
            if "naive_baseline_missing_summary" not in missing_evidence:
                missing_evidence.append("naive_baseline_missing_summary")

        evidence_refs = _evidence_refs_for_assessment(
            assessment,
            runtime_trace=runtime_trace,
            runtime_trace_path=runtime_trace_path,
            final_output_path=final_output_path,
            history_path=history_path,
        )
        violations = _normalize_violations(assessment, status=status, evidence_refs=evidence_refs)
        results.append(
            DynamicCriterionResult(
                criterion_id=criterion.id,
                criterion_name=criterion.name,
                status=DynamicStatus(status),
                summary=summary,
                missing_evidence=missing_evidence,
                violations=violations,
                requires_llm=True,
                llm_error=llm_error,
                model="browser-use-global-audit",
                generated_at=now,
            )
        )

    return DynamicWCAGReport(
        scenario=scenario_id,
        generated_at=now,
        criteria_requested=list(requested_criteria),
        results=results,
        unsupported_criteria=[],
    )


async def run_naive_browser_use_baseline(
    *,
    scenario: DynamicBenchmarkScenario,
    output_dir: Path,
    max_steps: int,
    allowed_criteria: list[str],
    model_name: str | None = None,
    api_key: str | None = None,
    step_logger: Any | None = None,
) -> NaiveBrowserUseBaselineResult:
    """Run the naive browser-use global-audit baseline and export artifacts."""

    from dynamic.browser_use_agent import BrowserUseAccessibilityAgent

    baseline_dir = Path(output_dir)
    task = build_naive_browser_use_task(scenario.target, max_steps, allowed_criteria)
    ensure_dir(baseline_dir.parent)

    agent = BrowserUseAccessibilityAgent(
        output_root=baseline_dir.parent,
        model_name=model_name,
        api_key=api_key,
        enable_evaluator=False,
        enable_formal_wcag=False,
    )
    trace = await agent.run_scenario(
        task,
        scenario_id=baseline_dir.name,
        max_steps=max(1, int(max_steps)),
        reset_output_dir=True,
        step_logger=step_logger,
        output_model_schema=NaiveBrowserUseAuditOutput,
    )
    write_json(
        baseline_dir / "baseline_task.json",
        {
            "baseline_id": NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id,
            "target_url": scenario.target,
            "max_steps": max_steps,
            "allowed_criteria": _supported_requested_criteria(allowed_criteria),
            "task": task,
        },
    )
    write_json(baseline_dir / "output_schema.json", NaiveBrowserUseAuditOutput.model_json_schema())

    runtime_trace_path = baseline_dir / "runtime_trace.json"
    raw_final_result_path = baseline_dir / "browser_use_final_result.txt"
    structured_output_path = baseline_dir / "browser_use_structured_output.json"
    raw_final_result: str | None = None
    structured_payload: dict[str, Any] | None = None
    if raw_final_result_path.exists():
        raw_final_result = raw_final_result_path.read_text(encoding="utf-8")
    if structured_output_path.exists():
        structured_payload = json.loads(structured_output_path.read_text(encoding="utf-8"))

    output: dict[str, Any] | str | None = structured_payload or raw_final_result
    invalid_ids, ignored_ids, unsupported_requested = _normalization_metadata(output, allowed_criteria)
    write_json(
        baseline_dir / "normalization_metadata.json",
        {
            "invalid_criterion_ids": invalid_ids,
            "ignored_criterion_ids": ignored_ids,
            "unsupported_requested_criteria": unsupported_requested,
            "normalization": "invalid or unrequested criterion ids are omitted; unassessed supported requested criteria become incomplete",
        },
    )

    report = normalize_naive_output_to_report(
        output,
        scenario_id=scenario.scenario_id,
        requested_criteria=allowed_criteria,
        runtime_trace=trace,
        runtime_trace_path=runtime_trace_path,
        final_output_path=raw_final_result_path if raw_final_result_path.exists() else None,
        history_path=baseline_dir / "browser_use_history.json",
    )
    formal_report_path = _export_naive_report(baseline_dir, report, output)

    return NaiveBrowserUseBaselineResult(
        baseline_id=NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id,
        baseline_dir=baseline_dir,
        report=report,
        formal_report_path=formal_report_path,
        runtime_trace=trace,
        runtime_trace_path=runtime_trace_path,
        raw_final_result_path=raw_final_result_path if raw_final_result_path.exists() else None,
        structured_output_path=structured_output_path if structured_output_path.exists() else None,
        invalid_criterion_ids=tuple(invalid_ids),
        ignored_criterion_ids=tuple(ignored_ids),
        unsupported_requested_criteria=tuple(unsupported_requested),
    )


def _coerce_output_payload(output: NaiveBrowserUseAuditOutput | dict[str, Any] | str | None) -> dict[str, Any]:
    if output is None:
        return {}
    if isinstance(output, NaiveBrowserUseAuditOutput):
        return output.model_dump(mode="json")
    if isinstance(output, dict):
        return output
    text = str(output).strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _assessment_payloads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("assessments")
    if raw is None:
        raw = payload.get("criteria") or payload.get("criterion_results") or payload.get("results")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _supported_requested_criteria(criteria: list[str]) -> list[str]:
    seen: set[str] = set()
    supported: list[str] = []
    for raw in criteria:
        criterion_id = str(raw).strip()
        if criterion_id and criterion_id not in seen and DYNAMIC_WCAG_REGISTRY.is_supported(criterion_id):
            supported.append(criterion_id)
            seen.add(criterion_id)
    return supported


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    output: list[int] = []
    for item in value:
        try:
            step = int(item)
        except (TypeError, ValueError):
            continue
        if step > 0 and step not in output:
            output.append(step)
    return output


def _evidence_refs_for_assessment(
    assessment: dict[str, Any],
    *,
    runtime_trace: RuntimeTrace | None,
    runtime_trace_path: Path | str | None,
    final_output_path: Path | str | None,
    history_path: Path | str | None,
) -> list[str]:
    refs: list[str] = []
    refs.extend(_string_list(assessment.get("evidence_refs")))
    for step in _int_list(assessment.get("step_numbers")):
        refs.append(f"browser_use_step_{step}")
        if runtime_trace and 0 < step <= len(runtime_trace.steps):
            screenshot = runtime_trace.steps[step - 1].screenshot_path
            if screenshot:
                refs.append(str(screenshot))
    for path in (runtime_trace_path, final_output_path, history_path):
        if path:
            refs.append(str(path))
    deduped: list[str] = []
    for ref in refs:
        clean = str(ref).strip()
        if clean and clean not in deduped:
            deduped.append(clean)
    return deduped


def _normalize_violations(
    assessment: dict[str, Any],
    *,
    status: str,
    evidence_refs: list[str],
) -> list[DynamicViolation]:
    raw_violations = assessment.get("violations")
    violations: list[DynamicViolation] = []
    if isinstance(raw_violations, list):
        for index, item in enumerate(raw_violations, start=1):
            if not isinstance(item, dict):
                continue
            item_refs = _string_list(item.get("evidence_refs"))
            for step in _int_list(item.get("step_numbers")):
                item_refs.append(f"browser_use_step_{step}")
            refs = _dedupe_refs([*(item_refs or []), *evidence_refs]) if item_refs else evidence_refs
            violations.append(
                DynamicViolation(
                    title=str(item.get("title") or f"Naive browser-use observation {index}"),
                    summary=str(item.get("summary") or assessment.get("summary") or "Naive browser-use issue."),
                    severity=str(item.get("severity") or "medium"),
                    evidence_refs=refs,
                )
            )
    if not violations and status in {"fail", "warning"}:
        violations.append(
            DynamicViolation(
                title="Naive browser-use accessibility observation",
                summary=str(assessment.get("summary") or "Naive browser-use issue."),
                severity="medium",
                evidence_refs=evidence_refs,
            )
        )
    return violations


def _dedupe_refs(refs: list[str]) -> list[str]:
    deduped: list[str] = []
    for ref in refs:
        clean = str(ref).strip()
        if clean and clean not in deduped:
            deduped.append(clean)
    return deduped


def _normalization_metadata(output: dict[str, Any] | str | None, allowed_criteria: list[str]) -> tuple[list[str], list[str], list[str]]:
    payload = _coerce_output_payload(output)
    allowed_supported = set(_supported_requested_criteria(allowed_criteria))
    unsupported_requested = [
        str(item).strip()
        for item in allowed_criteria
        if str(item).strip() and not DYNAMIC_WCAG_REGISTRY.is_supported(str(item).strip())
    ]
    invalid_ids: list[str] = []
    ignored_ids: list[str] = []
    for assessment in _assessment_payloads(payload):
        criterion_id = str(assessment.get("criterion_id", "")).strip()
        if not criterion_id:
            continue
        if not DYNAMIC_WCAG_REGISTRY.is_supported(criterion_id):
            invalid_ids.append(criterion_id)
        elif criterion_id not in allowed_supported:
            ignored_ids.append(criterion_id)
    return sorted(set(invalid_ids)), sorted(set(ignored_ids)), sorted(set(unsupported_requested))


def _export_naive_report(baseline_dir: Path, report: DynamicWCAGReport, raw_output: dict[str, Any] | str | None) -> Path:
    criteria_dir = baseline_dir / "criteria"
    ensure_dir(criteria_dir)
    for result in report.results:
        criterion_dir = criteria_dir / result.criterion_id.replace(".", "_")
        ensure_dir(criterion_dir)
        write_json(criterion_dir / "result.json", result.model_dump(mode="json"))
        write_json(
            criterion_dir / "evidence.json",
            {
                "source": "naive-browser-use-global-audit",
                "browser_use_final_output": raw_output,
            },
        )
    report_path = baseline_dir / "formal_dynamic_wcag_results.json"
    write_json(report_path, report.model_dump(mode="json"))
    return report_path
