"""Run dynamic WCAG benchmarking for FlowA11y."""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib
import json
import os
import shutil
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from dynamic.analyzers.dynamic_wcag_analyzer import DynamicWCAGAnalyzer
from dynamic.preprocessors.wcag_mapper import DYNAMIC_WCAG_REGISTRY, prompt_filename
from dynamic.runtime_extractors.utils import write_json
from dynamic.schemas.runtime_state import RuntimeState
from dynamic.schemas.runtime_trace import RuntimeTrace, RuntimeTraceMetadata
from dynamic.schemas.wcag_dynamic import DynamicStatus, DynamicWCAGReport

from benchmarking.dynamic_baselines import (
    GEMINI_DYNAMIC_WCAG_BASELINE,
    HUMAN_ORACLE_BASELINE,
    HYBRID_DYNAMIC_WCAG_BASELINE,
    MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE,
    MATCHED_TRACE_GENERIC_JUDGE_BASELINE,
    NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE,
    RULES_DYNAMIC_WCAG_BASELINE,
    SYNTHETIC_FIXTURE_BASELINE,
    get_baseline_specs,
)
from benchmarking.dynamic.naive_browser_use_baseline import run_naive_browser_use_baseline
from benchmarking.dynamic.matched_global_evidence_baseline import run_matched_global_evidence_ablation
from benchmarking.dynamic.matched_trace_baseline import run_matched_trace_generic_judge
from benchmarking.config import (
    DEFAULT_GEMINI_MAX_RETRIES,
    DEFAULT_GEMINI_RETRY_BASE_S,
    DEFAULT_GEMINI_RETRY_MAX_S,
    load_local_env,
    resolve_model_name,
    resolve_model_pricing,
)
from benchmarking.dynamic_datasets import (
    D_dyn_fixture,
    D_dyn_real,
    DynamicBenchmarkDataset,
    DynamicBenchmarkScenario,
    flatten_expected_results,
    flatten_oracle_labels,
    default_real_oracle_path,
    load_dynamic_dataset,
    load_dynamic_dataset_file,
    load_real_oracle_labels,
    validate_expected_results,
)
from benchmarking.dynamic_metrics import (
    build_baseline_comparison_rows,
    build_dynamic_quality_summary_rows,
    build_false_audit_rows,
    compute_publication_metrics,
    compute_criterion_precision,
    compute_criterion_recall,
    compute_status_confusion_counts,
    compute_unsupported_incomplete_counts,
)
from benchmarking.dynamic_manual_review import (
    build_dynamic_manual_label_rows,
    write_dynamic_manual_label_queue,
    write_dynamic_manual_label_template,
    write_dynamic_oracle_json_template,
)
from benchmarking.model_client import CachedGeminiModelClient, DeterministicDryRunModelClient

DYNAMIC_ROOT = Path(__file__).resolve().parent
DEFAULT_RESULTS_DIR = DYNAMIC_ROOT / "results"
RAW_DIR_NAME = "raw"
TABLES_DIR_NAME = "tables"
FIGURES_DIR_NAME = "figures"

RUN_STATUSES = {
    "completed",
    "blocked",
    "challenge_or_bot_page",
    "extraction_error",
    "incomplete",
}

REMOVED_CHALLENGE_SCENARIO_IDS = (
    "real_reddit_share_dialog_focus_return",
    "real_yelp_filter_panel_results_reload",
    "real_imdb_lightbox_keyboard_navigation",
)


class BenchmarkBlockedError(RuntimeError):
    """Raised when a benchmark scenario cannot run until setup is completed."""


