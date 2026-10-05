"""Baseline 2.0: generic WCAG judging over the runtime trace shared with FlowA11y.

The baseline does not execute a second browser session.  It consumes the exact trace
captured for the primary analyzer, the same scenario description, and the same
criterion ids.  Its prompt deliberately omits FlowA11y's probes, evidence gates,
criterion evidence packets, and response verifier.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from dynamic.preprocessors.wcag_mapper import DYNAMIC_WCAG_REGISTRY, get_criterion
from dynamic.runtime_extractors.utils import ensure_dir, write_json
from dynamic.schemas.runtime_trace import RuntimeTrace
from dynamic.schemas.wcag_dynamic import (
    DynamicCriterion,
    DynamicCriterionResult,
    DynamicStatus,
    DynamicViolation,
    DynamicWCAGReport,
)

from benchmarking.dynamic_baselines import MATCHED_TRACE_GENERIC_JUDGE_BASELINE
from benchmarking.dynamic_datasets import DynamicBenchmarkScenario


class ModelClient(Protocol):
    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        ...

    def set_context(self, **kwargs: str) -> None:
        ...


TRACE_VIEW_VERSION = "baseline-2.0/raw-trace-view/v1"
TRACE_VIEW_BUDGET_CHARS = 96_000


@dataclass(frozen=True)
class MatchedTraceBaselineResult:
    baseline_id: str
    baseline_dir: Path
    report: DynamicWCAGReport
    formal_report_path: Path
    runtime_trace_path: Path


async def run_matched_trace_generic_judge(
    *,
    scenario: DynamicBenchmarkScenario,
    runtime_trace: RuntimeTrace,
    runtime_trace_path: Path,
    output_dir: Path,
    requested_criteria: list[str],
    model_client: ModelClient | None,
    run_id: str = "run_1",
) -> MatchedTraceBaselineResult:
    """Judge every requested criterion against the trace already captured upstream."""

    baseline_dir = Path(output_dir)
    ensure_dir(baseline_dir)
    criteria = _supported_criteria(requested_criteria)
    trace_view, trace_contract = build_generic_trace_view(runtime_trace)
    write_json(baseline_dir / "input_contract.json", {
        "baseline_id": MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id,
        "input_contract": {
            "scenario_task": scenario.task_description,
            "criterion_ids": criteria,
            "runtime_trace_path": str(runtime_trace_path),
            "runtime_trace_sha256": trace_contract["runtime_trace_sha256"],
            "trace_view_version": TRACE_VIEW_VERSION,
            "trace_view_budget_chars": TRACE_VIEW_BUDGET_CHARS,
        },
        "deliberately_absent": [
            "criterion-specific probes",
            "criterion evidence packets",
            "evidence gate",
            "evidence-quality scorer",
            "evidence-reference verifier",
            "fail-confidence validator",
        ],
    })
    write_json(baseline_dir / "raw_trace_view.json", trace_view)
    write_json(baseline_dir / "raw_trace_view_metadata.json", trace_contract)

    results: list[DynamicCriterionResult] = []
    for criterion_id in criteria:
        criterion = get_criterion(criterion_id)
        prompt = build_matched_trace_prompt(
            scenario_task=scenario.task_description,
            criterion=criterion,
            trace_view=trace_view,
        )
        if model_client is None:
            result = _incomplete_result(criterion, "No model client is configured for Baseline 2.0.", "missing_model_client")
            response_text = ""
            payload: dict[str, Any] = {}
        else:
            model_client.set_context(
                run_id=run_id,
                dataset="D_dyn_real",
                case_id=scenario.scenario_id,
                criterion=criterion.id,
            )
            response_text = await model_client.complete(prompt)
            payload = _coerce_payload(response_text)
            result = _result_from_payload(criterion, payload, model_name=getattr(model_client, "model_name", None))

        criterion_dir = baseline_dir / "criteria" / criterion.id.replace(".", "_")
        ensure_dir(criterion_dir)
        (criterion_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        write_json(criterion_dir / "llm_response.json", {"raw_response_text": response_text, "parsed_response": payload})
        write_json(criterion_dir / "result.json", result.model_dump(mode="json"))
        results.append(result)

    report = DynamicWCAGReport(
        scenario=scenario.scenario_id,
        generated_at=datetime.now(timezone.utc),
        criteria_requested=list(requested_criteria),
        results=results,
        unsupported_criteria=[criterion for criterion in requested_criteria if criterion not in set(criteria)],
    )
    formal_report_path = baseline_dir / "formal_dynamic_wcag_results.json"
    write_json(formal_report_path, report.model_dump(mode="json"))
    return MatchedTraceBaselineResult(
        baseline_id=MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id,
        baseline_dir=baseline_dir,
        report=report,
        formal_report_path=formal_report_path,
        runtime_trace_path=Path(runtime_trace_path),
    )


def build_matched_trace_prompt(*, scenario_task: str, criterion: DynamicCriterion, trace_view: dict[str, Any]) -> str:
    """Build a generic, per-criterion prompt using the matched raw trace view."""

    trace_text = json.dumps(trace_view, ensure_ascii=True, sort_keys=True)
    return (
        f"You are verifying WCAG success criterion {criterion.id} as a generic evaluator. Assess exactly one "
        "WCAG success criterion from an already completed "
        "browser interaction. You receive the same scenario task and runtime trace that another system received. "
        "Do not infer events that are absent from the trace.\n\n"
        "Scenario task:\n"
        f"{scenario_task}\n\n"
        "Evaluation target:\n"
        f"- criterion_id: {criterion.id}\n"
        f"- criterion_name: {criterion.name}\n"
        f"- WCAG level: {criterion.level.value}\n\n"
        "Classify this target as pass, fail, warning, not_applicable, or incomplete. Use incomplete when the "
        "trace does not provide enough usable evidence; use not_applicable only when the relevant feature is not "
        "observed. A fail must be directly supported by an observed behavior. A warning is a plausible risk that "
        "the trace does not directly prove. For fail or warning, cite one or more JSON paths from the supplied "
        "trace view in violations[].evidence_refs.\n\n"
        "Return strict JSON only with this schema:\n"
        "{\n"
        f'  "criterion_id": "{criterion.id}",\n'
        '  "status": "pass|fail|warning|not_applicable|incomplete",\n'
        '  "summary": "one concise evidence-grounded sentence",\n'
        '  "missing_evidence": ["trace field or observation that is unavailable"],\n'
        '  "violations": [{"title": "short title", "summary": "reason", "severity": "low|medium|high|critical", "evidence_refs": ["runtime_trace.steps[0].focused_element"]}]\n'
        "}\n"
        "Use an empty violations array for pass, not_applicable, or incomplete.\n\n"
        "Evidence JSON:\n"
        f"{trace_text}"
    )


def build_generic_trace_view(runtime_trace: RuntimeTrace) -> tuple[dict[str, Any], dict[str, Any]]:
    """Produce a documented, criterion-agnostic rendering of every captured trace step.

    Full DOM and accessibility-tree snapshots can be megabytes long.  The baseline
    receives a deterministic, bounded excerpt of each raw field so a single generic
    prompt remains executable.  This is not a probe or criterion-specific projection.
    """

    raw = runtime_trace.model_dump(mode="json")
    raw_text = json.dumps(raw, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
    per_step_budget = max(4_000, TRACE_VIEW_BUDGET_CHARS // max(1, len(steps)))
    rendered_steps = [_render_step(step, per_step_budget) for step in steps if isinstance(step, dict)]
    view = {
        "trace_view_version": TRACE_VIEW_VERSION,
        "scenario": raw.get("scenario", ""),
        "metadata": _bounded_value(raw.get("metadata", {}), 3_000),
        "steps": rendered_steps,
    }
    view_text = json.dumps(view, ensure_ascii=True, sort_keys=True)
    metadata = {
        "runtime_trace_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        "runtime_trace_characters": len(raw_text),
        "trace_view_characters": len(view_text),
        "trace_view_budget_characters": TRACE_VIEW_BUDGET_CHARS,
        "steps": len(rendered_steps),
        "per_step_budget_characters": per_step_budget,
        "field_policy": "All steps and raw field families are retained; large values are deterministically excerpted.",
    }
    return view, metadata


def _render_step(step: dict[str, Any], budget: int) -> dict[str, Any]:
    # The allocation is stable across criteria and does not encode WCAG knowledge.
    base_limits = {
        "timestamp": 100,
        "url": 350,
        "title": 200,
        "current_action": 600,
        "focused_element": 550,
        "keyboard_state": 550,
        "console_logs": 350,
        "dom_mutations": 600,
        "timing_info": 300,
        "media_state": 350,
        "interaction_metadata": 600,
        "screenshot_path": 250,
    }
    raw_field_limit = max(350, int(budget * 0.15))
    base_total = sum(base_limits.values())
    scale = min(1.0, max(0.1, (budget - (2 * raw_field_limit)) / base_total))
    limits = {field: max(50, int(limit * scale)) for field, limit in base_limits.items()}
    limits["dom_html"] = raw_field_limit
    limits["accessibility_tree"] = raw_field_limit
    return {field: _bounded_value(step.get(field), limit) for field, limit in limits.items() if field in step}


def _bounded_value(value: Any, limit: int) -> Any:
    serialized = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    if len(serialized) <= limit:
        return value
    return {
        "truncated": True,
        "original_characters": len(serialized),
        "excerpt": serialized[: max(1, limit - 90)],
    }


def _supported_criteria(requested_criteria: list[str]) -> list[str]:
    result: list[str] = []
    for raw in requested_criteria:
        criterion_id = str(raw).strip()
        if criterion_id and criterion_id not in result and DYNAMIC_WCAG_REGISTRY.is_supported(criterion_id):
            result.append(criterion_id)
    return result


def _coerce_payload(response_text: str) -> dict[str, Any]:
    text = str(response_text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return payload if isinstance(payload, dict) else {}


def _result_from_payload(criterion: DynamicCriterion, payload: dict[str, Any], model_name: str | None) -> DynamicCriterionResult:
    if payload.get("error") and not payload.get("criterion_id"):
        return _incomplete_result(criterion, "Model API error prevented a Baseline 2.0 judgment.", str(payload.get("error")))
    if str(payload.get("criterion_id", "")).strip() != criterion.id:
        return _incomplete_result(criterion, "Invalid JSON from Baseline 2.0 or criterion id mismatch.", "invalid_json")
    try:
        status = DynamicStatus(str(payload.get("status", "")).strip())
    except ValueError:
        return _incomplete_result(criterion, "Baseline 2.0 returned an invalid status.", "invalid_status")

    violations: list[DynamicViolation] = []
    raw_violations = payload.get("violations", [])
    if status in {DynamicStatus.fail, DynamicStatus.warning}:
        if not isinstance(raw_violations, list) or not raw_violations:
            return _incomplete_result(criterion, "Baseline 2.0 returned a fail or warning without a violation.", "missing_violations")
        try:
            violations = [DynamicViolation.model_validate(item) for item in raw_violations if isinstance(item, dict)]
        except Exception:
            return _incomplete_result(criterion, "Baseline 2.0 returned an invalid violation schema.", "invalid_violation")
        if not violations:
            return _incomplete_result(criterion, "Baseline 2.0 returned no usable violations.", "missing_violations")

    missing = payload.get("missing_evidence", [])
    if not isinstance(missing, list):
        missing = []
    summary = str(payload.get("summary", "")).strip() or "Baseline 2.0 returned no summary."
    return DynamicCriterionResult(
        criterion_id=criterion.id,
        criterion_name=criterion.name,
        status=status,
        summary=summary,
        missing_evidence=[str(item) for item in missing if str(item).strip()],
        violations=violations,
        requires_llm=True,
        model=model_name,
        generated_at=datetime.now(timezone.utc),
    )


def _incomplete_result(criterion: DynamicCriterion, summary: str, error: str) -> DynamicCriterionResult:
    return DynamicCriterionResult(
        criterion_id=criterion.id,
        criterion_name=criterion.name,
        status=DynamicStatus.incomplete,
        summary=summary,
        missing_evidence=["llm_evaluation"],
        requires_llm=True,
        llm_error=error,
        generated_at=datetime.now(timezone.utc),
    )