@dataclass(frozen=True)
class BrowserUseAvailability:
    available: bool
    reason: str
    source_path: str = ""
    install_command: str = "python -m pip install -e ../browser-use"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-set", choices=["fixture", "real"], default="fixture")
    parser.add_argument("--scenario-file", type=Path, default=None, help="Explicit dynamic scenario dataset JSON.")
    parser.add_argument("--scenario-id", action="append", default=[], help="Scenario id to include; can repeat.")
    parser.add_argument("--criteria", action="append", default=[], help="Comma-separated criteria to evaluate.")
    parser.add_argument("--max-scenarios", type=int, default=None)
    parser.add_argument("--browser-max-steps", type=int, default=None)
    parser.add_argument(
        "--browser-use-model",
        default=None,
        help=(
            "Model for browser-use orchestration. Defaults to --gemini-model/GEMINI_MODEL/default; "
            "set this when browser-use should use a different model than the formal judge."
        ),
    )
    parser.add_argument(
        "--browser-use-fallback-model",
        default="gemini-2.5-flash-lite",
        help=(
            "Fallback browser-use orchestration model after a primary provider/rate-limit failure; "
            "use 'none' to disable."
        ),
    )
    parser.add_argument(
        "--browser-use-scenario-retries",
        type=int,
        default=3,
        help="Retry an unsuccessful browser-use scenario this many times.",
    )
    parser.add_argument(
        "--browser-use-scenario-retry-base-s",
        type=float,
        default=30.0,
        help="Initial delay before retrying a transiently failed browser-use scenario.",
    )
    parser.add_argument(
        "--browser-use-scenario-retry-max-s",
        type=float,
        default=180.0,
        help="Maximum delay before retrying a transiently failed browser-use scenario.",
    )
    parser.add_argument(
        "--browser-use-retry-goal-not-reached",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Retry returned browser-use traces that do not contain a successful done action.",
    )
    parser.add_argument("--browser-use-llm-retries", type=int, default=3)
    parser.add_argument("--browser-use-fallback-retries", type=int, default=8)
    parser.add_argument("--browser-use-llm-retry-base-s", type=float, default=2.0)
    parser.add_argument("--browser-use-llm-retry-max-s", type=float, default=90.0)
    parser.add_argument("--browser-use-max-failures", type=int, default=8)
    parser.add_argument("--browser-use-llm-timeout-s", type=int, default=120)
    parser.add_argument("--browser-use-step-timeout-s", type=int, default=240)
    parser.add_argument("--dry-run", action="store_true", help="Use deterministic fixture traces and no model/API calls.")
    parser.add_argument("--gemini-model", default=None)
    parser.add_argument(
        "--dynamic-judge",
        choices=["rules", "gemini", "hybrid"],
        default="rules",
        help="Formal dynamic WCAG judge: deterministic rules, Gemini prompts, or Gemini with rule fallback.",
    )
    parser.add_argument(
        "--dynamic-criteria-scope",
        choices=["scenario", "all-prompts"],
        default="scenario",
        help="Use CLI/scenario fixture criteria or all supported dynamic prompt criteria.",
    )
    parser.add_argument(
        "--oracle-file",
        type=Path,
        default=None,
        help="External real oracle CSV. Defaults to real_oracle_labels.csv next to the real scenario file.",
    )
    parser.add_argument(
        "--allow-sparse-real-oracle",
        action="store_true",
        help="Allow real all-prompts metrics when the external oracle is intentionally sparse.",
    )
    parser.add_argument(
        "--execution-mode",
        choices=["fixture-trace", "browser-use"],
        default="fixture-trace",
        help="Runtime execution source. browser-use is optional and skipped cleanly when unavailable.",
    )
    parser.add_argument(
        "--comparison-baseline",
        choices=[
            NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id,
            MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id,
            MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id,
            "none",
        ],
        default=NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id,
        help="Paper-facing dynamic comparison baseline.",
    )
    parser.add_argument(
        "--baseline-max-steps",
        type=int,
        default=None,
        help="Step budget for the comparison baseline; defaults to --browser-max-steps or scenario.max_steps.",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Report scenario/oracle readiness and recommended publication/exploratory commands without running.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    return parser.parse_args(argv)


async def async_main(args: argparse.Namespace) -> int:
    if bool(getattr(args, "preflight", False)):
        print(json.dumps(build_preflight_for_args(args), indent=2, sort_keys=True, ensure_ascii=True))
        return 0
    summary = await run_dynamic_benchmark(args)
    print("Dynamic benchmark finished.")
    print(f"- Results: {summary['results_dir']}")
    print(f"- Raw: {summary['raw_dir']}")
    print(f"- Tables: {summary['tables_dir']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(parse_args(argv)))


async def run_dynamic_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    """Run dynamic benchmark scenarios and write raw artifacts plus normalized tables."""

    output_dir = Path(getattr(args, "output_dir", DEFAULT_RESULTS_DIR))
    raw_dir = output_dir / RAW_DIR_NAME
    tables_dir = output_dir / TABLES_DIR_NAME
    figures_dir = output_dir / FIGURES_DIR_NAME
    for path in (output_dir, raw_dir, tables_dir, figures_dir):
        path.mkdir(parents=True, exist_ok=True)
    run_id = "run_1"
    run_raw_dir = raw_dir / run_id
    if run_raw_dir.exists():
        shutil.rmtree(run_raw_dir)
    run_raw_dir.mkdir(parents=True, exist_ok=True)

    dataset = _load_dataset_for_args(args)
    validation_errors = validate_expected_results(dataset)
    if validation_errors:
        raise ValueError("Invalid dynamic benchmark dataset: " + "; ".join(validation_errors))

    scenarios = _select_scenarios(dataset, getattr(args, "scenario_id", []) or [], getattr(args, "max_scenarios", None))
    selected_dataset = DynamicBenchmarkDataset(
        dataset_id=dataset.dataset_id,
        name=dataset.name,
        baseline_kind=dataset.baseline_kind,
        scenarios=tuple(scenarios),
        description=dataset.description,
    )
    requested_criteria = _normalize_criteria(getattr(args, "criteria", []) or [])
    criteria_scope = str(getattr(args, "dynamic_criteria_scope", "scenario") or "scenario")
    judge_mode = str(getattr(args, "dynamic_judge", "rules") or "rules")
    if criteria_scope not in {"scenario", "all-prompts"}:
        raise ValueError(f"Unsupported dynamic criteria scope: {criteria_scope}")
    if judge_mode not in {"rules", "gemini", "hybrid"}:
        raise ValueError(f"Unsupported dynamic judge mode: {judge_mode}")
    oracle_rows = _oracle_rows_for_metrics(args, selected_dataset)
    oracle_cardinality = _assert_oracle_cardinality_for_metrics(
        args=args,
        dataset=selected_dataset,
        criteria_scope=criteria_scope,
        requested_criteria=requested_criteria,
        oracle_rows=oracle_rows,
    )
    preflight = build_publication_preflight(
        args=args,
        dataset=selected_dataset,
        requested_criteria=requested_criteria,
        criteria_scope=criteria_scope,
        oracle_rows=oracle_rows,
    )

    model_name = resolve_model_name(getattr(args, "gemini_model", None))
    pricing = resolve_model_pricing(model_name)
    model_client = _create_dynamic_model_client(
        judge_mode=judge_mode,
        dry_run=bool(getattr(args, "dry_run", False)),
        model_name=model_name,
        cache_dir=output_dir / "cache" / run_id,
    )
    analyzer = DynamicWCAGAnalyzer(model_client=model_client, judge_mode=judge_mode)
    analyzer_baseline_id = _analyzer_baseline_id(judge_mode)
    comparison_baseline = str(
        getattr(args, "comparison_baseline", NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id)
        or NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id
    )
    if comparison_baseline not in {
        NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id,
        MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id,
        MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id,
        "none",
    }:
        raise ValueError(f"Unsupported dynamic comparison baseline: {comparison_baseline}")
    if comparison_baseline == MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id and judge_mode != "gemini":
        raise ValueError("Baseline 3.0 requires --dynamic-judge gemini so it shares FlowA11y's Gemini judgment path.")

    matched_trace_model_client = None
    uses_matched_comparison = comparison_baseline in {
        MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id,
        MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id,
    }
    if uses_matched_comparison:
        matched_trace_model_client = _create_dynamic_model_client(
            judge_mode="gemini",
            dry_run=bool(getattr(args, "dry_run", False)),
            model_name=model_name,
            cache_dir=output_dir / "cache" / run_id / comparison_baseline,
        )

    case_records: list[dict[str, Any]] = []
    finding_records: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []
    quality_records: list[dict[str, Any]] = []
    comparison_report_rows: list[dict[str, Any]] = []
    comparison_skip_reasons: set[str] = set()
    unsupported_seen: set[str] = set()

    for scenario in scenarios:
        scenario_raw_dir = raw_dir / run_id / scenario.scenario_id
        scenario_raw_dir.mkdir(parents=True, exist_ok=True)
        criteria = _criteria_for_scenario(
            dataset=selected_dataset,
            scenario=scenario,
            requested_criteria=requested_criteria,
            criteria_scope=criteria_scope,
        )
        _verify_prompt_files(criteria)
        start = time.perf_counter()

        try:
            trace = await _runtime_trace_for_scenario(scenario, args, scenario_raw_dir)
            trace_path = scenario_raw_dir / "runtime_trace.json"
            write_json(trace_path, trace.model_dump(mode="json"))

            _set_model_context(
                model_client,
                run_id=run_id,
                dataset=dataset.dataset_id,
                scenario_id=scenario.scenario_id,
            )
            report, evidence_bundle, artifacts = await analyzer.analyze(trace, criteria=criteria)
            formal_report_path = analyzer.export(scenario_raw_dir, report, artifacts)
            elapsed_s = round(time.perf_counter() - start, 6)
            unsupported_seen.update(report.unsupported_criteria)
            run_status = _run_status_for(trace, report)
            task_summary = _browser_task_summary(trace)
            scoring = _scoring_status_for(
                scenario=scenario,
                trace=trace,
                report=report,
                run_status=run_status,
                task_summary=task_summary,
                execution_mode=str(getattr(args, "execution_mode", "fixture-trace")),
            )

            result_rows = _result_rows(
                run_id=run_id,
                dataset=dataset,
                scenario=scenario,
                report=report,
                formal_report_path=formal_report_path,
                runtime_trace_path=trace_path,
                run_status=run_status,
                analyzer_baseline_id=analyzer_baseline_id,
                artifacts=artifacts,
                scoring=scoring,
            )
            report_rows.extend(result_rows)
            finding_records.extend(result_rows)
            quality_records.extend(
                _evidence_quality_rows(
                    run_id=run_id,
                    dataset=dataset,
                    scenario=scenario,
                    report=report,
                    artifacts=artifacts,
                    analyzer_baseline_id=analyzer_baseline_id,
                )
            )

            naive_reason = (
                _naive_baseline_skip_reason(args, scenario, comparison_baseline)
                if comparison_baseline == NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id
                else None
            )
            if comparison_baseline == NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id and naive_reason is None:
                baseline_max_steps = _baseline_max_steps_for_scenario(args, scenario)
                try:
                    naive_result = await run_naive_browser_use_baseline(
                        scenario=scenario,
                        output_dir=scenario_raw_dir / "baselines" / NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id,
                        max_steps=baseline_max_steps,
                        allowed_criteria=criteria,
                        model_name=model_name,
                    )
                except Exception as exc:  # noqa: BLE001
                    comparison_skip_reasons.add(f"naive baseline failed for {scenario.scenario_id}: {exc}")
                else:
                    naive_run_status = _run_status_for(naive_result.runtime_trace, naive_result.report)
                    unsupported_seen.update(naive_result.report.unsupported_criteria)
                    comparison_report_rows.extend(
                        _result_rows(
                            run_id=run_id,
                            dataset=dataset,
                            scenario=scenario,
                            report=naive_result.report,
                            formal_report_path=naive_result.formal_report_path,
                            runtime_trace_path=naive_result.runtime_trace_path,
                            run_status=naive_run_status,
                            analyzer_baseline_id=NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id,
                            scoring=scoring,
                        )
                    )
            elif comparison_baseline == MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id:
                try:
                    matched_result = await run_matched_trace_generic_judge(
                        scenario=scenario,
                        runtime_trace=trace,
                        runtime_trace_path=trace_path,
                        output_dir=scenario_raw_dir / "baselines" / MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id,
                        requested_criteria=criteria,
                        model_client=matched_trace_model_client,
                        run_id=run_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    comparison_skip_reasons.add(f"Baseline 2.0 failed for {scenario.scenario_id}: {exc}")
                else:
                    comparison_report_rows.extend(
                        _result_rows(
                            run_id=run_id,
                            dataset=dataset,
                            scenario=scenario,
                            report=matched_result.report,
                            formal_report_path=matched_result.formal_report_path,
                            runtime_trace_path=matched_result.runtime_trace_path,
                            run_status=run_status,
                            analyzer_baseline_id=MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id,
                            scoring=scoring,
                        )
                    )
            elif comparison_baseline == MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id:
                try:
                    matched_result = await run_matched_global_evidence_ablation(
                        scenario=scenario,
                        dataset_id=dataset.dataset_id,
                        runtime_trace=trace,
                        runtime_trace_path=trace_path,
                        output_dir=scenario_raw_dir / "baselines" / MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id,
                        requested_criteria=criteria,
                        primary_artifacts=artifacts,
                        model_client=matched_trace_model_client,
                        run_id=run_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    comparison_skip_reasons.add(f"Baseline 3.0 failed for {scenario.scenario_id}: {exc}")
                else:
                    comparison_report_rows.extend(
                        _result_rows(
                            run_id=run_id,
                            dataset=dataset,
                            scenario=scenario,
                            report=matched_result.report,
                            formal_report_path=matched_result.formal_report_path,
                            runtime_trace_path=matched_result.runtime_trace_path,
                            run_status=run_status,
                            analyzer_baseline_id=MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id,
                            artifacts=matched_result.artifacts,
                            scoring=scoring,
                        )
                    )
            elif comparison_baseline == NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id:
                comparison_skip_reasons.add(naive_reason or "naive baseline was not run")

            case_record = _case_record(
                run_id=run_id,
                dataset=dataset,
                scenario=scenario,
                criteria=criteria,
                report=report,
                evidence_bundle_steps=evidence_bundle.steps_count,
                elapsed_s=elapsed_s,
                run_status=run_status,
                task_summary=task_summary,
                scoring=scoring,
                runtime_trace_path=trace_path,
                formal_report_path=formal_report_path,
            )
            case_records.append(case_record)
            write_json(scenario_raw_dir / "scenario_result.json", case_record)
        except Exception as exc:  # noqa: BLE001
            elapsed_s = round(time.perf_counter() - start, 6)
            case_record = _error_case_record(
                run_id=run_id,
                dataset=dataset,
                scenario=scenario,
                criteria=criteria,
                elapsed_s=elapsed_s,
                error=exc,
            )
            case_records.append(case_record)
            error_rows = _error_result_rows(
                run_id=run_id,
                dataset=dataset,
                scenario=scenario,
                criteria=criteria,
                run_status=str(case_record.get("run_status", "")),
                analyzer_baseline_id=analyzer_baseline_id,
                error=exc,
            )
            report_rows.extend(error_rows)
            finding_records.extend(error_rows)
            write_json(scenario_raw_dir / "scenario_result.json", case_record)

    leakage_summary = _assert_no_oracle_label_leakage(raw_dir / run_id, oracle_rows)

    complete_oracle_scenario_ids = set(preflight.get("complete_oracle_scenario_ids", []))
    primary_report_rows = _primary_scored_report_rows(report_rows, complete_oracle_scenario_ids)
    primary_oracle_rows = _oracle_rows_for_report_keys(oracle_rows, primary_report_rows)

    recall = compute_criterion_recall(selected_dataset, primary_report_rows, oracle_rows=primary_oracle_rows)
    precision = compute_criterion_precision(selected_dataset, primary_report_rows, oracle_rows=primary_oracle_rows)
    confusion_rows = compute_status_confusion_counts(selected_dataset, primary_report_rows, oracle_rows=primary_oracle_rows)
    publication_metrics = compute_publication_metrics(selected_dataset, primary_report_rows, oracle_rows=primary_oracle_rows)
    attempted_recall = compute_criterion_recall(selected_dataset, report_rows, oracle_rows=oracle_rows)
    attempted_precision = compute_criterion_precision(selected_dataset, report_rows, oracle_rows=oracle_rows)
    attempted_confusion_rows = compute_status_confusion_counts(selected_dataset, report_rows, oracle_rows=oracle_rows)
    attempted_publication_metrics = compute_publication_metrics(selected_dataset, report_rows, oracle_rows=oracle_rows)
    task_success = _task_success_summary(case_records)
    dynamic_quality_summary_rows = build_dynamic_quality_summary_rows(
        publication_metrics,
        baseline_id=analyzer_baseline_id,
        task_success_summary=task_success,
        scope="primary_scored",
    )
    attempted_dynamic_quality_summary_rows = build_dynamic_quality_summary_rows(
        attempted_publication_metrics,
        baseline_id=analyzer_baseline_id,
        task_success_summary=task_success,
        scope="attempted",
    )
    false_positive_rows, false_negative_rows = build_false_audit_rows(
        selected_dataset,
        report_rows,
        oracle_rows=oracle_rows,
    )
    unsupported_incomplete = compute_unsupported_incomplete_counts(report_rows, unsupported_seen)
    model_summary = model_client.summary() if model_client is not None else _empty_model_summary(model_name)
    model_cost_summary = _model_cost_summary(model_summary, pricing)
    model_call_records = list(getattr(model_client, "call_records", [])) if model_client is not None else []
    model_call_verification_rows = _model_call_verification_rows(model_call_records)

    coverage_rows = _coverage_rows(selected_dataset, report_rows, unsupported_seen, oracle_rows=oracle_rows)
    recall_rows = _recall_rows(recall)
    precision_rows = _precision_rows(precision)
    attempted_recall_rows = _recall_rows(attempted_recall)
    attempted_precision_rows = _precision_rows(attempted_precision)
    variability_rows = _variability_rows(case_records, report_rows)
    quality_by_status_rows = _quality_by_status_rows(quality_records)
    scoring_audit_rows = _scoring_audit_rows(selected_dataset, report_rows, oracle_rows=oracle_rows)
    invalid_evidence_ref_rows = _invalid_evidence_ref_rows(report_rows)
    error_analysis_rows = _error_analysis_rows(false_positive_rows, false_negative_rows)
    reports_by_baseline: dict[str, list[dict[str, Any]]] = {}
    if report_rows:
        reports_by_baseline[analyzer_baseline_id] = report_rows
    if comparison_report_rows:
        reports_by_baseline[comparison_baseline] = comparison_report_rows
    primary_reports_by_baseline = {
        baseline_id: _primary_scored_report_rows(rows, complete_oracle_scenario_ids)
        for baseline_id, rows in reports_by_baseline.items()
    }
    primary_reports_by_baseline = {
        baseline_id: rows for baseline_id, rows in primary_reports_by_baseline.items() if rows
    }
    baseline_finding_rows = [
        row for baseline_id in sorted(reports_by_baseline) for row in reports_by_baseline[baseline_id]
    ]
    ablation_rows = _ablation_rows(
        selected_dataset,
        primary_reports_by_baseline,
        analyzer_baseline_id,
        judge_mode,
        oracle_rows=primary_oracle_rows,
    )
    performance_rows = _performance_rows(case_records, model_summary, model_cost_summary, judge_mode)
    completed_baseline_ids = {baseline_id for baseline_id, rows in primary_reports_by_baseline.items() if rows}
    baseline_rows = _baseline_status_rows(
        judge_mode,
        completed_baseline_ids,
        selected_dataset,
        comparison_baseline=comparison_baseline,
        comparison_skip_reasons=sorted(comparison_skip_reasons),
    )
    baseline_comparison_rows = build_baseline_comparison_rows(
        selected_dataset,
        primary_reports_by_baseline,
        oracle_rows=primary_oracle_rows,
    )
    attempted_baseline_comparison_rows = build_baseline_comparison_rows(
        selected_dataset,
        reports_by_baseline,
        oracle_rows=oracle_rows,
    )
    baseline_pairwise_rows = _baseline_pairwise_rows(
        selected_dataset,
        primary_reports_by_baseline,
        primary_baseline_id=analyzer_baseline_id,
        comparison_baseline_id=comparison_baseline,
        oracle_rows=primary_oracle_rows,
    )
    attempted_baseline_pairwise_rows = _baseline_pairwise_rows(
        selected_dataset,
        reports_by_baseline,
        primary_baseline_id=analyzer_baseline_id,
        comparison_baseline_id=comparison_baseline,
        oracle_rows=oracle_rows,
    )
    manual_label_rows = build_dynamic_manual_label_rows(
        selected_dataset,
        case_records,
        finding_records,
        raw_dir,
        oracle_rows=oracle_rows,
    )
    manual_label_queue_path = write_dynamic_manual_label_queue(
        manual_label_rows,
        output_dir / "manual_label_queue.csv",
    )
    manual_label_template_path = write_dynamic_manual_label_template(
        manual_label_rows,
        output_dir / "manual_label_template.csv",
    )
    manual_oracle_template_path = write_dynamic_oracle_json_template(
        selected_dataset,
        manual_label_rows,
        output_dir / "manual_oracle_template.json",
    )

    _write_csv(tables_dir / "dynamic_case_records.csv", case_records)
    _write_csv(tables_dir / "dynamic_finding_status_records.csv", finding_records)
    _write_csv(tables_dir / "dynamic_recall.csv", recall_rows)
    _write_csv(tables_dir / "dynamic_precision.csv", precision_rows)
    _write_csv(tables_dir / "attempted_dynamic_recall.csv", attempted_recall_rows)
    _write_csv(tables_dir / "attempted_dynamic_precision.csv", attempted_precision_rows)
    _write_csv(tables_dir / "dynamic_coverage.csv", coverage_rows)
    _write_csv(tables_dir / "dynamic_evidence_quality.csv", quality_records)
    _write_csv(tables_dir / "dynamic_quality_by_status.csv", quality_by_status_rows)
    _write_csv(tables_dir / "dynamic_quality_summary.csv", dynamic_quality_summary_rows)
    _write_csv(tables_dir / "attempted_dynamic_quality_summary.csv", attempted_dynamic_quality_summary_rows)
    _write_csv(tables_dir / "dynamic_false_positive_audit.csv", false_positive_rows)
    _write_csv(tables_dir / "dynamic_false_negative_audit.csv", false_negative_rows)
    _write_csv(tables_dir / "dynamic_error_analysis.csv", error_analysis_rows)
    _write_csv(tables_dir / "dynamic_scoring_audit.csv", scoring_audit_rows)
    _write_csv(tables_dir / "invalid_evidence_refs.csv", invalid_evidence_ref_rows)
    _write_csv(tables_dir / "dynamic_variability.csv", variability_rows)
    _write_csv(tables_dir / "dynamic_ablation.csv", ablation_rows)
    _write_csv(tables_dir / "dynamic_performance_cost.csv", performance_rows)
    _write_csv(tables_dir / "status_confusion.csv", confusion_rows)
    _write_csv(tables_dir / "attempted_status_confusion.csv", attempted_confusion_rows)
    _write_csv(tables_dir / "baseline_comparison.csv", baseline_comparison_rows)
    _write_csv(tables_dir / "attempted_baseline_comparison.csv", attempted_baseline_comparison_rows)
    _write_csv(tables_dir / "baseline_finding_status_records.csv", baseline_finding_rows)
    _write_csv(tables_dir / "baseline_pairwise_comparison.csv", baseline_pairwise_rows)
    _write_csv(tables_dir / "attempted_baseline_pairwise_comparison.csv", attempted_baseline_pairwise_rows)
    _write_csv(tables_dir / "baseline_status.csv", baseline_rows)
    _write_csv(tables_dir / "model_calls.csv", model_call_records)
    _write_csv(tables_dir / "model_call_verification.csv", model_call_verification_rows)
    matched_trace_model_summary = (
        matched_trace_model_client.summary()
        if matched_trace_model_client is not None
        else _empty_model_summary(model_name)
    )
    matched_trace_model_cost_summary = _model_cost_summary(matched_trace_model_summary, pricing)
    if comparison_baseline in {
        MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id,
        MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id,
    }:
        model_call_filename = (
            "baseline_2_0_model_calls.csv"
            if comparison_baseline == MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id
            else "baseline_3_0_model_calls.csv"
        )
        _write_csv(
            tables_dir / model_call_filename,
            list(getattr(matched_trace_model_client, "call_records", [])),
        )
    _write_csv(tables_dir / "unsupported_incomplete_counts.csv", [unsupported_incomplete])
    _write_csv(
        tables_dir / "criteria_support_matrix.csv",
        _criteria_support_rows(selected_dataset, report_rows, unsupported_seen, oracle_rows=oracle_rows),
    )

    summary = {
        "mode": "dynamic",
        "dataset_id": dataset.dataset_id,
        "scenario_set": getattr(args, "scenario_set", "fixture"),
        "dry_run": bool(getattr(args, "dry_run", False)),
        "execution_mode": getattr(args, "execution_mode", "fixture-trace"),
        "dynamic_judge": judge_mode,
        "dynamic_criteria_scope": criteria_scope,
        "analyzer_baseline_id": analyzer_baseline_id,
        "model": model_name,
        "model_summary": model_summary,
        "model_cost_summary": model_cost_summary,
        "model_call_verification": model_call_verification_rows,
        "comparison_model_summary": matched_trace_model_summary if uses_matched_comparison else {},
        "comparison_model_cost_summary": matched_trace_model_cost_summary if uses_matched_comparison else {},
        "run_id": run_id,
        "scenarios": len(scenarios),
        "available_scenarios": len(dataset.scenarios),
        "selected_scenario_ids": [scenario.scenario_id for scenario in scenarios],
        "scenario_id_filters": list(getattr(args, "scenario_id", []) or []),
        "max_scenarios": getattr(args, "max_scenarios", None),
        "scenario_file": str(getattr(args, "scenario_file", "") or ""),
        "oracle_file": str(_oracle_path_for_args(args, dataset) or ""),
        "oracle_rows": len(oracle_rows),
        "oracle_cardinality": oracle_cardinality,
        "preflight": preflight,
        "leakage_check": leakage_summary,
        "comparison_baseline": comparison_baseline,
        "browser_max_steps": getattr(args, "browser_max_steps", None),
        "browser_use_model": _browser_use_model_name(args),
        "browser_use_fallback_model": str(getattr(args, "browser_use_fallback_model", "") or ""),
        "browser_use_scenario_retries": max(0, int(getattr(args, "browser_use_scenario_retries", 0) or 0)),
        "browser_use_scenario_retry_base_s": float(getattr(args, "browser_use_scenario_retry_base_s", 0.0) or 0.0),
        "browser_use_scenario_retry_max_s": float(getattr(args, "browser_use_scenario_retry_max_s", 0.0) or 0.0),
        "browser_use_retry_goal_not_reached": bool(
            getattr(args, "browser_use_retry_goal_not_reached", True)
        ),
        "browser_use_llm_retries": int(getattr(args, "browser_use_llm_retries", 3) or 0),
        "browser_use_fallback_retries": int(getattr(args, "browser_use_fallback_retries", 8) or 0),
        "browser_use_llm_retry_base_s": float(getattr(args, "browser_use_llm_retry_base_s", 2.0) or 0.0),
        "browser_use_llm_retry_max_s": float(getattr(args, "browser_use_llm_retry_max_s", 90.0) or 0.0),
        "browser_use_max_failures": int(getattr(args, "browser_use_max_failures", 8) or 0),
        "browser_use_llm_timeout_s": int(getattr(args, "browser_use_llm_timeout_s", 120) or 0),
        "browser_use_step_timeout_s": int(getattr(args, "browser_use_step_timeout_s", 240) or 0),
        "baseline_max_steps": getattr(args, "baseline_max_steps", None),
        "results_dir": str(output_dir),
        "raw_dir": str(raw_dir),
        "tables_dir": str(tables_dir),
        "figures_dir": str(figures_dir),
        "case_records": len(case_records),
        "finding_status_records": len(finding_records),
        "attempted_rows": len(report_rows),
        "scored_rows": len(primary_report_rows),
        "unscored_rows": len(report_rows) - len(primary_report_rows),
        "unscored_reasons": _unscored_reason_counts(report_rows),
        "runtime_extraction_error_counts": _case_runtime_extraction_error_counts(case_records),
        "nonfatal_runtime_extraction_error_counts": _case_nonfatal_runtime_extraction_error_counts(case_records),
        "challenge_bot_count": sum(1 for record in case_records if str(record.get("run_status")) == "challenge_or_bot_page"),
        "baseline_finding_status_records": len(baseline_finding_rows),
        "comparison_baselines": sorted(reports_by_baseline),
        "unsupported_criteria": sorted(unsupported_seen),
        "recall": recall.get("overall", {}),
        "precision": precision.get("overall", {}),
        "publication_metrics": publication_metrics,
        "primary_metric_oracle_rows": len(primary_oracle_rows),
        "attempted_recall": attempted_recall.get("overall", {}),
        "attempted_precision": attempted_precision.get("overall", {}),
        "attempted_publication_metrics": attempted_publication_metrics,
        "dynamic_quality_summary": {
            row["metric"]: row.get("value") for row in dynamic_quality_summary_rows
        },
        "attempted_dynamic_quality_summary": {
            row["metric"]: row.get("value") for row in attempted_dynamic_quality_summary_rows
        },
        "task_success": task_success,
        "false_positive_audit_rows": len(false_positive_rows),
        "false_negative_audit_rows": len(false_negative_rows),
        "invalid_evidence_ref_rows": len(invalid_evidence_ref_rows),
        "invalid_evidence_ref_counts": _invalid_evidence_ref_counts(report_rows),
        "evidence_quality_counts": _quality_counts_summary(quality_records),
        "run_statuses": sorted({str(record.get("run_status")) for record in case_records}),
        "manual_label_queue": str(manual_label_queue_path),
        "manual_label_template": str(manual_label_template_path),
        "manual_oracle_template": str(manual_oracle_template_path),
    }
    write_json(output_dir / "benchmark_summary.json", summary)
    return summary


def _load_dataset_for_args(args: argparse.Namespace) -> DynamicBenchmarkDataset:
    scenario_file = getattr(args, "scenario_file", None)
    if scenario_file:
        return load_dynamic_dataset_file(Path(scenario_file))
    scenario_set = getattr(args, "scenario_set", "fixture")
    if scenario_set == "fixture":
        return load_dynamic_dataset(D_dyn_fixture)
    return load_dynamic_dataset(D_dyn_real)


def _select_scenarios(
    dataset: DynamicBenchmarkDataset,
    scenario_ids: list[str],
    max_scenarios: int | None,
) -> list[DynamicBenchmarkScenario]:
    scenarios = list(dataset.scenarios)
    if scenario_ids:
        wanted = set(scenario_ids)
        scenarios = [scenario for scenario in scenarios if scenario.scenario_id in wanted]
    if max_scenarios is not None:
        scenarios = scenarios[: max(0, max_scenarios)]
    return scenarios


def _normalize_criteria(raw_values: list[str]) -> list[str]:
    criteria: list[str] = []
    for raw in raw_values:
        for item in str(raw).split(","):
            criterion = item.strip()
            if criterion:
                criteria.append(criterion)
    return criteria


def _criteria_for_scenario(
    *,
    dataset: DynamicBenchmarkDataset,
    scenario: DynamicBenchmarkScenario,
    requested_criteria: list[str],
    criteria_scope: str,
) -> list[str]:
    if requested_criteria:
        return requested_criteria
    if criteria_scope == "all-prompts":
        return DYNAMIC_WCAG_REGISTRY.supported_ids()
    if criteria_scope == "scenario":
        if dataset.baseline_kind != HUMAN_ORACLE_BASELINE.kind and scenario.criteria_expected:
            return list(scenario.criteria_expected)
        return DYNAMIC_WCAG_REGISTRY.supported_ids()
    raise ValueError(f"Unsupported dynamic criteria scope: {criteria_scope}")


def build_preflight_for_args(args: argparse.Namespace) -> dict[str, Any]:
    """Build a publication-readiness preflight without executing scenarios."""

    dataset = _load_dataset_for_args(args)
    scenarios = _select_scenarios(dataset, getattr(args, "scenario_id", []) or [], getattr(args, "max_scenarios", None))
    selected_dataset = DynamicBenchmarkDataset(
        dataset_id=dataset.dataset_id,
        name=dataset.name,
        baseline_kind=dataset.baseline_kind,
        scenarios=tuple(scenarios),
        description=dataset.description,
    )
    requested_criteria = _normalize_criteria(getattr(args, "criteria", []) or [])
    criteria_scope = str(getattr(args, "dynamic_criteria_scope", "scenario") or "scenario")
    oracle_rows = _oracle_rows_for_metrics(args, selected_dataset)
    return build_publication_preflight(
        args=args,
        dataset=selected_dataset,
        requested_criteria=requested_criteria,
        criteria_scope=criteria_scope,
        oracle_rows=oracle_rows,
    )


def build_publication_preflight(
    *,
    args: argparse.Namespace,
    dataset: DynamicBenchmarkDataset,
    requested_criteria: list[str],
    criteria_scope: str,
    oracle_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Report oracle/scenario readiness for publication and exploratory runs."""

    expected_criteria = _preflight_criteria(dataset, requested_criteria, criteria_scope)
    expected_criteria_by_scenario = _preflight_criteria_by_scenario(
        dataset,
        requested_criteria,
        criteria_scope,
    )
    oracle_by_scenario: dict[str, set[str]] = {scenario.scenario_id: set() for scenario in dataset.scenarios}
    for row in oracle_rows:
        scenario_id = str(row.get("scenario_id", ""))
        criterion_id = str(row.get("criterion_id", ""))
        if scenario_id in oracle_by_scenario and criterion_id:
            oracle_by_scenario[scenario_id].add(criterion_id)

    missing_by_scenario: list[dict[str, Any]] = []
    complete_oracle_scenario_ids: list[str] = []
    for scenario in dataset.scenarios:
        expected_criteria_set = set(expected_criteria_by_scenario.get(scenario.scenario_id, expected_criteria))
        present = oracle_by_scenario.get(scenario.scenario_id, set())
        missing = sorted(expected_criteria_set - present)
        if missing:
            missing_by_scenario.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "missing_count": len(missing),
                    "present_count": len(present & expected_criteria_set),
                    "missing_criteria": missing,
                }
            )
        else:
            complete_oracle_scenario_ids.append(scenario.scenario_id)

    labels_pending_scenario_ids = sorted(
        scenario.scenario_id
        for scenario in dataset.scenarios
        if scenario.labels_pending or scenario.scenario_id not in complete_oracle_scenario_ids
    )
    expected_rows = sum(len(criteria) for criteria in expected_criteria_by_scenario.values())
    actual_rows = sum(
        len(oracle_by_scenario.get(scenario_id, set()) & set(criteria))
        for scenario_id, criteria in expected_criteria_by_scenario.items()
    )
    scenario_file = getattr(args, "scenario_file", None)
    scenario_file_text = str(scenario_file) if scenario_file else "benchmarking/fixtures/dynamic/real_scenarios.json"
    oracle_file = str(_oracle_path_for_args(args, dataset) or "")
    publication_ids_arg = " ".join(f"--scenario-id {scenario_id}" for scenario_id in complete_oracle_scenario_ids)
    base_command = (
        "python3 -m benchmarking.dynamic.run_dynamic_benchmark "
        "--scenario-set real --execution-mode browser-use --dynamic-judge gemini "
        "--dynamic-criteria-scope all-prompts"
    )
    if scenario_file:
        base_command += f" --scenario-file {scenario_file_text}"
    if oracle_file:
        base_command += f" --oracle-file {oracle_file}"

    return {
        "dataset_id": dataset.dataset_id,
        "scenario_count": len(dataset.scenarios),
        "supported_criteria_count": len(expected_criteria),
        "expected_oracle_rows": expected_rows,
        "actual_oracle_rows": actual_rows,
        "oracle_status": "complete" if actual_rows == expected_rows else "sparse",
        "missing_rows_by_scenario": missing_by_scenario,
        "labels_pending_scenario_ids": labels_pending_scenario_ids,
        "removed_challenge_scenario_ids": list(REMOVED_CHALLENGE_SCENARIO_IDS),
        "removed_challenge_scenarios_present": sorted(
            set(REMOVED_CHALLENGE_SCENARIO_IDS) & {scenario.scenario_id for scenario in dataset.scenarios}
        ),
        "complete_oracle_scenario_ids": complete_oracle_scenario_ids,
        "complete_oracle_rows": sum(
            len(expected_criteria_by_scenario.get(scenario_id, ())) for scenario_id in complete_oracle_scenario_ids
        ),
        "scenario_file": scenario_file_text,
        "oracle_file": oracle_file,
        "allow_sparse_real_oracle": bool(getattr(args, "allow_sparse_real_oracle", False)),
        "final_run_recommendation": {
            "publication_subset": {
                "description": "Use only complete-oracle scenarios and do not pass --allow-sparse-real-oracle.",
                "scenario_ids": complete_oracle_scenario_ids,
                "command": f"{base_command} {publication_ids_arg} --output-dir benchmarking/dynamic/results_real_publication".strip(),
            },
            "exploratory_full_set": {
                "description": "Non-publication only when oracle coverage is sparse; keeps all attempted rows visible.",
                "scenario_ids": [scenario.scenario_id for scenario in dataset.scenarios],
                "command": (
                    f"{base_command} --allow-sparse-real-oracle "
                    "--output-dir benchmarking/dynamic/results_real_exploratory"
                ),
            },
        },
    }


def _preflight_criteria(
    dataset: DynamicBenchmarkDataset,
    requested_criteria: list[str],
    criteria_scope: str,
) -> list[str]:
    if requested_criteria:
        return list(requested_criteria)
    if criteria_scope == "all-prompts" or dataset.baseline_kind == HUMAN_ORACLE_BASELINE.kind:
        return DYNAMIC_WCAG_REGISTRY.supported_ids()
    criteria = sorted({criterion for scenario in dataset.scenarios for criterion in scenario.criteria_expected})
    return criteria or DYNAMIC_WCAG_REGISTRY.supported_ids()


def _preflight_criteria_by_scenario(
    dataset: DynamicBenchmarkDataset,
    requested_criteria: list[str],
    criteria_scope: str,
) -> dict[str, list[str]]:
    if requested_criteria:
        return {scenario.scenario_id: list(requested_criteria) for scenario in dataset.scenarios}
    if criteria_scope == "all-prompts" or dataset.baseline_kind == HUMAN_ORACLE_BASELINE.kind:
        supported = DYNAMIC_WCAG_REGISTRY.supported_ids()
        return {scenario.scenario_id: supported for scenario in dataset.scenarios}
    return {
        scenario.scenario_id: list(scenario.criteria_expected) or DYNAMIC_WCAG_REGISTRY.supported_ids()
        for scenario in dataset.scenarios
    }


def _oracle_path_for_args(args: argparse.Namespace, dataset: DynamicBenchmarkDataset) -> Path | None:
    if dataset.baseline_kind != HUMAN_ORACLE_BASELINE.kind:
        return None
    oracle_file = getattr(args, "oracle_file", None)
    if oracle_file:
        return Path(oracle_file)
    scenario_file = getattr(args, "scenario_file", None)
    if scenario_file:
        return default_real_oracle_path(Path(scenario_file))
    return default_real_oracle_path()


def _oracle_rows_for_metrics(args: argparse.Namespace, dataset: DynamicBenchmarkDataset) -> list[dict[str, Any]]:
    if dataset.baseline_kind == HUMAN_ORACLE_BASELINE.kind:
        oracle_path = _oracle_path_for_args(args, dataset)
        labels = load_real_oracle_labels(oracle_path) if oracle_path is not None else ()
        return flatten_oracle_labels(dataset, labels)
    return flatten_expected_results(dataset)


def _assert_oracle_cardinality_for_metrics(
    *,
    args: argparse.Namespace,
    dataset: DynamicBenchmarkDataset,
    criteria_scope: str,
    requested_criteria: list[str],
    oracle_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    allow_sparse = bool(getattr(args, "allow_sparse_real_oracle", False))
    if dataset.baseline_kind != HUMAN_ORACLE_BASELINE.kind or criteria_scope != "all-prompts":
        return {
            "status": "not_required",
            "oracle_rows": len(oracle_rows),
            "expected_rows": "",
            "allow_sparse_real_oracle": allow_sparse,
        }

    expected_criteria = requested_criteria or DYNAMIC_WCAG_REGISTRY.supported_ids()
    expected_rows = len(dataset.scenarios) * len(expected_criteria)
    status = "passed" if len(oracle_rows) == expected_rows else "sparse_allowed" if allow_sparse else "failed"
    summary = {
        "status": status,
        "oracle_rows": len(oracle_rows),
        "expected_rows": expected_rows,
        "scenario_count": len(dataset.scenarios),
        "supported_dynamic_criteria_count": len(expected_criteria),
        "allow_sparse_real_oracle": allow_sparse,
    }
    if status == "failed":
        raise ValueError(
            "Real all-prompts metrics require a complete external oracle: "
            f"found {len(oracle_rows)} rows, expected {expected_rows} "
            f"({len(dataset.scenarios)} scenarios * {len(expected_criteria)} criteria). "
            "Pass --allow-sparse-real-oracle only for exploratory non-publication runs."
        )
    return summary


def _assert_no_oracle_label_leakage(raw_run_dir: Path, oracle_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fail if external oracle labels appear in analyzer prompt/runtime inputs."""

    snippets = _oracle_leakage_snippets(oracle_rows)
    if not snippets:
        return {"files_scanned": 0, "oracle_snippets": 0, "status": "skipped_no_oracle"}

    files = sorted(
        {
            path
            for pattern in (
                "**/criteria/*/prompt.txt",
                "**/criteria/*/prompt_template.txt",
                "**/runtime_trace.json",
                "**/runtime_state_step_*.json",
                "**/dom_snapshot_step_*.html",
            )
            for path in raw_run_dir.glob(pattern)
        }
    )
    leaks: list[dict[str, str]] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        lower_text = text.lower()
        if "expected_status" in lower_text or "expected_evidence" in lower_text:
            leaks.append({"path": str(path), "snippet": "expected_status/expected_evidence key"})
            continue
        if path.name not in {"prompt.txt", "prompt_template.txt"}:
            continue
        for snippet in snippets:
            if snippet.lower() in lower_text:
                leaks.append({"path": str(path), "snippet": snippet[:120]})
                break

    if leaks:
        details = "; ".join(f"{item['path']}: {item['snippet']}" for item in leaks[:5])
        raise ValueError(f"Oracle label leakage detected in analyzer prompt/runtime artifacts: {details}")

    return {"files_scanned": len(files), "oracle_snippets": len(snippets), "status": "passed"}


def _oracle_leakage_snippets(oracle_rows: list[dict[str, Any]]) -> list[str]:
    snippets: set[str] = set()
    for row in oracle_rows:
        evidence = str(row.get("expected_evidence") or row.get("evidence") or "")
        notes = str(row.get("notes") or "")
        for value in [*evidence.split(";"), notes]:
            normalized = " ".join(value.split())
            if len(normalized) >= 24:
                snippets.add(normalized)
    return sorted(snippets)


def _verify_prompt_files(criteria: list[str]) -> None:
    prompts_dir = APP_ROOT / "dynamic" / "prompts"
    missing = [
        criterion_id
        for criterion_id in criteria
        if DYNAMIC_WCAG_REGISTRY.is_supported(criterion_id)
        and not (prompts_dir / prompt_filename(criterion_id)).exists()
    ]
    if missing:
        raise ValueError("Missing dynamic WCAG prompt files for: " + ", ".join(sorted(missing)))


def _create_dynamic_model_client(
    *,
    judge_mode: str,
    dry_run: bool,
    model_name: str,
    cache_dir: Path,
) -> Any | None:
    if judge_mode == "rules":
        return None
    if dry_run:
        return DeterministicDryRunModelClient(model_name=f"dry-run:{model_name}")

    load_local_env()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        if judge_mode == "hybrid":
            return None
        raise RuntimeError("GEMINI_API_KEY is required unless --dry-run is set.")

    max_retries = int(os.environ.get("GEMINI_MAX_RETRIES", str(DEFAULT_GEMINI_MAX_RETRIES)))
    base_delay_s = float(os.environ.get("GEMINI_RETRY_BASE", str(DEFAULT_GEMINI_RETRY_BASE_S)))
    max_delay_s = float(os.environ.get("GEMINI_RETRY_MAX", str(DEFAULT_GEMINI_RETRY_MAX_S)))

    return CachedGeminiModelClient(
        api_key=api_key,
        model_name=model_name,
        cache_dir=cache_dir,
        max_retries=max_retries,
        base_delay_s=base_delay_s,
        max_delay_s=max_delay_s,
    )


def _set_model_context(
    model_client: Any | None,
    *,
    run_id: str,
    dataset: str,
    scenario_id: str,
) -> None:
    if model_client is None or not hasattr(model_client, "set_context"):
        return
    model_client.set_context(run_id=run_id, dataset=dataset, case_id=scenario_id)


def _analyzer_baseline_id(judge_mode: str) -> str:
    if judge_mode == "gemini":
        return GEMINI_DYNAMIC_WCAG_BASELINE.baseline_id
    if judge_mode == "hybrid":
        return HYBRID_DYNAMIC_WCAG_BASELINE.baseline_id
    return RULES_DYNAMIC_WCAG_BASELINE.baseline_id


def _expected_label_baseline_id(baseline_kind: str) -> str:
    if baseline_kind == "human_oracle":
        return HUMAN_ORACLE_BASELINE.baseline_id
    if baseline_kind == "synthetic_fixture":
        return SYNTHETIC_FIXTURE_BASELINE.baseline_id
    return baseline_kind


def _empty_model_summary(model_name: str) -> dict[str, Any]:
    return {
        "model": model_name,
        "total_calls": 0,
        "api_calls": 0,
        "cache_hits": 0,
        "errors": 0,
        "prompt_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_usage_calls": 0,
        "total_model_time_s": 0.0,
    }


def _model_cost_summary(model_summary: dict[str, Any], pricing: Any) -> dict[str, Any]:
    prompt_tokens = int(model_summary.get("prompt_tokens") or 0)
    output_tokens = int(model_summary.get("output_tokens") or 0)
    estimated_cost = None
    if pricing.input_per_million is not None and pricing.output_per_million is not None:
        estimated_cost = (prompt_tokens / 1_000_000.0) * float(pricing.input_per_million) + (
            output_tokens / 1_000_000.0
        ) * float(pricing.output_per_million)
    return {
        "model_calls": int(model_summary.get("total_calls") or 0),
        "api_calls": int(model_summary.get("api_calls") or 0),
        "cache_hits": int(model_summary.get("cache_hits") or 0),
        "errors": int(model_summary.get("errors") or 0),
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(model_summary.get("total_tokens") or 0),
        "estimated_usage_calls": int(model_summary.get("estimated_usage_calls") or 0),
        "estimated_cost": estimated_cost,
        "estimated_cost_currency": pricing.currency,
        "cost_available": estimated_cost is not None,
    }


def add_local_browser_use_source() -> Path | None:
    """Prefer the local ../browser-use checkout when it exists."""

    local_source = APP_ROOT.parent / "browser-use"
    if (local_source / "browser_use").exists() and str(local_source) not in sys.path:
        sys.path.insert(0, str(local_source))
    return local_source if (local_source / "browser_use").exists() else None


def browser_use_availability() -> BrowserUseAvailability:
    """Check whether live browser-use scenarios can run in this environment."""

    source = add_local_browser_use_source()
    try:
        importlib.import_module("browser_use")
        browser_use = importlib.import_module("browser_use")
        getattr(browser_use, "Agent")
        getattr(browser_use, "BrowserSession")
        getattr(browser_use, "ChatGoogle")
    except Exception as exc:  # noqa: BLE001
        source_text = str(source) if source else ""
        return BrowserUseAvailability(
            available=False,
            reason=(
                f"browser-use is not runnable in this environment: {type(exc).__name__}: {exc}. "
                "From a11y-agent, run `python -m pip install -e ../browser-use` and ensure GEMINI_API_KEY is set."
            ),
            source_path=source_text,
        )

    return BrowserUseAvailability(
        available=True,
        reason="browser-use package and required exports are importable",
        source_path=str(source) if source else "",
    )


async def _runtime_trace_for_scenario(
    scenario: DynamicBenchmarkScenario,
    args: argparse.Namespace,
    output_dir: Path,
) -> RuntimeTrace:
    execution_mode = getattr(args, "execution_mode", "fixture-trace")
    if execution_mode == "browser-use":
        return await _run_browser_use_scenario(scenario, args, output_dir)
    return build_fixture_runtime_trace(scenario)


async def _run_browser_use_scenario(
    scenario: DynamicBenchmarkScenario,
    args: argparse.Namespace,
    output_dir: Path,
) -> RuntimeTrace:
    availability = browser_use_availability()
    if not availability.available:
        raise BenchmarkBlockedError(availability.reason)

    try:
        from dynamic.browser_use_agent import BrowserUseAccessibilityAgent
    except Exception as exc:  # noqa: BLE001
        raise BenchmarkBlockedError(f"browser-use execution unavailable: {exc}") from exc

    max_steps_override = getattr(args, "browser_max_steps", None)
    max_steps = int(max_steps_override) if max_steps_override is not None else scenario.max_steps
    max_attempts = max(1, int(getattr(args, "browser_use_scenario_retries", 3) or 0) + 1)
    last_exc: Exception | None = None
    retry_reasons: list[str] = []
    for attempt_index in range(max_attempts):
        agent = BrowserUseAccessibilityAgent(
            output_root=output_dir.parent,
            enable_evaluator=False,
            enable_formal_wcag=False,
            model_name=_browser_use_model_name(args),
            fallback_model_name=str(
                getattr(args, "browser_use_fallback_model", "gemini-2.5-flash-lite") or ""
            ),
            llm_max_retries=int(getattr(args, "browser_use_llm_retries", 3) or 0),
            fallback_llm_max_retries=int(getattr(args, "browser_use_fallback_retries", 8) or 0),
            llm_retry_base_s=float(getattr(args, "browser_use_llm_retry_base_s", 2.0) or 0.0),
            llm_retry_max_s=float(getattr(args, "browser_use_llm_retry_max_s", 90.0) or 0.0),
            agent_max_failures=int(getattr(args, "browser_use_max_failures", 8) or 1),
            agent_llm_timeout_s=int(getattr(args, "browser_use_llm_timeout_s", 120) or 1),
            agent_step_timeout_s=int(getattr(args, "browser_use_step_timeout_s", 240) or 1),
        )
        try:
            attempt_task = _browser_use_task_for_attempt(
                scenario.task_description,
                attempt_index=attempt_index,
                prior_retry_reason=retry_reasons[-1] if retry_reasons else "",
            )
            trace = await agent.run_scenario(
                attempt_task,
                scenario_id=scenario.scenario_id,
                max_steps=max(1, max_steps + (attempt_index * 2)),
            )
            task_summary = _browser_task_summary(trace)
            retry_reason = _browser_use_trace_retry_reason(task_summary, args)
            if retry_reason and attempt_index < max_attempts - 1:
                retry_reasons.append(retry_reason)
                _archive_browser_use_attempt(output_dir, attempt_index + 1)
                delay_s = _browser_use_scenario_retry_delay(args, attempt_index)
                print(
                    "[runtime] Retrying scenario "
                    f"{scenario.scenario_id} after unsuccessful browser-use trace "
                    f"({attempt_index + 1}/{max_attempts - 1}) in {delay_s:.1f}s: {retry_reason}",
                    flush=True,
                )
                await asyncio.sleep(delay_s)
                continue
            if retry_reason:
                retry_reasons.append(retry_reason)
            return trace.model_copy(
                update={
                    "metadata": trace.metadata.model_copy(
                        update={
                            "scenario_attempts": attempt_index + 1,
                            "retry_reasons": retry_reasons,
                        }
                    )
                }
            )
        except asyncio.CancelledError:
            raise
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_transient_browser_use_error(exc) or attempt_index >= max_attempts - 1:
                raise
            retry_reasons.append(str(exc))
            _archive_browser_use_attempt(output_dir, attempt_index + 1)
            delay_s = _browser_use_scenario_retry_delay(args, attempt_index)
            print(
                "[runtime] Retrying scenario "
                f"{scenario.scenario_id} after transient browser-use error "
                f"({attempt_index + 1}/{max_attempts - 1}) in {delay_s:.1f}s: {exc}",
                flush=True,
            )
            await asyncio.sleep(delay_s)
    if last_exc is not None:  # pragma: no cover - defensive fallback
        raise last_exc
    raise RuntimeError(f"browser-use scenario {scenario.scenario_id} did not run")


def _browser_use_task_for_attempt(
    task: str,
    *,
    attempt_index: int,
    prior_retry_reason: str,
) -> str:
    if attempt_index <= 0:
        return task
    reason = str(prior_retry_reason or "the prior attempt did not reach the task goal").splitlines()[0]
    return (
        f"{task}\n\nRetry execution strategy:\n"
        f"- This is retry {attempt_index + 1}; the prior attempt failed because: {reason}.\n"
        "- Change the action strategy instead of repeating the failed sequence.\n"
        "- Re-inspect the current DOM and use visible element indices or accessible role/name targeting.\n"
        "- Never use jQuery-only selectors such as :contains(...).\n"
        "- Batch repeated keyboard presses when needed and reserve enough steps to verify the final state."
    )


def _browser_use_trace_retry_reason(task_summary: dict[str, Any], args: argparse.Namespace) -> str:
    if bool(task_summary.get("browser_task_success")):
        return ""
    errors = [str(error) for error in task_summary.get("browser_errors", []) if str(error).strip()]
    transient_errors = [error for error in errors if _is_transient_browser_use_error(RuntimeError(error))]
    if transient_errors:
        return f"{len(transient_errors)} transient provider/runtime error(s) and no successful done action"
    if bool(getattr(args, "browser_use_retry_goal_not_reached", True)):
        return "task goal was not reached"
    return ""


def _archive_browser_use_attempt(output_dir: Path, attempt_number: int) -> Path | None:
    if not output_dir.exists():
        return None
    archive_dir = output_dir.parent / "_scenario_attempts" / output_dir.name / f"attempt_{attempt_number}"
    if archive_dir.exists():
        shutil.rmtree(archive_dir)
    archive_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(output_dir), str(archive_dir))
    return archive_dir


def _browser_use_model_name(args: argparse.Namespace) -> str:
    explicit_browser_model = getattr(args, "browser_use_model", None)
    if explicit_browser_model:
        return str(explicit_browser_model).replace("models/", "")
    return resolve_model_name(getattr(args, "gemini_model", None))


def _browser_use_scenario_retry_delay(args: argparse.Namespace, attempt_index: int) -> float:
    base_s = float(getattr(args, "browser_use_scenario_retry_base_s", 15.0) or 0.0)
    max_s = float(getattr(args, "browser_use_scenario_retry_max_s", 120.0) or 0.0)
    delay_s = base_s * (2**attempt_index)
    if max_s > 0:
        delay_s = min(delay_s, max_s)
    return max(0.0, delay_s)


def _is_transient_browser_use_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    transient_markers = (
        "429",
        "500",
        "502",
        "503",
        "504",
        "unavailable",
        "resource_exhausted",
        "rate limit",
        "quota",
        "high demand",
        "temporarily",
        "timeout",
        "timed out",
        "deadline",
        "connection reset",
        "connectionerror",
    )
    return any(marker in text for marker in transient_markers)


def _baseline_max_steps_for_scenario(args: argparse.Namespace, scenario: DynamicBenchmarkScenario) -> int:
    baseline_max_steps = getattr(args, "baseline_max_steps", None)
    if baseline_max_steps is not None:
        return max(1, int(baseline_max_steps))
    browser_max_steps = getattr(args, "browser_max_steps", None)
    if browser_max_steps is not None:
        return max(1, int(browser_max_steps))
    return max(1, int(scenario.max_steps))


def _naive_baseline_skip_reason(
    args: argparse.Namespace,
    scenario: DynamicBenchmarkScenario,
    comparison_baseline: str,
) -> str | None:
    if comparison_baseline == "none":
        return "disabled by --comparison-baseline none"
    if bool(getattr(args, "dry_run", False)):
        return "skipped in dry-run mode"
    if str(getattr(args, "execution_mode", "fixture-trace")) != "browser-use":
        return "skipped in fixture-trace mode"
    if str(getattr(args, "scenario_set", "")) != "real":
        return "skipped for non-real scenario set"
    if scenario.interaction_type != "browser_use_task":
        return "skipped for non-browser-use scenario"
    target = str(scenario.target or "")
    if not (target.startswith("http://") or target.startswith("https://")):
        return "skipped for non-http target"
    return None


def build_fixture_runtime_trace(scenario: DynamicBenchmarkScenario) -> RuntimeTrace:
    """Build a deterministic runtime trace for a synthetic fixture scenario."""

    now = datetime.now(timezone.utc)
    state_builders = {
        "keyboard_trap_fail": _keyboard_trap_states,
        "missing_focus_indicator_fail": _missing_focus_indicator_states,
        "incorrect_focus_order_fail": _incorrect_focus_order_states,
        "modal_focus_escape_fail": _modal_focus_escape_states,
        "hover_only_content_fail": _hover_only_content_states,
        "status_message_without_live_region_fail": _status_message_states,
        "timeout_without_warning_fail": _timeout_states,
        "form_error_not_announced_fail": _form_error_states,
        "flashing_content_fail": _flashing_content_states,
        "pointer_cancellation_fail": _pointer_cancellation_fail_states,
        "drag_only_control_fail": _drag_only_control_fail_states,
        "media_captions_missing_fail": _media_captions_missing_states,
        "keyboard_navigation_pass": _keyboard_navigation_pass_states,
        "focus_indicator_pass": _focus_indicator_pass_states,
        "focus_order_pass": _focus_order_pass_states,
        "hover_dismissible_pass": _hover_dismissible_pass_states,
        "status_message_live_region_pass": _status_message_live_region_pass_states,
        "timeout_warning_pass": _timeout_warning_pass_states,
        "flashing_below_threshold_pass": _flashing_below_threshold_pass_states,
        "pointer_cancellation_pass": _pointer_cancellation_pass_states,
        "dragging_with_keyboard_alternative_pass": _dragging_with_keyboard_alternative_pass_states,
        "media_captions_present_pass": _media_captions_present_states,
    }
    builder = state_builders.get(scenario.scenario_id, _generic_states)
    states = builder(now, scenario)
    return RuntimeTrace(
        scenario=scenario.scenario_id,
        metadata=RuntimeTraceMetadata(
            start_time=now,
            end_time=states[-1].timestamp if states else now,
            duration_seconds=float(max(0, len(states) - 1)),
            artifacts_dir="deterministic_fixture_trace",
        ),
        steps=states,
    )


def _state(
    timestamp: datetime,
    scenario: DynamicBenchmarkScenario,
    step: int,
    *,
    dom_html: str = "",
    focused_element: dict[str, Any] | None = None,
    keyboard_events: list[dict[str, Any]] | None = None,
    dom_mutations: list[dict[str, Any]] | None = None,
    timing_info: dict[str, Any] | None = None,
    media_state: dict[str, Any] | None = None,
    screenshot_path: str | None = None,
    runtime_instrumentation: dict[str, Any] | None = None,
) -> RuntimeState:
    return RuntimeState(
        timestamp=timestamp,
        url=f"fixture://{scenario.scenario_id}/step-{step}",
        title=scenario.scenario_type or scenario.scenario_id,
        current_action={"step": step, "description": scenario.scripted_steps[min(step - 1, len(scenario.scripted_steps) - 1)]}
        if scenario.scripted_steps
        else {"step": step},
        dom_html=dom_html,
        focused_element=focused_element,
        keyboard_state={"events": keyboard_events or []},
        console_logs=[],
        screenshot_path=screenshot_path,
        dom_mutations=dom_mutations or [],
        timing_info=timing_info,
        media_state=media_state,
        interaction_metadata={
            "fixture_trace": True,
            "runtime_instrumentation": runtime_instrumentation or {},
        },
    )


def _focused(xpath: str, *, style: str = "", class_name: str = "", role: str = "button") -> dict[str, Any]:
    return {
        "xpath": xpath,
        "tag": "button",
        "role": role,
        "id": xpath.rsplit("/", 1)[-1].replace("[", "_").replace("]", ""),
        "attributes": {"style": style, "class": class_name},
    }


def _keyboard_trap_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html='<button id="close">Close</button>',
            focused_element=_focused("/html/body/dialog/button[1]", style="outline: 2px solid #111"),
            keyboard_events=[{"key": "Tab"}],
        ),
        _state(
            now,
            scenario,
            2,
            dom_html='<button id="close">Close</button>',
            focused_element=_focused("/html/body/dialog/button[1]", style="outline: 2px solid #111"),
            keyboard_events=[{"key": "Tab"}],
        ),
    ]


def _missing_focus_indicator_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    focused_element = {
        **_focused("/html/body/button[1]"),
        "computed_style": {
            "outline": "none",
            "outlineWidth": "0px",
            "outlineStyle": "none",
            "outlineColor": "rgba(0, 0, 0, 0)",
            "boxShadow": "none",
            "border": "0px none rgb(0, 0, 0)",
            "backgroundColor": "rgb(255, 255, 255)",
            "color": "rgb(0, 0, 0)",
        },
        "bounding_client_rect": {"x": 12, "y": 16, "width": 96, "height": 32},
        "viewport": {"width": 1280, "height": 720, "deviceScaleFactor": 1},
        "visible_focus_candidate": False,
    }
    return [
        _state(
            now,
            scenario,
            1,
            dom_html='<button class="primary">Continue</button>',
            focused_element=focused_element,
            keyboard_events=[{"key": "Tab"}],
            screenshot_path="fixture_missing_focus_indicator_step_1.png",
        )
    ]


def _incorrect_focus_order_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(now, scenario, 1, focused_element=_focused("/html/body/form/input[1]"), keyboard_events=[{"key": "Tab"}]),
        _state(now, scenario, 2, focused_element=_focused("/html/body/form/button[1]"), keyboard_events=[{"key": "Tab"}]),
        _state(now, scenario, 3, focused_element=_focused("/html/body/form/input[2]"), keyboard_events=[{"key": "Tab"}]),
    ]


def _modal_focus_escape_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html='<div role="dialog"><button>Save</button></div><a href="/background">Background</a>',
            focused_element=_focused("/html/body/div[@role='dialog']/button[1]"),
            keyboard_events=[{"key": "Tab"}],
        ),
        _state(
            now,
            scenario,
            2,
            dom_html='<div role="dialog"><button>Save</button></div><a href="/background">Background</a>',
            focused_element={"xpath": "/html/body/a[1]", "tag": "a", "role": "link", "attributes": {"style": ""}},
            keyboard_events=[{"key": "Tab"}],
        ),
    ]


def _hover_only_content_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html='<button aria-describedby="tip">Help</button><div class="tooltip">Only on hover</div>',
            focused_element=_focused("/html/body/button[1]", style="outline: 2px solid #111"),
            keyboard_events=[{"key": "Tab"}],
            screenshot_path="fixture_hover_only_step_1.png",
        )
    ]


def _status_message_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html='<form><button>Save</button></form><div id="message">Saved</div>',
            dom_mutations=[{"kind": "status", "target": "#message", "text": "Saved"}],
        )
    ]


def _timeout_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html="<main>Session will end automatically.</main>",
            timing_info={"deadline_seconds": 30, "user_extend_control": False},
        )
    ]


def _form_error_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html='<form><input required><button>Submit</button></form>',
            focused_element=_focused("/html/body/form/input[1]", style="outline: 2px solid #111"),
            keyboard_events=[{"key": "Tab"}],
        ),
        _state(
            now,
            scenario,
            2,
            dom_html=(
                '<form><input required aria-invalid="true" aria-describedby="email-error">'
                '<button>Submit</button><div id="email-error" role="alert">Required field missing</div></form>'
            ),
            focused_element=_focused("/html/body/form/input[1]", style="outline: 2px solid #111"),
            dom_mutations=[
                {
                    "mutation_type": "observed_child_list",
                    "added_text": "Required field missing",
                    "related_control": {
                        "target": {"tag": "input", "xpath": "/html/body/form[1]/input[1]"}
                    },
                    "related_form": {"xpath": "/html/body/form[1]"},
                }
            ],
            runtime_instrumentation={
                "submit_events": [
                    {
                        "step": 2,
                        "type": "submit",
                        "target": {"tag": "form", "xpath": "/html/body/form[1]"},
                        "form": {"xpath": "/html/body/form[1]"},
                    }
                ],
                "form_control_states": [
                    {
                        "step": 2,
                        "target": {
                            "tag": "input",
                            "xpath": "/html/body/form[1]/input[1]",
                            "aria_invalid": "true",
                        },
                        "form": {"xpath": "/html/body/form[1]"},
                        "invalid": True,
                        "error_feedback_present": True,
                        "help_present": False,
                        "suggestion_present": False,
                    }
                ],
            },
        ),
    ]


def _flashing_content_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html='<main><button>Show alert</button><div class="strobe flash flicker">Warning</div></main>',
            screenshot_path="fixture_flashing_content_step_1.png",
        )
    ]


def _pointer_cancellation_fail_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html='<div role="button" onpointerdown="activateImmediately()">Buy now</div>',
            runtime_instrumentation={
                "pointer_events": [
                    {
                        "step": 1,
                        "type": "pointerdown",
                        "pointer_type": "mouse",
                        "target": {"tag": "div", "role": "button", "xpath": "/html/body/div[1]"},
                    }
                ]
            },
        )
    ]


def _drag_only_control_fail_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html='<ul><li draggable="true" ondragstart="moveItem(event)">Quarterly report</li></ul>',
            runtime_instrumentation={
                "pointer_events": [
                    {
                        "step": 1,
                        "type": "dragstart",
                        "pointer_type": "mouse",
                        "target": {"tag": "li", "xpath": "/html/body/ul[1]/li[1]"},
                    }
                ]
            },
        )
    ]


def _media_captions_missing_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html="<video src='training.mp4'></video>",
            media_state={
                "media": [
                    {
                        "tag": "video",
                        "muted": False,
                        "paused": False,
                        "audible": True,
                        "visible": True,
                        "controls": True,
                        "current_time": 4.0,
                        "current_time_delta": 1.0,
                        "duration": 120,
                    }
                ],
                "media_events": [{"type": "timeupdate", "current_time": 4.0}],
            },
        )
    ]


def _keyboard_navigation_pass_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(now, scenario, 1, focused_element=_focused("/html/body/button[1]"), keyboard_events=[{"key": "Tab"}]),
        _state(now, scenario, 2, focused_element=_focused("/html/body/a[1]", role="link"), keyboard_events=[{"key": "Tab"}]),
        _state(now, scenario, 3, focused_element=_focused("/html/body/input[1]", role="textbox"), keyboard_events=[{"key": "Tab"}]),
    ]


def _focus_indicator_pass_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    focused = {
        **_focused("/html/body/button[1]", style="outline: 3px solid #005fcc"),
        "computed_style": {
            "outline": "3px solid rgb(0, 95, 204)",
            "outlineWidth": "3px",
            "outlineStyle": "solid",
            "outlineColor": "rgb(0, 95, 204)",
            "boxShadow": "none",
            "border": "0px none rgb(0, 0, 0)",
            "backgroundColor": "rgb(255, 255, 255)",
        },
        "bounding_client_rect": {"x": 12, "y": 16, "width": 96, "height": 32},
        "visible_focus_candidate": True,
        "focus_visible_match": True,
        "visible_focus_signals": {"outline": True},
    }
    return [
        _state(
            now,
            scenario,
            1,
            focused_element=focused,
            keyboard_events=[{"key": "Tab"}],
            screenshot_path="fixture_focus_indicator_pass_step_1.png",
        )
    ]


def _focus_order_pass_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(now, scenario, 1, focused_element=_focused("/html/body/form/input[1]"), keyboard_events=[{"key": "Tab"}]),
        _state(now, scenario, 2, focused_element=_focused("/html/body/form/input[2]"), keyboard_events=[{"key": "Tab"}]),
        _state(now, scenario, 3, focused_element=_focused("/html/body/form/button[1]"), keyboard_events=[{"key": "Tab"}]),
    ]


def _hover_dismissible_pass_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html='<button aria-describedby="tip">Help</button><div class="tooltip"><button>Close</button>Keyboard help</div>',
            focused_element=_focused("/html/body/button[1]", style="outline: 2px solid #111"),
            keyboard_events=[{"key": "Tab"}],
            screenshot_path="fixture_hover_dismissible_pass_step_1.png",
        )
    ]


def _status_message_live_region_pass_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html='<form><button>Save</button></form><div role="status" aria-live="polite">Saved</div>',
            dom_mutations=[{"kind": "status", "target": "#message", "text": "Saved", "aria-live": "polite"}],
        )
    ]


def _timeout_warning_pass_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html="<main>Your session timeout is approaching. <button>Extend session</button></main>",
            timing_info={"deadline_seconds": 120, "user_extend_control": True},
        )
    ]


def _flashing_below_threshold_pass_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html='<main><div class="safe-flash below-threshold">Two-flash notification</div></main>',
            screenshot_path="fixture_flashing_below_threshold_step_1.png",
        )
    ]


def _pointer_cancellation_pass_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html='<button onpointerdown="prepare()" onpointerup="activate()" onpointercancel="cancel()">Buy now</button>',
        )
    ]


def _dragging_with_keyboard_alternative_pass_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html=(
                '<ul><li draggable="true" ondragstart="moveItem(event)">Quarterly report</li></ul>'
                '<button class="keyboard-alternative">Move up</button><button>Move down</button>'
            ),
        )
    ]


def _media_captions_present_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [
        _state(
            now,
            scenario,
            1,
            dom_html='<video src="training.mp4"><track kind="captions" srclang="en" src="training.vtt"></video>',
            media_state={
                "media": [
                    {
                        "tag": "video",
                        "muted": False,
                        "paused": False,
                        "audible": True,
                        "visible": True,
                        "controls": True,
                        "current_time": 4.0,
                        "current_time_delta": 1.0,
                        "duration": 120,
                        "caption_tracks": [{"kind": "captions", "srclang": "en"}],
                    }
                ],
                "media_events": [{"type": "timeupdate", "current_time": 4.0}],
            },
        )
    ]


def _generic_states(now: datetime, scenario: DynamicBenchmarkScenario) -> list[RuntimeState]:
    return [_state(now, scenario, 1, dom_html="<main>Generic dynamic fixture</main>")]


def _run_status_for(trace: RuntimeTrace, report: DynamicWCAGReport) -> str:
    if _looks_like_challenge(trace):
        return "challenge_or_bot_page"
    if not trace.steps or not report.results:
        return "incomplete"
    task_summary = _browser_task_summary(trace)
    if task_summary["browser_task_failed_max_steps"] and not task_summary["browser_task_done"]:
        return "incomplete"
    return "completed"


def _scoring_status_for(
    *,
    scenario: DynamicBenchmarkScenario,
    trace: RuntimeTrace,
    report: DynamicWCAGReport,
    run_status: str,
    task_summary: dict[str, Any],
    execution_mode: str,
) -> dict[str, Any]:
    """Decide whether a scenario's criterion rows are eligible for primary metrics."""

    if run_status == "challenge_or_bot_page" or _looks_like_challenge(trace):
        return {"scored": False, "unscored_reason": "challenge_or_bot_page"}
    if run_status != "completed":
        return {"scored": False, "unscored_reason": run_status or "not_completed"}
    if not report.results:
        return {"scored": False, "unscored_reason": "no_formal_results"}
    if scenario.interaction_type == "browser_use_task" and execution_mode == "browser-use":
        if not bool(task_summary.get("browser_task_success")):
            if task_summary.get("browser_task_failed_max_steps"):
                return {"scored": False, "unscored_reason": "browser_task_failed_max_steps"}
            if task_summary.get("browser_task_failed"):
                return {"scored": False, "unscored_reason": "browser_task_failed"}
            return {"scored": False, "unscored_reason": "browser_task_goal_not_reached"}
    if scenario.interaction_type == "browser_use_task" and execution_mode != "browser-use":
        return {"scored": False, "unscored_reason": "non_browser_use_execution"}
    extraction_errors = _runtime_extraction_errors(trace)
    fatal_errors = [error for error in extraction_errors if not _is_nonfatal_runtime_extraction_error(error)]
    nonfatal_errors = [error for error in extraction_errors if _is_nonfatal_runtime_extraction_error(error)]
    if fatal_errors:
        return {
            "scored": False,
            "unscored_reason": "runtime_extraction_error",
            "runtime_extraction_errors": fatal_errors,
            "nonfatal_runtime_extraction_errors": nonfatal_errors,
        }
    return {
        "scored": True,
        "unscored_reason": "",
        "runtime_extraction_errors": [],
        "nonfatal_runtime_extraction_errors": nonfatal_errors,
    }


def _runtime_extraction_errors(trace: RuntimeTrace) -> list[str]:
    errors: list[str] = []
    for state in trace.steps:
        metadata = state.interaction_metadata or {}
        if not isinstance(metadata, dict):
            continue
        extraction_errors = metadata.get("extraction_errors", {})
        if isinstance(extraction_errors, dict):
            errors.extend(f"{key}:{value}" for key, value in extraction_errors.items())
    return errors


def _is_nonfatal_runtime_extraction_error(error: str) -> bool:
    """Return true for artifact-only failures that should not void the task."""

    error_text = str(error).strip().lower()
    if not error_text.startswith("screenshot:"):
        return False
    return any(
        marker in error_text
        for marker in (
            "page.capturescreenshot",
            "capture screenshot",
            "capturescreenshot",
            "screenshot",
        )
    )


def _browser_task_summary(trace: RuntimeTrace) -> dict[str, Any]:
    action_results = _browser_action_results(trace)
    browser_task_done = any(result.get("is_done") is True for result in action_results)
    browser_task_success = any(result.get("is_done") is True and result.get("success") is True for result in action_results)
    browser_task_failed = any(result.get("is_done") is True and result.get("success") is False for result in action_results)
    browser_errors = [str(result.get("error", "")) for result in action_results if result.get("error")]
    failed_max_steps = any("maximum steps" in error.lower() for error in browser_errors)
    return {
        "browser_task_done": browser_task_done,
        "browser_task_success": browser_task_success,
        "browser_task_failed": browser_task_failed,
        "browser_task_failed_max_steps": failed_max_steps,
        "browser_error_count": len(browser_errors),
        "browser_errors": browser_errors,
        "scenario_attempts": trace.metadata.scenario_attempts,
        "retry_reasons": list(trace.metadata.retry_reasons),
    }


def _browser_action_results(trace: RuntimeTrace) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for state in trace.steps:
        metadata = state.interaction_metadata or {}
        raw_results = metadata.get("action_results", [])
        if isinstance(raw_results, list):
            results.extend(item for item in raw_results if isinstance(item, dict))

    history = trace.metadata.browser_use_history or []
    for item in history:
        if not isinstance(item, dict):
            continue
        raw_results = item.get("result", [])
        if isinstance(raw_results, list):
            results.extend(result for result in raw_results if isinstance(result, dict))
    return results


def _looks_like_challenge(trace: RuntimeTrace) -> bool:
    combined = " ".join((state.dom_html or "") for state in trace.steps).lower()
    challenge_markers = [
        "bot challenge",
        "checking your browser",
        "complete the captcha",
        "security check",
        "select all images",
        "verify you are human",
    ]
    captcha_widgets = ["cf-turnstile", "g-recaptcha", "h-captcha", "recaptcha-checkbox"]
    return any(marker in combined for marker in [*challenge_markers, *captcha_widgets])


def _case_record(
    *,
    run_id: str,
    dataset: DynamicBenchmarkDataset,
    scenario: DynamicBenchmarkScenario,
    criteria: list[str],
    report: DynamicWCAGReport,
    evidence_bundle_steps: int,
    elapsed_s: float,
    run_status: str,
    task_summary: dict[str, Any],
    scoring: dict[str, Any],
    runtime_trace_path: Path,
    formal_report_path: Path,
) -> dict[str, Any]:
    statuses = {result.criterion_id: result.status.value for result in report.results}
    expected_statuses = {item.criterion_id: item.expected_status.value for item in scenario.expected_results}
    browser_errors = task_summary.get("browser_errors", [])
    browser_error_summary = "; ".join(str(error).splitlines()[0] for error in browser_errors[:3])
    retry_reasons = task_summary.get("retry_reasons", [])
    return {
        "run_id": run_id,
        "dataset_id": dataset.dataset_id,
        "scenario_id": scenario.scenario_id,
        "scenario_type": scenario.scenario_type,
        "target": scenario.target,
        "interaction_type": scenario.interaction_type,
        "task_description": scenario.task_description,
        "max_steps": scenario.max_steps,
        "baseline_kind": scenario.baseline_kind.value,
        "criteria_evaluated": ";".join(criteria),
        "criteria_expected": ";".join(scenario.criteria_expected),
        "expected_statuses": json.dumps(expected_statuses, sort_keys=True, ensure_ascii=True),
        "actual_statuses": json.dumps(statuses, sort_keys=True, ensure_ascii=True),
        "unsupported_criteria": ";".join(report.unsupported_criteria),
        "incomplete_count": sum(1 for result in report.results if result.status == DynamicStatus.incomplete),
        "unsupported_count": len(report.unsupported_criteria),
        "run_status": run_status,
        "scored": bool(scoring.get("scored")),
        "unscored_reason": str(scoring.get("unscored_reason") or ""),
        "browser_task_done": task_summary.get("browser_task_done", False),
        "browser_task_success": task_summary.get("browser_task_success", False),
        "browser_task_failed": task_summary.get("browser_task_failed", False),
        "browser_task_failed_max_steps": task_summary.get("browser_task_failed_max_steps", False),
        "browser_error_count": task_summary.get("browser_error_count", 0),
        "browser_error_summary": browser_error_summary,
        "scenario_attempts": task_summary.get("scenario_attempts", 1),
        "scenario_retry_count": max(0, int(task_summary.get("scenario_attempts", 1) or 1) - 1),
        "scenario_retry_reasons": "; ".join(str(reason).splitlines()[0] for reason in retry_reasons),
        "runtime_extraction_error_count": len(scoring.get("runtime_extraction_errors") or []),
        "runtime_extraction_errors": ";".join(str(error) for error in scoring.get("runtime_extraction_errors") or []),
        "nonfatal_runtime_extraction_error_count": len(scoring.get("nonfatal_runtime_extraction_errors") or []),
        "nonfatal_runtime_extraction_errors": ";".join(
            str(error) for error in scoring.get("nonfatal_runtime_extraction_errors") or []
        ),
        "llm_error_count": sum(1 for result in report.results if result.llm_error),
        "steps_count": evidence_bundle_steps,
        "total_time_s": elapsed_s,
        "runtime_trace_path": str(runtime_trace_path),
        "formal_report_path": str(formal_report_path),
        "errors": "",
    }


def _error_case_record(
    *,
    run_id: str,
    dataset: DynamicBenchmarkDataset,
    scenario: DynamicBenchmarkScenario,
    criteria: list[str],
    elapsed_s: float,
    error: Exception,
) -> dict[str, Any]:
    run_status = "blocked" if isinstance(error, BenchmarkBlockedError) else "extraction_error"
    return {
        "run_id": run_id,
        "dataset_id": dataset.dataset_id,
        "scenario_id": scenario.scenario_id,
        "scenario_type": scenario.scenario_type,
        "target": scenario.target,
        "interaction_type": scenario.interaction_type,
        "task_description": scenario.task_description,
        "max_steps": scenario.max_steps,
        "baseline_kind": scenario.baseline_kind.value,
        "criteria_evaluated": ";".join(criteria),
        "criteria_expected": ";".join(scenario.criteria_expected),
        "expected_statuses": "",
        "actual_statuses": "",
        "unsupported_criteria": "",
        "incomplete_count": 0,
        "unsupported_count": 0,
        "run_status": run_status,
        "scored": False,
        "unscored_reason": run_status,
        "browser_task_done": False,
        "browser_task_success": False,
        "browser_task_failed": False,
        "browser_task_failed_max_steps": False,
        "browser_error_count": 0,
        "browser_error_summary": "",
        "scenario_attempts": 0,
        "scenario_retry_count": 0,
        "scenario_retry_reasons": "",
        "runtime_extraction_error_count": 0,
        "runtime_extraction_errors": "",
        "nonfatal_runtime_extraction_error_count": 0,
        "nonfatal_runtime_extraction_errors": "",
        "llm_error_count": 0,
        "steps_count": 0,
        "total_time_s": elapsed_s,
        "runtime_trace_path": "",
        "formal_report_path": "",
        "errors": str(error),
    }


def _result_rows(
    *,
    run_id: str,
    dataset: DynamicBenchmarkDataset,
    scenario: DynamicBenchmarkScenario,
    report: DynamicWCAGReport,
    formal_report_path: Path,
    runtime_trace_path: Path,
    run_status: str,
    analyzer_baseline_id: str,
    artifacts: dict[str, dict[str, Any]] | None = None,
    scoring: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    expected_by_criterion = {result.criterion_id: result for result in scenario.expected_results}
    expected_label_baseline_id = _expected_label_baseline_id(scenario.baseline_kind.value)
    artifacts = artifacts or {}
    scoring = scoring or {"scored": run_status == "completed", "unscored_reason": ""}
    rows: list[dict[str, Any]] = []
    for result in report.results:
        expected = expected_by_criterion.get(result.criterion_id)
        expected_status = expected.expected_status.value if expected else ""
        artifact = artifacts.get(result.criterion_id, {})
        artifact_status = _artifact_status_metadata(artifact)
        fail_validation = result.fail_validation.model_dump(mode="json") if result.fail_validation else {}
        evidence_packet = result.evidence_packet.model_dump(mode="json") if result.evidence_packet else {}
        rows.append(
            {
                "run_id": run_id,
                "dataset_id": dataset.dataset_id,
                "scenario_id": scenario.scenario_id,
                "baseline_id": analyzer_baseline_id,
                "analyzer_baseline_id": analyzer_baseline_id,
                "expected_label_baseline_id": expected_label_baseline_id if expected else "",
                "baseline_kind": scenario.baseline_kind.value,
                "criterion_id": result.criterion_id,
                "criterion_name": result.criterion_name,
                "expected_status": expected_status,
                "status": result.status.value,
                "decision_level": result.decision_level or "",
                "matched_expected": bool(expected_status and expected_status == result.status.value),
                "summary": result.summary,
                "missing_evidence": ";".join(result.missing_evidence),
                "violations_count": len(result.violations),
                "evidence_refs": ";".join(ref for violation in result.violations for ref in violation.evidence_refs),
                "gate_decision": artifact.get("gate_decision", ""),
                "gate_reason": artifact.get("gate_reason", ""),
                "evidence_strength": artifact_status.get("evidence_strength", ""),
                "llm_status": artifact_status.get("llm_status", ""),
                "llm_verifier_status": artifact_status.get("llm_verifier_status", ""),
                "llm_verifier_error": artifact_status.get("llm_verifier_error", ""),
                "invalid_evidence_ref_count": artifact_status.get("invalid_evidence_ref_count", 0),
                "invalid_evidence_refs": artifact_status.get("invalid_evidence_refs", ""),
                "fail_validation_original_status": fail_validation.get("original_status", ""),
                "fail_validation_validated_status": fail_validation.get("validated_status", ""),
                "fail_validation_reason": fail_validation.get("reason", ""),
                "fail_validation_evidence_strength": fail_validation.get("evidence_strength", ""),
                "fail_validation_rule": fail_validation.get("rule", ""),
                "fail_validation_validator": fail_validation.get("validator", ""),
                "packet_criterion_family": evidence_packet.get("criterion_family", ""),
                "packet_applicability": evidence_packet.get("applicability", ""),
                "packet_applicability_reason": evidence_packet.get("applicability_reason", ""),
                "packet_evidence_strength": evidence_packet.get("evidence_strength", ""),
                "packet_step_consistency": evidence_packet.get("step_consistency", ""),
                "packet_positive_evidence": ";".join(str(value) for value in evidence_packet.get("positive_evidence", []))
                if isinstance(evidence_packet.get("positive_evidence"), list)
                else "",
                "packet_missing_evidence": ";".join(str(value) for value in evidence_packet.get("missing_evidence", []))
                if isinstance(evidence_packet.get("missing_evidence"), list)
                else "",
                "packet_contradictory_evidence": ";".join(
                    str(value) for value in evidence_packet.get("contradictory_evidence", [])
                )
                if isinstance(evidence_packet.get("contradictory_evidence"), list)
                else "",
                "requires_llm": result.requires_llm,
                "llm_error": result.llm_error or "",
                "run_status": run_status,
                "scored": bool(scoring.get("scored")),
                "unscored_reason": str(scoring.get("unscored_reason") or ""),
                "runtime_extraction_error_count": len(scoring.get("runtime_extraction_errors") or []),
                "runtime_extraction_errors": ";".join(
                    str(error) for error in scoring.get("runtime_extraction_errors") or []
                ),
                "nonfatal_runtime_extraction_error_count": len(
                    scoring.get("nonfatal_runtime_extraction_errors") or []
                ),
                "nonfatal_runtime_extraction_errors": ";".join(
                    str(error) for error in scoring.get("nonfatal_runtime_extraction_errors") or []
                ),
                "runtime_trace_path": str(runtime_trace_path),
                "formal_report_path": str(formal_report_path),
            }
        )
    for unsupported in report.unsupported_criteria:
        rows.append(
            {
                "run_id": run_id,
                "dataset_id": dataset.dataset_id,
                "scenario_id": scenario.scenario_id,
                "baseline_id": analyzer_baseline_id,
                "analyzer_baseline_id": analyzer_baseline_id,
                "expected_label_baseline_id": "",
                "baseline_kind": scenario.baseline_kind.value,
                "criterion_id": unsupported,
                "criterion_name": "",
                "expected_status": "",
                "status": "unsupported",
                "decision_level": "",
                "matched_expected": False,
                "summary": "Criterion is not supported by the dynamic analyzer.",
                "missing_evidence": "",
                "violations_count": 0,
                "evidence_refs": "",
                "gate_decision": "",
                "gate_reason": "",
                "evidence_strength": "",
                "llm_status": "",
                "llm_verifier_status": "",
                "llm_verifier_error": "",
                "invalid_evidence_ref_count": 0,
                "invalid_evidence_refs": "",
                "fail_validation_original_status": "",
                "fail_validation_validated_status": "",
                "fail_validation_reason": "",
                "fail_validation_evidence_strength": "",
                "fail_validation_rule": "",
                "fail_validation_validator": "",
                "packet_criterion_family": "",
                "packet_applicability": "",
                "packet_applicability_reason": "",
                "packet_evidence_strength": "",
                "packet_step_consistency": "",
                "packet_positive_evidence": "",
                "packet_missing_evidence": "",
                "packet_contradictory_evidence": "",
                "requires_llm": False,
                "llm_error": "",
                "run_status": run_status,
                "scored": bool(scoring.get("scored")),
                "unscored_reason": str(scoring.get("unscored_reason") or ""),
                "runtime_extraction_error_count": len(scoring.get("runtime_extraction_errors") or []),
                "runtime_extraction_errors": ";".join(
                    str(error) for error in scoring.get("runtime_extraction_errors") or []
                ),
                "nonfatal_runtime_extraction_error_count": len(
                    scoring.get("nonfatal_runtime_extraction_errors") or []
                ),
                "nonfatal_runtime_extraction_errors": ";".join(
                    str(error) for error in scoring.get("nonfatal_runtime_extraction_errors") or []
                ),
                "runtime_trace_path": str(runtime_trace_path),
                "formal_report_path": str(formal_report_path),
            }
        )
    return rows


def _artifact_status_metadata(artifact: dict[str, Any]) -> dict[str, Any]:
    quality = artifact.get("soft_evidence_quality")
    if not isinstance(quality, dict):
        quality = artifact.get("evidence", {}).get("evidence_quality_summary") if isinstance(artifact.get("evidence"), dict) else {}
    if not isinstance(quality, dict):
        quality = {}

    llm_payload = artifact.get("llm_response") if isinstance(artifact.get("llm_response"), dict) else {}
    parsed = llm_payload.get("parsed_response") if isinstance(llm_payload, dict) else {}
    verification = llm_payload.get("llm_verification") if isinstance(llm_payload, dict) else {}
    return {
        "evidence_strength": str(quality.get("evidence_strength") or ""),
        "llm_status": str(parsed.get("status") or "") if isinstance(parsed, dict) else "",
        "llm_verifier_status": "valid" if isinstance(verification, dict) and verification.get("valid") is True else (
            "invalid" if isinstance(verification, dict) and verification.get("valid") is False else ""
        ),
        "llm_verifier_error": str(verification.get("error_code") or "") if isinstance(verification, dict) else "",
        "invalid_evidence_ref_count": int(verification.get("invalid_evidence_ref_count") or 0)
        if isinstance(verification, dict)
        else 0,
        "invalid_evidence_refs": ";".join(str(ref) for ref in verification.get("invalid_evidence_refs", []))
        if isinstance(verification, dict) and isinstance(verification.get("invalid_evidence_refs"), list)
        else "",
    }


def _error_result_rows(
    *,
    run_id: str,
    dataset: DynamicBenchmarkDataset,
    scenario: DynamicBenchmarkScenario,
    criteria: list[str],
    run_status: str,
    analyzer_baseline_id: str,
    error: Exception,
) -> list[dict[str, Any]]:
    expected_by_criterion = {result.criterion_id: result for result in scenario.expected_results}
    expected_label_baseline_id = _expected_label_baseline_id(scenario.baseline_kind.value)
    reason = run_status or ("blocked" if isinstance(error, BenchmarkBlockedError) else "extraction_error")
    rows: list[dict[str, Any]] = []
    for criterion_id in criteria:
        criterion = DYNAMIC_WCAG_REGISTRY.get(criterion_id) if DYNAMIC_WCAG_REGISTRY.is_supported(criterion_id) else None
        expected = expected_by_criterion.get(criterion_id)
        rows.append(
            {
                "run_id": run_id,
                "dataset_id": dataset.dataset_id,
                "scenario_id": scenario.scenario_id,
                "baseline_id": analyzer_baseline_id,
                "analyzer_baseline_id": analyzer_baseline_id,
                "expected_label_baseline_id": expected_label_baseline_id if expected else "",
                "baseline_kind": scenario.baseline_kind.value,
                "criterion_id": criterion_id,
                "criterion_name": criterion.name if criterion else "",
                "expected_status": expected.expected_status.value if expected else "",
                "status": "incomplete" if criterion else "unsupported",
                "decision_level": "insufficient_evidence" if criterion else "",
                "matched_expected": False,
                "summary": f"Scenario was attempted but not scored because the run ended as {reason}.",
                "missing_evidence": reason,
                "violations_count": 0,
                "evidence_refs": "",
                "gate_decision": "incomplete" if criterion else "",
                "gate_reason": str(error),
                "evidence_strength": "",
                "llm_status": "",
                "llm_verifier_status": "",
                "llm_verifier_error": "",
                "invalid_evidence_ref_count": 0,
                "invalid_evidence_refs": "",
                "fail_validation_original_status": "",
                "fail_validation_validated_status": "",
                "fail_validation_reason": "",
                "fail_validation_evidence_strength": "",
                "fail_validation_rule": "",
                "fail_validation_validator": "",
                "packet_criterion_family": "",
                "packet_applicability": "",
                "packet_applicability_reason": "",
                "packet_evidence_strength": "",
                "packet_step_consistency": "",
                "packet_positive_evidence": "",
                "packet_missing_evidence": "",
                "packet_contradictory_evidence": "",
                "requires_llm": bool(criterion.requires_llm) if criterion else False,
                "llm_error": "",
                "run_status": run_status,
                "scored": False,
                "unscored_reason": reason,
                "runtime_extraction_error_count": 0,
                "runtime_extraction_errors": "",
                "nonfatal_runtime_extraction_error_count": 0,
                "nonfatal_runtime_extraction_errors": "",
                "runtime_trace_path": "",
                "formal_report_path": "",
            }
        )
    return rows


def _criteria_support_rows(
    dataset: DynamicBenchmarkDataset,
    report_rows: list[dict[str, Any]],
    unsupported_criteria: set[str],
    *,
    oracle_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected = {str(row["criterion_id"]) for row in oracle_rows}
    evaluated = {str(row.get("criterion_id")) for row in report_rows if row.get("status") != "unsupported"}
    rows: list[dict[str, Any]] = []
    for criterion_id in sorted(set(DYNAMIC_WCAG_REGISTRY.supported_ids()) | expected | unsupported_criteria):
        supported = DYNAMIC_WCAG_REGISTRY.is_supported(criterion_id)
        criterion = DYNAMIC_WCAG_REGISTRY.get(criterion_id) if supported else None
        rows.append(
            {
                "criterion_id": criterion_id,
                "criterion_name": criterion.name if criterion else "",
                "level": criterion.level.value if criterion else "",
                "probe": criterion.probe if criterion else "",
                "requires_llm": criterion.requires_llm if criterion else False,
                "requires_runtime_evidence": supported,
                "in_dynamic_registry": supported,
                "in_fixture_expectations": criterion_id in expected,
                "evaluated": criterion_id in evaluated,
                "unsupported_requested": criterion_id in unsupported_criteria,
            }
        )
    return rows


def _coverage_rows(
    dataset: DynamicBenchmarkDataset,
    report_rows: list[dict[str, Any]],
    unsupported_criteria: set[str],
    *,
    oracle_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected = {str(row["criterion_id"]) for row in oracle_rows}
    statuses_by_criterion: dict[str, list[str]] = {}
    for row in report_rows:
        statuses_by_criterion.setdefault(str(row.get("criterion_id")), []).append(str(row.get("status")))

    rows: list[dict[str, Any]] = []
    for criterion_id in sorted(set(DYNAMIC_WCAG_REGISTRY.supported_ids()) | expected | unsupported_criteria):
        statuses = statuses_by_criterion.get(criterion_id, [])
        supported = DYNAMIC_WCAG_REGISTRY.is_supported(criterion_id)
        criterion = DYNAMIC_WCAG_REGISTRY.get(criterion_id) if supported else None
        rows.append(
            {
                "criterion_id": criterion_id,
                "criterion_name": criterion.name if criterion else "",
                "probe": criterion.probe if criterion else "",
                "supported": supported,
                "expected_in_dataset": criterion_id in expected,
                "evaluated_count": len(statuses),
                "fail_count": statuses.count("fail"),
                "warning_count": statuses.count("warning"),
                "pass_count": statuses.count("pass"),
                "not_applicable_count": statuses.count("not_applicable"),
                "incomplete_count": statuses.count("incomplete"),
                "unsupported_count": statuses.count("unsupported"),
                "coverage": bool(statuses and supported),
            }
        )
    return rows


def _recall_rows(recall: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [{"criterion_id": "overall", **recall.get("overall", {})}]
    for criterion_id, values in sorted(recall.get("by_criterion", {}).items()):
        rows.append({"criterion_id": criterion_id, **values})
    return rows


def _precision_rows(precision: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [{"criterion_id": "overall", **precision.get("overall", {})}]
    for criterion_id, values in sorted(precision.get("by_criterion", {}).items()):
        rows.append({"criterion_id": criterion_id, **values})
    return rows


def _variability_rows(case_records: list[dict[str, Any]], report_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    statuses = sorted({str(row.get("status")) for row in report_rows})
    row: dict[str, Any] = {
        "run_id": "run_1",
        "scenarios": len(case_records),
        "records": len(report_rows),
    }
    for status in statuses:
        row[f"{status}_count"] = sum(1 for result in report_rows if result.get("status") == status)
    return [row]


def _evidence_quality_rows(
    *,
    run_id: str,
    dataset: DynamicBenchmarkDataset,
    scenario: DynamicBenchmarkScenario,
    report: DynamicWCAGReport,
    artifacts: dict[str, dict[str, Any]],
    analyzer_baseline_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in report.results:
        artifact = artifacts.get(result.criterion_id, {})
        quality = artifact.get("soft_evidence_quality") or artifact.get("evidence", {}).get("evidence_quality_summary") or {}
        if not isinstance(quality, dict):
            quality = {}
        decisive = quality.get("decisive_evidence_refs") if isinstance(quality.get("decisive_evidence_refs"), list) else []
        candidates = quality.get("candidate_evidence_refs") if isinstance(quality.get("candidate_evidence_refs"), list) else []
        weak = quality.get("weak_signal_refs") if isinstance(quality.get("weak_signal_refs"), list) else []
        reasons = quality.get("reasons") if isinstance(quality.get("reasons"), list) else []
        rows.append(
            {
                "run_id": run_id,
                "dataset_id": dataset.dataset_id,
                "baseline_id": analyzer_baseline_id,
                "scenario_id": scenario.scenario_id,
                "criterion_id": result.criterion_id,
                "status": result.status.value,
                "applicability": quality.get("applicability", ""),
                "evidence_strength": quality.get("evidence_strength", ""),
                "decisive_count": len(decisive),
                "candidate_count": len(candidates),
                "weak_count": len(weak),
                "decisive_evidence_refs": decisive,
                "candidate_evidence_refs": candidates,
                "weak_signal_refs": weak,
                "recommended_status_floor": quality.get("recommended_status_floor", ""),
                "recommended_status_ceiling": quality.get("recommended_status_ceiling", ""),
                "reasons": reasons,
            }
        )
    return rows


def _quality_by_status_rows(quality_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str]] = Counter()
    for record in quality_records:
        strength = str(record.get("evidence_strength") or "unknown")
        status = str(record.get("status") or "unknown")
        counts[(strength, status)] += 1
    return [
        {"evidence_strength": strength, "status": status, "count": count}
        for (strength, status), count in sorted(counts.items())
    ]


def _quality_counts_summary(quality_records: list[dict[str, Any]]) -> dict[str, Any]:
    by_strength: Counter[str] = Counter()
    by_strength_status: Counter[tuple[str, str]] = Counter()
    for record in quality_records:
        strength = str(record.get("evidence_strength") or "unknown")
        status = str(record.get("status") or "unknown")
        by_strength[strength] += 1
        by_strength_status[(strength, status)] += 1
    return {
        "by_strength": dict(sorted(by_strength.items())),
        "by_strength_and_status": {
            f"{strength}:{status}": count for (strength, status), count in sorted(by_strength_status.items())
        },
    }


def _primary_scored_report_rows(
    report_rows: list[dict[str, Any]],
    complete_oracle_scenario_ids: set[str],
) -> list[dict[str, Any]]:
    return [
        row
        for row in report_rows
        if _is_truthy(row.get("scored")) and str(row.get("scenario_id", "")) in complete_oracle_scenario_ids
    ]


def _oracle_rows_for_report_keys(
    oracle_rows: list[dict[str, Any]],
    report_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    keys = {
        (str(row.get("scenario_id", "")), str(row.get("criterion_id", "")))
        for row in report_rows
        if row.get("scenario_id") and row.get("criterion_id")
    }
    return [
        row
        for row in oracle_rows
        if (str(row.get("scenario_id", "")), str(row.get("criterion_id", ""))) in keys
    ]


def _unscored_reason_counts(report_rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in report_rows:
        if _is_truthy(row.get("scored")):
            continue
        reason = str(row.get("unscored_reason") or "unknown")
        counts[reason] += 1
    return dict(sorted(counts.items()))


def _case_runtime_extraction_error_counts(case_records: list[dict[str, Any]]) -> dict[str, Any]:
    return _case_error_counts(case_records, "runtime_extraction_errors")


def _case_nonfatal_runtime_extraction_error_counts(case_records: list[dict[str, Any]]) -> dict[str, Any]:
    return _case_error_counts(case_records, "nonfatal_runtime_extraction_errors")


def _case_error_counts(case_records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    total = 0
    by_scenario: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    for record in case_records:
        scenario_id = str(record.get("scenario_id", ""))
        raw_errors = [item for item in str(record.get(field) or "").split(";") if item]
        for error in raw_errors:
            total += 1
            by_scenario[scenario_id] += 1
            by_source[_runtime_extraction_error_source(error)] += 1
    return {
        "total": total,
        "by_scenario": dict(sorted(by_scenario.items())),
        "by_source": dict(sorted(by_source.items())),
    }


def _runtime_extraction_error_source(error: str) -> str:
    source, separator, _ = str(error).partition(":")
    return source if separator else "unknown"


def _invalid_evidence_ref_rows(report_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in report_rows:
        raw_refs = str(row.get("invalid_evidence_refs") or "")
        count = int(row.get("invalid_evidence_ref_count") or 0)
        if count <= 0 and not raw_refs:
            continue
        refs = [ref for ref in raw_refs.split(";") if ref]
        if not refs:
            refs = [""]
        for ref in refs:
            rows.append(
                {
                    "scenario_id": row.get("scenario_id", ""),
                    "criterion_id": row.get("criterion_id", ""),
                    "status": row.get("status", ""),
                    "llm_error": row.get("llm_error", ""),
                    "llm_verifier_error": row.get("llm_verifier_error", ""),
                    "invalid_evidence_ref": ref,
                    "invalid_evidence_ref_count": count,
                    "scored": row.get("scored", ""),
                    "unscored_reason": row.get("unscored_reason", ""),
                    "runtime_trace_path": row.get("runtime_trace_path", ""),
                    "formal_report_path": row.get("formal_report_path", ""),
                }
            )
    return rows


def _invalid_evidence_ref_counts(report_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_scenario: Counter[str] = Counter()
    by_criterion: Counter[str] = Counter()
    total = 0
    for row in report_rows:
        count = int(row.get("invalid_evidence_ref_count") or 0)
        if count <= 0:
            continue
        total += count
        by_scenario[str(row.get("scenario_id", ""))] += count
        by_criterion[str(row.get("criterion_id", ""))] += count
    return {
        "total_invalid_refs": total,
        "by_scenario": dict(sorted(by_scenario.items())),
        "by_criterion": dict(sorted(by_criterion.items())),
    }


def _scoring_audit_rows(
    dataset: DynamicBenchmarkDataset,
    report_rows: list[dict[str, Any]],
    *,
    oracle_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected_by_key = {
        (str(row.get("scenario_id", "")), str(row.get("criterion_id", ""))): str(row.get("expected_status", ""))
        for row in oracle_rows
    }
    rows: list[dict[str, Any]] = []
    for row in sorted(report_rows, key=lambda item: (str(item.get("scenario_id", "")), str(item.get("criterion_id", "")))):
        key = (str(row.get("scenario_id", "")), str(row.get("criterion_id", "")))
        rows.append(
            {
                "dataset_id": dataset.dataset_id,
                "scenario_id": key[0],
                "criterion_id": key[1],
                "oracle_status": expected_by_key.get(key, ""),
                "analyzer_status": row.get("status", ""),
                "decision_level": row.get("decision_level", ""),
                "scored": row.get("scored", ""),
                "unscored_reason": row.get("unscored_reason", ""),
                "run_status": row.get("run_status", ""),
                "evidence_strength": row.get("evidence_strength", ""),
                "fail_validation_original_status": row.get("fail_validation_original_status", ""),
                "fail_validation_validated_status": row.get("fail_validation_validated_status", ""),
                "fail_validation_reason": row.get("fail_validation_reason", ""),
                "fail_validation_evidence_strength": row.get("fail_validation_evidence_strength", ""),
                "fail_validation_rule": row.get("fail_validation_rule", ""),
                "packet_criterion_family": row.get("packet_criterion_family", ""),
                "packet_applicability": row.get("packet_applicability", ""),
                "packet_applicability_reason": row.get("packet_applicability_reason", ""),
                "packet_evidence_strength": row.get("packet_evidence_strength", ""),
                "packet_step_consistency": row.get("packet_step_consistency", ""),
                "packet_positive_evidence": row.get("packet_positive_evidence", ""),
                "packet_missing_evidence": row.get("packet_missing_evidence", ""),
                "packet_contradictory_evidence": row.get("packet_contradictory_evidence", ""),
                "evidence_refs": row.get("evidence_refs", ""),
                "invalid_evidence_ref_count": row.get("invalid_evidence_ref_count", 0),
                "llm_error": row.get("llm_error", ""),
                "runtime_extraction_error_count": row.get("runtime_extraction_error_count", 0),
                "runtime_extraction_errors": row.get("runtime_extraction_errors", ""),
                "nonfatal_runtime_extraction_error_count": row.get("nonfatal_runtime_extraction_error_count", 0),
                "nonfatal_runtime_extraction_errors": row.get("nonfatal_runtime_extraction_errors", ""),
                "runtime_trace_path": row.get("runtime_trace_path", ""),
                "formal_report_path": row.get("formal_report_path", ""),
            }
        )
    return rows


def _error_analysis_rows(
    false_positive_rows: list[dict[str, Any]],
    false_negative_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: Counter[tuple[str, str, str, str, str, str]] = Counter()
    for audit_type, rows in (("false_positive", false_positive_rows), ("false_negative", false_negative_rows)):
        for row in rows:
            grouped[
                (
                    audit_type,
                    str(row.get("criterion_id", "")),
                    str(row.get("target_status", "")),
                    str(row.get("evidence_strength", "")),
                    str(row.get("llm_error", "")),
                    str(row.get("scenario_id", "")),
                )
            ] += 1
    return [
        {
            "audit_type": audit_type,
            "criterion_id": criterion_id,
            "target_status": target_status,
            "probe_family": _probe_family_for_criterion(criterion_id),
            "evidence_strength": evidence_strength,
            "llm_error": llm_error,
            "scenario_id": scenario_id,
            "count": count,
        }
        for (audit_type, criterion_id, target_status, evidence_strength, llm_error, scenario_id), count in sorted(
            grouped.items(), key=lambda item: (-item[1], item[0])
        )
    ]


def _probe_family_for_criterion(criterion_id: str) -> str:
    if not DYNAMIC_WCAG_REGISTRY.is_supported(criterion_id):
        return ""
    return DYNAMIC_WCAG_REGISTRY.get(criterion_id).probe


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def _task_success_summary(case_records: list[dict[str, Any]]) -> dict[str, Any]:
    browser_task_rows = [
        record for record in case_records if str(record.get("interaction_type")) == "browser_use_task"
    ]
    denominator = len(browser_task_rows)
    success_count = sum(
        1
        for record in browser_task_rows
        if str(record.get("browser_task_success")).lower() == "true" or record.get("browser_task_success") is True
    )
    completed_count = sum(1 for record in browser_task_rows if str(record.get("run_status")) == "completed")
    challenge_count = sum(1 for record in browser_task_rows if str(record.get("run_status")) == "challenge_or_bot_page")
    return {
        "task_success_count": success_count,
        "task_success_denominator": denominator,
        "task_success_rate": success_count / denominator if denominator else None,
        "browser_task_completed_count": completed_count,
        "browser_task_blocked_or_failed_count": denominator - completed_count,
        "challenge_or_bot_count": challenge_count,
    }


def _model_call_verification_rows(model_call_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str]] = Counter()
    for record in model_call_records:
        status = str(record.get("verifier_status") or "unverified")
        error = str(record.get("verifier_error") or "")
        counts[(status, error)] += 1

    if not counts:
        return []

    rows = [
        {
            "verifier_status": status,
            "verifier_error": error,
            "invalid_llm_result": status == "invalid",
            "count": count,
        }
        for (status, error), count in sorted(counts.items())
    ]
    rows.append(
        {
            "verifier_status": "total",
            "verifier_error": "",
            "invalid_llm_result": "",
            "count": sum(counts.values()),
        }
    )
    return rows


def _ablation_rows(
    dataset: DynamicBenchmarkDataset,
    reports_by_baseline: dict[str, list[dict[str, Any]]],
    primary_baseline_id: str,
    judge_mode: str,
    oracle_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for baseline_id, report_rows in sorted(reports_by_baseline.items()):
        recall = compute_criterion_recall(dataset, report_rows, oracle_rows=oracle_rows)
        precision = compute_criterion_precision(dataset, report_rows, oracle_rows=oracle_rows)
        recall_overall = recall.get("overall", {})
        precision_overall = precision.get("overall", {})
        if baseline_id == primary_baseline_id:
            variant = f"formal_analyzer_{judge_mode}_judge"
        elif baseline_id == NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id:
            variant = "naive_browser_use_global_audit"
        elif baseline_id == MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id:
            variant = "matched_global_evidence_ablation"
        else:
            variant = baseline_id
        rows.append(
            {
                "baseline_id": baseline_id,
                "variant": variant,
                "status": "completed",
                "expected": recall_overall.get("expected", 0),
                "detected": recall_overall.get("detected", 0),
                "missed": recall_overall.get("missed", 0),
                "recall": recall_overall.get("recall"),
                "labelled_predictions": precision_overall.get("labelled_predictions", 0),
                "correct": precision_overall.get("correct", 0),
                "precision": precision_overall.get("precision"),
            }
        )
    return rows


def _baseline_pairwise_rows(
    dataset: DynamicBenchmarkDataset,
    reports_by_baseline: dict[str, list[dict[str, Any]]],
    *,
    primary_baseline_id: str,
    comparison_baseline_id: str,
    oracle_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if primary_baseline_id == comparison_baseline_id:
        return []
    if primary_baseline_id not in reports_by_baseline or comparison_baseline_id not in reports_by_baseline:
        return []

    expected_by_key = {
        (row["scenario_id"], row["criterion_id"]): row["expected_status"]
        for row in oracle_rows
    }
    primary_by_key = _status_by_key(reports_by_baseline[primary_baseline_id])
    comparison_by_key = _status_by_key(reports_by_baseline[comparison_baseline_id])
    keys = sorted(expected_by_key)

    return [
        {
            "dataset_id": dataset.dataset_id,
            "scenario_id": scenario_id,
            "criterion_id": criterion_id,
            "expected_status": expected_by_key.get((scenario_id, criterion_id), ""),
            "primary_baseline_id": primary_baseline_id,
            "primary_status": primary_by_key.get((scenario_id, criterion_id), DynamicStatus.incomplete.value),
            "comparison_baseline_id": comparison_baseline_id,
            "comparison_status": comparison_by_key.get((scenario_id, criterion_id), DynamicStatus.incomplete.value),
            "primary_assessed": (scenario_id, criterion_id) in primary_by_key,
            "comparison_assessed": (scenario_id, criterion_id) in comparison_by_key,
            "status_match": primary_by_key.get((scenario_id, criterion_id), DynamicStatus.incomplete.value)
            == comparison_by_key.get((scenario_id, criterion_id), DynamicStatus.incomplete.value),
        }
        for scenario_id, criterion_id in keys
    ]


def _status_by_key(report_rows: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    output: dict[tuple[str, str], str] = {}
    for row in report_rows:
        scenario_id = str(row.get("scenario_id", ""))
        criterion_id = str(row.get("criterion_id", ""))
        if scenario_id and criterion_id:
            output[(scenario_id, criterion_id)] = str(row.get("status", ""))
    return output


def _performance_rows(
    case_records: list[dict[str, Any]],
    model_summary: dict[str, Any],
    model_cost_summary: dict[str, Any],
    judge_mode: str,
) -> list[dict[str, Any]]:
    total_time = sum(float(record.get("total_time_s") or 0.0) for record in case_records)
    uses_browser_use = any(str(record.get("interaction_type")) == "browser_use_task" for record in case_records)
    if judge_mode == "rules":
        note = (
            "Browser-use scenario orchestration model calls are not metered here; formal dynamic judge used rules."
            if uses_browser_use
            else "Deterministic fixture traces use no formal judge model calls in rules mode."
        )
    else:
        note = "Formal dynamic WCAG judge model calls are metered; browser-use orchestration calls are separate."
    return [
        {
            "run_id": "run_1",
            "scenarios": len(case_records),
            "total_time_s": round(total_time, 6),
            "avg_time_s": round(total_time / len(case_records), 6) if case_records else 0.0,
            "model": model_summary.get("model", ""),
            "model_calls": model_cost_summary.get("model_calls", 0),
            "api_calls": model_cost_summary.get("api_calls", 0),
            "cache_hits": model_cost_summary.get("cache_hits", 0),
            "prompt_tokens": model_cost_summary.get("prompt_tokens", 0),
            "output_tokens": model_cost_summary.get("output_tokens", 0),
            "total_tokens": model_cost_summary.get("total_tokens", 0),
            "estimated_cost": model_cost_summary.get("estimated_cost"),
            "currency": model_cost_summary.get("estimated_cost_currency", "USD"),
            "cost_available": model_cost_summary.get("cost_available", False),
            "note": note,
        }
    ]


def _baseline_status_rows(
    judge_mode: str,
    completed_baseline_ids: set[str],
    dataset: DynamicBenchmarkDataset,
    *,
    comparison_baseline: str,
    comparison_skip_reasons: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    primary_baseline_id = _analyzer_baseline_id(judge_mode)
    status_spec_ids = {
        SYNTHETIC_FIXTURE_BASELINE.baseline_id,
        HUMAN_ORACLE_BASELINE.baseline_id,
        primary_baseline_id,
    }
    if comparison_baseline in {
        NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id,
        MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id,
        MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id,
    }:
        status_spec_ids.add(comparison_baseline)

    for spec in get_baseline_specs():
        if spec.baseline_id not in status_spec_ids:
            continue
        if spec.baseline_id in completed_baseline_ids:
            status = "completed"
            available = True
            if spec.baseline_id == RULES_DYNAMIC_WCAG_BASELINE.baseline_id:
                reason = "computed from FLOW-A11y deterministic dynamic WCAG rules judge"
            elif spec.baseline_id == GEMINI_DYNAMIC_WCAG_BASELINE.baseline_id:
                reason = "computed from formal analyzer Gemini dynamic WCAG prompts"
            elif spec.baseline_id == NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id:
                reason = "completed single generic browser-use accessibility audit"
            elif spec.baseline_id == MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id:
                reason = "completed generic WCAG judgments over the same runtime trace as FlowA11y"
            elif spec.baseline_id == MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id:
                reason = "completed matched global-evidence ablation over the same runtime trace as FlowA11y"
            else:
                reason = "computed from formal analyzer hybrid Gemini/rules judge"
        elif spec.baseline_id == SYNTHETIC_FIXTURE_BASELINE.baseline_id:
            status = "available"
            available = True
            reason = "authored synthetic fixture oracle is available as D_dyn_fixture"
        elif spec.baseline_id == HUMAN_ORACLE_BASELINE.baseline_id:
            available = dataset.baseline_kind.value == "human_oracle"
            status = "available" if available else "skipped"
            reason = (
                "human oracle labels are loaded from the external real oracle file for metrics"
                if available
                else "current dataset is not human-labelled real scenarios"
            )
        elif spec.baseline_id == primary_baseline_id:
            status = "skipped"
            available = False
            reason = "no completed normalized report rows"
        elif spec.baseline_id == NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id:
            status = "skipped"
            available = False
            reason = "; ".join(comparison_skip_reasons) or "no eligible completed real browser-use http(s) scenarios"
        elif spec.baseline_id == MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id:
            status = "skipped"
            available = False
            reason = "; ".join(comparison_skip_reasons) or "no completed runtime traces were available"
        elif spec.baseline_id == MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id:
            status = "skipped"
            available = False
            reason = "; ".join(comparison_skip_reasons) or "no completed runtime traces were available"
        elif spec.command is None and spec.optional:
            status = "skipped"
            available = False
            reason = "optional placeholder baseline"
        elif spec.command is None:
            status = "available"
            available = True
            reason = "metadata baseline"
        else:
            continue
        rows.append(
            {
                "baseline_id": spec.baseline_id,
                "name": spec.name,
                "kind": spec.kind.value,
                "status": status,
                "available": available,
                "reason": reason,
            }
        )
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in fieldnames})


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, tuple, set)):
        return ";".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, ensure_ascii=True)
    return value


if __name__ == "__main__":
    raise SystemExit(main())
