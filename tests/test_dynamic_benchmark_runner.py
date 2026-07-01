from __future__ import annotations

import asyncio
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from benchmarking.dynamic.notebook_support import resolve_dynamic_results_dir, safe_read_csv
from benchmarking.dynamic.apply_manual_labels import write_external_oracle_from_labels
from benchmarking.dynamic.rescore_dynamic_results import rescore_dynamic_results
from benchmarking.dynamic.run_dynamic_benchmark import (
    BrowserUseAvailability,
    build_preflight_for_args,
    build_fixture_runtime_trace,
    run_dynamic_benchmark,
    _browser_use_task_for_attempt,
    _run_browser_use_scenario,
)
from benchmarking.dynamic_baselines import DynamicBaselineKind
from benchmarking.dynamic_datasets import DynamicBenchmarkScenario, load_synthetic_dynamic_fixture_metadata
from dynamic.preprocessors.wcag_mapper import DYNAMIC_WCAG_REGISTRY
from dynamic.schemas.runtime_state import RuntimeState
from dynamic.schemas.runtime_trace import RuntimeTrace, RuntimeTraceMetadata
from dynamic.schemas.wcag_dynamic import DynamicCriterionResult, DynamicStatus, DynamicWCAGReport


def test_dynamic_dry_run_writes_tables_and_artifacts(tmp_path: Path) -> None:
    args = SimpleNamespace(
        scenario_set="fixture",
        scenario_id=[],
        criteria=[],
        max_scenarios=2,
        dry_run=True,
        execution_mode="fixture-trace",
        output_dir=tmp_path / "dynamic_results",
    )
    stale_dir = args.output_dir / "raw" / "run_1" / "stale_previous_scenario"
    stale_dir.mkdir(parents=True)
    (stale_dir / "scenario_result.json").write_text("{}", encoding="utf-8")

    summary = asyncio.run(run_dynamic_benchmark(args))

    assert summary["scenario_set"] == "fixture"
    assert summary["case_records"] == 2
    assert summary["available_scenarios"] >= 2
    assert summary["selected_scenario_ids"] == ["keyboard_trap_fail", "missing_focus_indicator_fail"]
    assert summary["max_scenarios"] == 2
    assert not stale_dir.exists()
    tables_dir = args.output_dir / "tables"
    raw_dir = args.output_dir / "raw" / "run_1"

    expected_tables = {
        "dynamic_case_records.csv",
        "dynamic_finding_status_records.csv",
        "dynamic_recall.csv",
        "dynamic_precision.csv",
        "dynamic_coverage.csv",
        "dynamic_evidence_quality.csv",
        "dynamic_quality_by_status.csv",
        "dynamic_quality_summary.csv",
        "dynamic_false_positive_audit.csv",
        "dynamic_false_negative_audit.csv",
        "dynamic_error_analysis.csv",
        "dynamic_scoring_audit.csv",
        "invalid_evidence_refs.csv",
        "dynamic_variability.csv",
        "dynamic_ablation.csv",
        "dynamic_performance_cost.csv",
        "status_confusion.csv",
        "attempted_status_confusion.csv",
        "baseline_comparison.csv",
        "attempted_baseline_comparison.csv",
        "baseline_finding_status_records.csv",
        "baseline_pairwise_comparison.csv",
        "attempted_baseline_pairwise_comparison.csv",
        "baseline_status.csv",
        "unsupported_incomplete_counts.csv",
        "criteria_support_matrix.csv",
        "attempted_dynamic_recall.csv",
        "attempted_dynamic_precision.csv",
        "attempted_dynamic_quality_summary.csv",
    }
    assert expected_tables.issubset({path.name for path in tables_dir.iterdir()})
    assert (args.output_dir / "manual_label_queue.csv").exists()
    assert (args.output_dir / "manual_label_template.csv").exists()
    assert (args.output_dir / "manual_oracle_template.json").exists()

    case_records = _read_csv(tables_dir / "dynamic_case_records.csv")
    assert {row["scenario_id"] for row in case_records} == {
        "keyboard_trap_fail",
        "missing_focus_indicator_fail",
    }
    assert all(row["runtime_trace_path"] for row in case_records)
    assert all(row["formal_report_path"] for row in case_records)

    findings = _read_csv(tables_dir / "dynamic_finding_status_records.csv")
    assert any(row["criterion_id"] == "2.1.2" and row["status"] == "fail" for row in findings)
    assert any(row["criterion_id"] == "2.4.7" and row["status"] == "fail" for row in findings)

    quality_rows = _read_csv(tables_dir / "dynamic_evidence_quality.csv")
    assert quality_rows
    assert {"scenario_id", "criterion_id", "evidence_strength", "decisive_count", "weak_count"}.issubset(
        quality_rows[0]
    )
    quality_by_status = _read_csv(tables_dir / "dynamic_quality_by_status.csv")
    assert quality_by_status
    assert {"evidence_strength", "status", "count"}.issubset(quality_by_status[0])
    assert summary["evidence_quality_counts"]["by_strength"]
    quality_summary = {row["metric"]: row for row in _read_csv(tables_dir / "dynamic_quality_summary.csv")}
    assert quality_summary["exact_status_accuracy"]["value"] == "1.0"
    assert quality_summary["fail_precision_micro"]["value"] == "1.0"
    assert quality_summary["fail_recall_micro"]["value"] == "1.0"
    assert "ci_low" in quality_summary["exact_status_accuracy"]
    assert summary["publication_metrics"]["point"]["exact_status_accuracy"] == 1.0
    assert summary["attempted_publication_metrics"]["point"]["exact_status_accuracy"] == 1.0
    assert summary["attempted_rows"] == summary["scored_rows"] == 2

    recall = {row["criterion_id"]: row for row in _read_csv(tables_dir / "dynamic_recall.csv")}
    assert recall["overall"]["expected"] == "2"
    assert recall["overall"]["detected"] == "2"
    assert float(recall["overall"]["recall"]) == 1.0

    for scenario_id in ("keyboard_trap_fail", "missing_focus_indicator_fail"):
        scenario_dir = raw_dir / scenario_id
        assert (scenario_dir / "scenario_result.json").exists()
        assert (scenario_dir / "runtime_trace.json").exists()
        assert (scenario_dir / "formal_dynamic_wcag_results.json").exists()
        assert (scenario_dir / "criteria").exists()

    manual_rows = _read_csv(args.output_dir / "manual_label_queue.csv")
    assert {row["scenario_id"] for row in manual_rows} == {
        "keyboard_trap_fail",
        "missing_focus_indicator_fail",
    }
    assert all(row["analyzer_status"] == "fail" for row in manual_rows)
    assert all(row["label_state"] == "labelled" for row in manual_rows)

    baseline_status = _read_csv(tables_dir / "baseline_status.csv")
    assert "non-llm-dynamic-probes" not in {row["baseline_id"] for row in baseline_status}
    assert "static-after-each-state" not in {row["baseline_id"] for row in baseline_status}
    naive_status = next(row for row in baseline_status if row["baseline_id"] == "naive-browser-use-global-audit")
    assert naive_status["status"] == "skipped"
    assert "dry-run" in naive_status["reason"]


def test_fixture_scenario_records_are_loaded_and_trace_is_analyzed() -> None:
    dataset = load_synthetic_dynamic_fixture_metadata()
    scenario = next(item for item in dataset.scenarios if item.scenario_id == "keyboard_trap_fail")

    trace = build_fixture_runtime_trace(scenario)

    assert trace.scenario == "keyboard_trap_fail"
    assert len(trace.steps) >= 2
    assert trace.steps[0].keyboard_state


def test_dynamic_runner_reports_unsupported_and_incomplete(tmp_path: Path) -> None:
    args = SimpleNamespace(
        scenario_set="fixture",
        scenario_id=["hover_only_content_fail"],
        criteria=["1.4.13,9.9.9"],
        max_scenarios=None,
        dry_run=True,
        execution_mode="fixture-trace",
        output_dir=tmp_path / "dynamic_results",
    )

    summary = asyncio.run(run_dynamic_benchmark(args))

    assert summary["unsupported_criteria"] == ["9.9.9"]
    tables_dir = args.output_dir / "tables"
    findings = _read_csv(tables_dir / "dynamic_finding_status_records.csv")
    assert any(row["criterion_id"] == "1.4.13" and row["status"] == "fail" for row in findings)
    assert any(row["criterion_id"] == "9.9.9" and row["status"] == "unsupported" for row in findings)

    counts = _read_csv(tables_dir / "unsupported_incomplete_counts.csv")[0]
    assert counts["incomplete"] == "0"
    assert counts["unsupported"] == "1"

    case_record = _read_csv(tables_dir / "dynamic_case_records.csv")[0]
    assert case_record["run_status"] == "completed"


def test_dynamic_all_prompts_scope_selects_all_supported_criteria(tmp_path: Path) -> None:
    args = SimpleNamespace(
        scenario_set="fixture",
        scenario_id=["keyboard_trap_fail"],
        criteria=[],
        max_scenarios=None,
        dry_run=True,
        gemini_model=None,
        dynamic_judge="rules",
        dynamic_criteria_scope="all-prompts",
        execution_mode="fixture-trace",
        output_dir=tmp_path / "dynamic_results",
    )

    asyncio.run(run_dynamic_benchmark(args))

    case_record = _read_csv(args.output_dir / "tables" / "dynamic_case_records.csv")[0]
    criteria = [item for item in case_record["criteria_evaluated"].split(";") if item]
    assert criteria == DYNAMIC_WCAG_REGISTRY.supported_ids()
    assert len(criteria) == 45


def test_real_all_prompts_scope_uses_supported_registry_without_scenario_hints(tmp_path: Path) -> None:
    args = SimpleNamespace(
        scenario_set="real",
        scenario_id=[],
        criteria=[],
        max_scenarios=2,
        dry_run=True,
        gemini_model=None,
        dynamic_judge="rules",
        dynamic_criteria_scope="all-prompts",
        execution_mode="fixture-trace",
        comparison_baseline="none",
        output_dir=tmp_path / "dynamic_real_results",
    )

    summary = asyncio.run(run_dynamic_benchmark(args))

    assert summary["scenario_set"] == "real"
    assert summary["oracle_rows"] == 90
    assert summary["attempted_rows"] == 90
    assert summary["scored_rows"] == 0
    assert summary["unscored_reasons"] == {"non_browser_use_execution": 90}
    case_records = _read_csv(args.output_dir / "tables" / "dynamic_case_records.csv")
    assert len(case_records) == 2
    assert {row["scored"] for row in case_records} == {"False"}
    for row in case_records:
        criteria = [item for item in row["criteria_evaluated"].split(";") if item]
        assert criteria == DYNAMIC_WCAG_REGISTRY.supported_ids()
        assert len(criteria) == 45
        assert row["criteria_expected"] == ""
        assert row["expected_statuses"] == "{}"

    comparison_rows = _read_csv(args.output_dir / "tables" / "baseline_comparison.csv")
    attempted_comparison_rows = _read_csv(args.output_dir / "tables" / "attempted_baseline_comparison.csv")
    confusion_rows = _read_csv(args.output_dir / "tables" / "status_confusion.csv")
    attempted_confusion_rows = _read_csv(args.output_dir / "tables" / "attempted_status_confusion.csv")
    manual_rows = _read_csv(args.output_dir / "manual_label_queue.csv")
    assert len(comparison_rows) == 0
    assert len(attempted_comparison_rows) == 90
    assert sum(int(row["count"]) for row in confusion_rows) == 0
    assert sum(int(row["count"]) for row in attempted_confusion_rows) == 90
    assert len(manual_rows) == 90


def test_real_preflight_reports_sparse_and_complete_oracle_scenarios(tmp_path: Path) -> None:
    scenario_file = tmp_path / "real_scenarios.json"
    oracle_file = tmp_path / "real_oracle_labels.csv"
    criteria = DYNAMIC_WCAG_REGISTRY.supported_ids()
    scenario_file.write_text(
        json.dumps(
            {
                "dataset_id": "D_dyn_real",
                "name": "Preflight real oracle test",
                "baseline_kind": "human_oracle",
                "scenarios": [
                    {
                        "scenario_id": "s_complete",
                        "scenario_type": "complete oracle",
                        "target": "https://example.com/complete",
                        "interaction_type": "browser_use_task",
                        "task_description": "Open https://example.com/complete and inspect the page.",
                        "scripted_steps": [],
                        "baseline_kind": "human_oracle",
                        "max_steps": 8,
                    },
                    {
                        "scenario_id": "s_sparse",
                        "scenario_type": "sparse oracle",
                        "target": "https://example.com/sparse",
                        "interaction_type": "browser_use_task",
                        "task_description": "Open https://example.com/sparse and inspect the page.",
                        "scripted_steps": [],
                        "baseline_kind": "human_oracle",
                        "labels_pending": True,
                        "max_steps": 8,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    oracle_lines = ["scenario_id,criterion_id,expected_status,evidence,reviewer,confidence,notes"]
    oracle_lines.extend(f"s_complete,{criterion},pass,reviewed,R1,high," for criterion in criteria)
    oracle_lines.append("s_sparse,2.1.2,fail,reviewed,R1,high,")
    oracle_file.write_text("\n".join(oracle_lines) + "\n", encoding="utf-8")
    args = SimpleNamespace(
        scenario_set="real",
        scenario_file=scenario_file,
        oracle_file=oracle_file,
        scenario_id=[],
        criteria=[],
        max_scenarios=None,
        dynamic_criteria_scope="all-prompts",
        allow_sparse_real_oracle=True,
    )

    preflight = build_preflight_for_args(args)

    assert preflight["scenario_count"] == 2
    assert preflight["supported_criteria_count"] == 45
    assert preflight["expected_oracle_rows"] == 90
    assert preflight["actual_oracle_rows"] == 46
    assert preflight["complete_oracle_scenario_ids"] == ["s_complete"]
    assert preflight["labels_pending_scenario_ids"] == ["s_sparse"]
    assert "real_reddit_share_dialog_focus_return" in preflight["removed_challenge_scenario_ids"]
    assert preflight["removed_challenge_scenarios_present"] == []
    sparse = {row["scenario_id"]: row for row in preflight["missing_rows_by_scenario"]}
    assert sparse["s_sparse"]["missing_count"] == 44
    assert "--allow-sparse-real-oracle" not in preflight["final_run_recommendation"]["publication_subset"]["command"]
    assert "--allow-sparse-real-oracle" in preflight["final_run_recommendation"]["exploratory_full_set"]["command"]


def test_real_all_prompts_sparse_oracle_raises_clear_error(tmp_path: Path) -> None:
    scenario_file = tmp_path / "real_scenarios.json"
    oracle_file = tmp_path / "real_oracle_labels.csv"
    scenario_file.write_text(
        json.dumps(
            {
                "dataset_id": "D_dyn_real",
                "name": "Sparse real oracle test",
                "baseline_kind": "human_oracle",
                "scenarios": [
                    {
                        "scenario_id": "s_sparse",
                        "scenario_type": "sparse oracle",
                        "target": "https://example.com",
                        "interaction_type": "browser_use_task",
                        "task_description": "Open https://example.com and inspect the page.",
                        "scripted_steps": [],
                        "baseline_kind": "human_oracle",
                        "max_steps": 8,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    oracle_file.write_text(
        "scenario_id,criterion_id,expected_status,evidence,reviewer,confidence,notes\n"
        "s_sparse,2.1.2,pass,keyboard navigation reviewed,R1,high,only one row\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        scenario_set="real",
        scenario_file=scenario_file,
        oracle_file=oracle_file,
        scenario_id=[],
        criteria=[],
        max_scenarios=None,
        dry_run=True,
        gemini_model=None,
        dynamic_judge="rules",
        dynamic_criteria_scope="all-prompts",
        execution_mode="fixture-trace",
        comparison_baseline="none",
        output_dir=tmp_path / "dynamic_real_results",
    )

    try:
        asyncio.run(run_dynamic_benchmark(args))
    except ValueError as exc:
        message = str(exc)
    else:  # pragma: no cover - defensive failure path
        raise AssertionError("Sparse all-prompts real oracle did not raise")

    assert "complete external oracle" in message
    assert "expected 45" in message


def test_dynamic_gemini_dry_run_writes_model_calls(tmp_path: Path) -> None:
    args = SimpleNamespace(
        scenario_set="fixture",
        scenario_id=["keyboard_trap_fail"],
        criteria=[],
        max_scenarios=None,
        dry_run=True,
        gemini_model="gemini-2.5-flash",
        dynamic_judge="gemini",
        dynamic_criteria_scope="scenario",
        execution_mode="fixture-trace",
        output_dir=tmp_path / "dynamic_results",
    )

    summary = asyncio.run(run_dynamic_benchmark(args))

    assert summary["dynamic_judge"] == "gemini"
    assert summary["analyzer_baseline_id"] == "gemini-dynamic-wcag"
    assert summary["model_summary"]["total_calls"] == 1
    rows = _read_csv(args.output_dir / "tables" / "model_calls.csv")
    assert len(rows) == 1
    assert rows[0]["criterion"] == "2.1.2"
    findings = _read_csv(args.output_dir / "tables" / "dynamic_finding_status_records.csv")
    assert findings[0]["baseline_id"] == "gemini-dynamic-wcag"
    assert findings[0]["expected_label_baseline_id"] == "synthetic-fixture"
    baseline_findings = _read_csv(args.output_dir / "tables" / "baseline_finding_status_records.csv")
    assert {row["baseline_id"] for row in baseline_findings} == {"gemini-dynamic-wcag"}
    pairwise = _read_csv(args.output_dir / "tables" / "baseline_pairwise_comparison.csv")
    assert pairwise == []


def test_real_browser_use_runner_exports_naive_baseline_rows(monkeypatch, tmp_path: Path) -> None:
    from benchmarking.dynamic import run_dynamic_benchmark as runner
    from dynamic.runtime_extractors.utils import write_json

    async def fake_runtime_trace(scenario, args, output_dir):
        now = datetime.now(timezone.utc)
        return RuntimeTrace(
            scenario=scenario.scenario_id,
            metadata=RuntimeTraceMetadata(
                start_time=now,
                end_time=now,
                duration_seconds=0.1,
                artifacts_dir=str(output_dir),
            ),
            steps=[
                RuntimeState(
                    timestamp=now,
                    url=scenario.target,
                    title="Real scenario stub",
                    focused_element={"tag": "button", "role": "button", "attributes": {"style": "outline: 2px solid"}},
                    keyboard_state={"events": [{"key": "Tab"}]},
                    interaction_metadata={"stub": True, "action_results": [{"is_done": True, "success": True}]},
                )
            ],
        )

    async def fake_naive_baseline(*, scenario, output_dir, max_steps, allowed_criteria, **kwargs):
        now = datetime.now(timezone.utc)
        output_dir.mkdir(parents=True, exist_ok=True)
        trace = RuntimeTrace(
            scenario=scenario.scenario_id,
            metadata=RuntimeTraceMetadata(
                start_time=now,
                end_time=now,
                duration_seconds=0.2,
                artifacts_dir=str(output_dir),
            ),
            steps=[RuntimeState(timestamp=now, url=scenario.target, title="Naive stub")],
        )
        results = []
        for criterion_id in allowed_criteria:
            if not DYNAMIC_WCAG_REGISTRY.is_supported(criterion_id):
                continue
            criterion = DYNAMIC_WCAG_REGISTRY.get(criterion_id)
            results.append(
                DynamicCriterionResult(
                    criterion_id=criterion.id,
                    criterion_name=criterion.name,
                    status=DynamicStatus.incomplete,
                    summary="Naive stub did not assess this criterion.",
                    missing_evidence=["naive_baseline_not_assessed"],
                    model="browser-use-global-audit",
                    generated_at=now,
                )
            )
        report = DynamicWCAGReport(
            scenario=scenario.scenario_id,
            generated_at=now,
            criteria_requested=list(allowed_criteria),
            results=results,
            unsupported_criteria=[],
        )
        trace_path = output_dir / "runtime_trace.json"
        report_path = output_dir / "formal_dynamic_wcag_results.json"
        write_json(trace_path, trace.model_dump(mode="json"))
        write_json(report_path, report.model_dump(mode="json"))
        return SimpleNamespace(
            report=report,
            formal_report_path=report_path,
            runtime_trace=trace,
            runtime_trace_path=trace_path,
        )

    monkeypatch.setattr(runner, "_runtime_trace_for_scenario", fake_runtime_trace)
    monkeypatch.setattr(runner, "run_naive_browser_use_baseline", fake_naive_baseline)
    args = SimpleNamespace(
        scenario_set="real",
        scenario_id=[],
        criteria=[],
        max_scenarios=1,
        browser_max_steps=12,
        baseline_max_steps=12,
        dry_run=False,
        gemini_model=None,
        dynamic_judge="rules",
        dynamic_criteria_scope="scenario",
        execution_mode="browser-use",
        comparison_baseline="naive-browser-use-global-audit",
        output_dir=tmp_path / "dynamic_real_results",
    )

    summary = asyncio.run(run_dynamic_benchmark(args))

    assert summary["comparison_baselines"] == ["naive-browser-use-global-audit", "rules-dynamic-wcag"]
    assert summary["scored_rows"] == 45
    baseline_findings = _read_csv(args.output_dir / "tables" / "baseline_finding_status_records.csv")
    assert "naive-browser-use-global-audit" in {row["baseline_id"] for row in baseline_findings}
    pairwise = _read_csv(args.output_dir / "tables" / "baseline_pairwise_comparison.csv")
    assert pairwise
    assert {row["primary_baseline_id"] for row in pairwise} == {"rules-dynamic-wcag"}
    assert {row["comparison_baseline_id"] for row in pairwise} == {"naive-browser-use-global-audit"}
    summary_payload = json.loads((args.output_dir / "benchmark_summary.json").read_text(encoding="utf-8"))
    assert summary_payload["comparison_baselines"] == ["naive-browser-use-global-audit", "rules-dynamic-wcag"]


def test_real_browser_use_challenge_rows_are_attempted_but_unscored(monkeypatch, tmp_path: Path) -> None:
    from benchmarking.dynamic import run_dynamic_benchmark as runner

    async def fake_challenge_trace(scenario, args, output_dir):
        now = datetime.now(timezone.utc)
        return RuntimeTrace(
            scenario=scenario.scenario_id,
            metadata=RuntimeTraceMetadata(
                start_time=now,
                end_time=now,
                duration_seconds=0.1,
                artifacts_dir=str(output_dir),
            ),
            steps=[
                RuntimeState(
                    timestamp=now,
                    url=scenario.target,
                    title="Security check",
                    dom_html="<main>Checking your browser before accessing the page</main>",
                    interaction_metadata={"action_results": [{"is_done": True, "success": False}]},
                )
            ],
        )

    monkeypatch.setattr(runner, "_runtime_trace_for_scenario", fake_challenge_trace)
    args = SimpleNamespace(
        scenario_set="real",
        scenario_id=["real_govuk_search_subscribe"],
        criteria=[],
        max_scenarios=None,
        browser_max_steps=12,
        baseline_max_steps=12,
        dry_run=False,
        gemini_model=None,
        dynamic_judge="rules",
        dynamic_criteria_scope="all-prompts",
        execution_mode="browser-use",
        comparison_baseline="none",
        allow_sparse_real_oracle=False,
        output_dir=tmp_path / "dynamic_real_results",
    )

    summary = asyncio.run(run_dynamic_benchmark(args))

    assert summary["attempted_rows"] == 45
    assert summary["scored_rows"] == 0
    assert summary["challenge_bot_count"] == 1
    assert summary["unscored_reasons"] == {"challenge_or_bot_page": 45}
    findings = _read_csv(args.output_dir / "tables" / "dynamic_finding_status_records.csv")
    assert len(findings) == 45
    assert {row["scored"] for row in findings} == {"False"}
    assert {row["unscored_reason"] for row in findings} == {"challenge_or_bot_page"}


def test_successful_browser_task_with_screenshot_timeout_is_scored(monkeypatch, tmp_path: Path) -> None:
    from benchmarking.dynamic import run_dynamic_benchmark as runner

    scenario_file, oracle_file = _write_single_real_scenario_with_complete_oracle(tmp_path)

    async def fake_trace(scenario, args, output_dir):
        now = datetime.now(timezone.utc)
        return RuntimeTrace(
            scenario=scenario.scenario_id,
            metadata=RuntimeTraceMetadata(
                start_time=now,
                end_time=now,
                duration_seconds=0.1,
                artifacts_dir=str(output_dir),
            ),
            steps=[
                RuntimeState(
                    timestamp=now,
                    url=scenario.target,
                    title="Successful task with screenshot timeout",
                    interaction_metadata={
                        "action_results": [{"is_done": True, "success": True}],
                        "extraction_errors": {
                            "screenshot": "CDP method 'Page.captureScreenshot' did not respond within 60s."
                        },
                    },
                )
            ],
        )

    monkeypatch.setattr(runner, "_runtime_trace_for_scenario", fake_trace)
    args = SimpleNamespace(
        scenario_set="real",
        scenario_file=scenario_file,
        oracle_file=oracle_file,
        scenario_id=[],
        criteria=[],
        max_scenarios=None,
        browser_max_steps=12,
        baseline_max_steps=12,
        dry_run=False,
        gemini_model=None,
        dynamic_judge="rules",
        dynamic_criteria_scope="all-prompts",
        execution_mode="browser-use",
        comparison_baseline="none",
        allow_sparse_real_oracle=False,
        output_dir=tmp_path / "dynamic_real_results",
    )

    summary = asyncio.run(run_dynamic_benchmark(args))

    assert summary["attempted_rows"] == 45
    assert summary["scored_rows"] == 45
    assert summary["unscored_reasons"] == {}
    assert summary["runtime_extraction_error_counts"]["total"] == 0
    assert summary["nonfatal_runtime_extraction_error_counts"]["total"] == 1
    findings = _read_csv(args.output_dir / "tables" / "dynamic_finding_status_records.csv")
    assert {row["scored"] for row in findings} == {"True"}
    assert {row["nonfatal_runtime_extraction_error_count"] for row in findings} == {"1"}
    scoring_audit = _read_csv(args.output_dir / "tables" / "dynamic_scoring_audit.csv")
    assert {row["scored"] for row in scoring_audit} == {"True"}


def test_successful_browser_task_with_fatal_extraction_error_is_unscored(monkeypatch, tmp_path: Path) -> None:
    from benchmarking.dynamic import run_dynamic_benchmark as runner

    scenario_file, oracle_file = _write_single_real_scenario_with_complete_oracle(tmp_path)

    async def fake_trace(scenario, args, output_dir):
        now = datetime.now(timezone.utc)
        return RuntimeTrace(
            scenario=scenario.scenario_id,
            metadata=RuntimeTraceMetadata(
                start_time=now,
                end_time=now,
                duration_seconds=0.1,
                artifacts_dir=str(output_dir),
            ),
            steps=[
                RuntimeState(
                    timestamp=now,
                    url=scenario.target,
                    title="Successful task with fatal extraction error",
                    interaction_metadata={
                        "action_results": [{"is_done": True, "success": True}],
                        "extraction_errors": {"dom": "DOM snapshot extraction failed."},
                    },
                )
            ],
        )

    monkeypatch.setattr(runner, "_runtime_trace_for_scenario", fake_trace)
    args = SimpleNamespace(
        scenario_set="real",
        scenario_file=scenario_file,
        oracle_file=oracle_file,
        scenario_id=[],
        criteria=[],
        max_scenarios=None,
        browser_max_steps=12,
        baseline_max_steps=12,
        dry_run=False,
        gemini_model=None,
        dynamic_judge="rules",
        dynamic_criteria_scope="all-prompts",
        execution_mode="browser-use",
        comparison_baseline="none",
        allow_sparse_real_oracle=False,
        output_dir=tmp_path / "dynamic_real_results",
    )

    summary = asyncio.run(run_dynamic_benchmark(args))

    assert summary["attempted_rows"] == 45
    assert summary["scored_rows"] == 0
    assert summary["unscored_reasons"] == {"runtime_extraction_error": 45}
    assert summary["runtime_extraction_error_counts"]["total"] == 1
    assert summary["nonfatal_runtime_extraction_error_counts"]["total"] == 0
    findings = _read_csv(args.output_dir / "tables" / "dynamic_finding_status_records.csv")
    assert {row["scored"] for row in findings} == {"False"}
    assert {row["runtime_extraction_error_count"] for row in findings} == {"1"}


def test_full_fixture_recall_is_publishable_threshold(tmp_path: Path) -> None:
    args = SimpleNamespace(
        scenario_set="fixture",
        scenario_id=[],
        criteria=[],
        max_scenarios=None,
        dry_run=True,
        execution_mode="fixture-trace",
        output_dir=tmp_path / "dynamic_results",
    )

    summary = asyncio.run(run_dynamic_benchmark(args))

    assert summary["case_records"] >= 8
    assert summary["recall"]["expected"] >= 8
    assert summary["recall"]["recall"] >= 0.75
    assert summary["unsupported_criteria"] == []

    counts = _read_csv(args.output_dir / "tables" / "unsupported_incomplete_counts.csv")[0]
    assert counts["incomplete"] == "0"
    assert counts["unsupported"] == "0"


def test_dynamic_notebook_and_safe_read_csv_tolerate_missing_and_empty_tables(tmp_path: Path) -> None:
    notebook_path = Path("benchmarking/dynamic/publication_analysis.ipynb")
    assert notebook_path.exists()
    json.loads(notebook_path.read_text(encoding="utf-8"))

    missing = safe_read_csv(tmp_path / "missing.csv", columns=["a"])
    assert list(missing.columns) == ["a"]
    assert missing.empty

    empty_path = tmp_path / "empty.csv"
    empty_path.write_text("", encoding="utf-8")
    empty = safe_read_csv(empty_path, columns=["b"])
    assert list(empty.columns) == ["b"]
    assert empty.empty


def test_dynamic_notebook_resolves_latest_results_dir(tmp_path: Path) -> None:
    older = tmp_path / "results_fixture"
    newer = tmp_path / "results_real_gemini_all"
    for path in (older, newer):
        (path / "tables").mkdir(parents=True)
        (path / "benchmark_summary.json").write_text("{}", encoding="utf-8")
    older_summary = older / "benchmark_summary.json"
    newer_summary = newer / "benchmark_summary.json"
    older_time = 1_700_000_000
    newer_time = older_time + 10
    older_summary.touch()
    newer_summary.touch()
    import os

    os.utime(older_summary, (older_time, older_time))
    os.utime(newer_summary, (newer_time, newer_time))

    assert resolve_dynamic_results_dir(tmp_path) == newer
    assert resolve_dynamic_results_dir(tmp_path, requested=older) == older


def test_real_browser_use_run_blocks_with_setup_hint(monkeypatch, tmp_path: Path) -> None:
    from benchmarking.dynamic import run_dynamic_benchmark as runner

    monkeypatch.setattr(
        runner,
        "browser_use_availability",
        lambda: BrowserUseAvailability(
            available=False,
            reason="browser-use is not runnable in this test environment",
        ),
    )
    args = SimpleNamespace(
        scenario_set="real",
        scenario_id=[],
        criteria=[],
        max_scenarios=1,
        browser_max_steps=None,
        dry_run=False,
        execution_mode="browser-use",
        output_dir=tmp_path / "dynamic_real_results",
    )

    summary = asyncio.run(run_dynamic_benchmark(args))

    assert summary["scenario_set"] == "real"
    assert summary["run_statuses"] == ["blocked"]
    assert summary["attempted_rows"] == 45
    assert summary["scored_rows"] == 0
    case_record = _read_csv(args.output_dir / "tables" / "dynamic_case_records.csv")[0]
    assert case_record["run_status"] == "blocked"
    assert case_record["scored"] == "False"
    assert "browser-use is not runnable" in case_record["errors"]
    assert case_record["task_description"].startswith("Open https://")

    manual_rows = _read_csv(args.output_dir / "manual_label_queue.csv")
    assert len(manual_rows) == 45
    assert {row["label_state"] for row in manual_rows} == {"labelled"}
    assert {row["analyzer_status"] for row in manual_rows} == {"incomplete"}
    template_rows = _read_csv(args.output_dir / "manual_label_template.csv")
    assert len(template_rows) == 45


def test_browser_use_scenario_retries_transient_provider_error(monkeypatch, tmp_path: Path) -> None:
    from benchmarking.dynamic import run_dynamic_benchmark as runner
    from dynamic import browser_use_agent

    attempts: list[str] = []
    attempted_tasks: list[str] = []
    model_names: list[str] = []
    fallback_model_names: list[str] = []

    class FakeBrowserUseAccessibilityAgent:
        def __init__(self, *, output_root, enable_evaluator, enable_formal_wcag, model_name, **kwargs):
            self.output_root = output_root
            model_names.append(model_name)
            fallback_model_names.append(kwargs.get("fallback_model_name"))

        async def run_scenario(self, scenario, scenario_id, max_steps):
            attempts.append(scenario_id)
            attempted_tasks.append(scenario)
            if len(attempts) == 1:
                raise RuntimeError("503 UNAVAILABLE: model is currently experiencing high demand")
            now = datetime.now(timezone.utc)
            return RuntimeTrace(
                scenario=scenario_id,
                metadata=RuntimeTraceMetadata(
                    start_time=now,
                    end_time=now,
                    duration_seconds=0.1,
                    artifacts_dir=str(self.output_root / scenario_id),
                ),
                steps=[
                    RuntimeState(
                        timestamp=now,
                        url="https://example.com",
                        title="Recovered",
                        interaction_metadata={"action_results": [{"is_done": True, "success": True}]},
                    )
                ],
            )

    monkeypatch.setattr(
        runner,
        "browser_use_availability",
        lambda: BrowserUseAvailability(available=True, reason="available"),
    )
    monkeypatch.setattr(browser_use_agent, "BrowserUseAccessibilityAgent", FakeBrowserUseAccessibilityAgent)
    scenario = DynamicBenchmarkScenario(
        scenario_id="s_real",
        target="https://example.com",
        interaction_type="browser_use_task",
        task_description="Open https://example.com and inspect the page.",
        scripted_steps=(),
        baseline_kind=DynamicBaselineKind.HUMAN_ORACLE,
        max_steps=8,
    )
    args = SimpleNamespace(
        browser_max_steps=12,
        browser_use_model="gemini-2.5-flash",
        browser_use_scenario_retries=1,
        browser_use_scenario_retry_base_s=0.0,
        browser_use_scenario_retry_max_s=0.0,
        gemini_model="gemini-1.5-flash",
    )

    trace = asyncio.run(_run_browser_use_scenario(scenario, args, tmp_path / "s_real"))

    assert trace.scenario == "s_real"
    assert trace.metadata.scenario_attempts == 2
    assert len(trace.metadata.retry_reasons) == 1
    assert attempts == ["s_real", "s_real"]
    assert attempted_tasks[0] == scenario.task_description
    assert "Retry execution strategy" in attempted_tasks[1]
    assert "Change the action strategy" in attempted_tasks[1]
    assert ":contains(...)" in attempted_tasks[1]
    assert model_names == ["gemini-2.5-flash", "gemini-2.5-flash"]
    assert fallback_model_names == ["gemini-2.5-flash-lite", "gemini-2.5-flash-lite"]


def test_browser_use_retry_task_forbids_invalid_contains_selector() -> None:
    task = _browser_use_task_for_attempt(
        "Open https://example.com and click the named link.",
        attempt_index=1,
        prior_retry_reason="task goal was not reached",
    )

    assert "visible element indices or accessible role/name" in task
    assert "Never use jQuery-only selectors such as :contains(...)" in task


def test_browser_use_scenario_retries_unsuccessful_returned_trace(monkeypatch, tmp_path: Path) -> None:
    from benchmarking.dynamic import run_dynamic_benchmark as runner
    from dynamic import browser_use_agent

    attempts = 0

    class FakeBrowserUseAccessibilityAgent:
        def __init__(self, *, output_root, **kwargs):
            self.output_root = output_root

        async def run_scenario(self, scenario, scenario_id, max_steps):
            nonlocal attempts
            attempts += 1
            now = datetime.now(timezone.utc)
            if attempts == 1:
                action_results = [
                    {
                        "is_done": False,
                        "success": False,
                        "error": "503 UNAVAILABLE: model is currently experiencing high demand",
                    }
                ]
            else:
                action_results = [{"is_done": True, "success": True}]
            return RuntimeTrace(
                scenario=scenario,
                metadata=RuntimeTraceMetadata(start_time=now, end_time=now, duration_seconds=0.1),
                steps=[
                    RuntimeState(
                        timestamp=now,
                        url="https://example.com",
                        interaction_metadata={"action_results": action_results},
                    )
                ],
            )

    monkeypatch.setattr(
        runner,
        "browser_use_availability",
        lambda: BrowserUseAvailability(available=True, reason="available"),
    )
    monkeypatch.setattr(browser_use_agent, "BrowserUseAccessibilityAgent", FakeBrowserUseAccessibilityAgent)
    scenario = DynamicBenchmarkScenario(
        scenario_id="s_returned_error",
        target="https://example.com",
        interaction_type="browser_use_task",
        task_description="Open https://example.com and inspect the page.",
        scripted_steps=(),
        baseline_kind=DynamicBaselineKind.HUMAN_ORACLE,
        max_steps=8,
    )
    args = SimpleNamespace(
        browser_use_scenario_retries=1,
        browser_use_scenario_retry_base_s=0.0,
        browser_use_scenario_retry_max_s=0.0,
        browser_use_retry_goal_not_reached=True,
    )

    trace = asyncio.run(_run_browser_use_scenario(scenario, args, tmp_path / scenario.scenario_id))

    assert attempts == 2
    assert trace.metadata.scenario_attempts == 2
    assert trace.steps[0].interaction_metadata["action_results"][0]["success"] is True


def test_write_external_oracle_from_manual_labels_csv(tmp_path: Path) -> None:
    labels_path = tmp_path / "manual_label_template.csv"
    output_path = tmp_path / "real_oracle_labels.csv"
    labels_path.write_text(
        "scenario_id,criterion_id,reviewer_status,reviewer_confidence,reviewer,reviewer_evidence,reviewer_notes\n"
        "s_real,2.4.7,fail,high,R1,screenshot_step_2.png shows no visible focus indicator,confirmed\n"
        "s_real,4.1.3,pass,medium,R2,live region announces updates,verified\n",
        encoding="utf-8",
    )

    output = write_external_oracle_from_labels(labels_path=labels_path, output_path=output_path)

    assert output == output_path
    rows = _read_csv(output)
    assert rows == [
        {
            "scenario_id": "s_real",
            "criterion_id": "2.4.7",
            "expected_status": "fail",
            "evidence": "screenshot_step_2.png shows no visible focus indicator",
            "reviewer": "R1",
            "confidence": "high",
            "notes": "confirmed",
        },
        {
            "scenario_id": "s_real",
            "criterion_id": "4.1.3",
            "expected_status": "pass",
            "evidence": "live region announces updates",
            "reviewer": "R2",
            "confidence": "medium",
            "notes": "verified",
        },
    ]


def test_rescore_dynamic_results_recomputes_attempted_metrics_from_saved_outputs(tmp_path: Path) -> None:
    criteria = DYNAMIC_WCAG_REGISTRY.supported_ids()
    scenario_file = tmp_path / "real_scenarios.json"
    oracle_file = tmp_path / "real_oracle_labels.csv"
    source_dir = tmp_path / "source_results"
    source_tables = source_dir / "tables"
    output_dir = tmp_path / "rescored_results"
    source_tables.mkdir(parents=True)
    scenario_file.write_text(
        json.dumps(
            {
                "dataset_id": "D_dyn_real",
                "name": "Rescore fixture",
                "baseline_kind": "human_oracle",
                "scenarios": [
                    {
                        "scenario_id": "s_real",
                        "scenario_type": "rescore fixture",
                        "target": "https://example.com",
                        "interaction_type": "browser_use_task",
                        "task_description": "Open https://example.com and inspect the page.",
                        "scripted_steps": [],
                        "baseline_kind": "human_oracle",
                        "max_steps": 8,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    oracle_rows = [
        {
            "scenario_id": "s_real",
            "criterion_id": criterion_id,
            "expected_status": "fail" if criterion_id == "2.1.2" else "pass",
            "evidence": "reviewed",
            "reviewer": "R1",
            "confidence": "high",
            "notes": "",
        }
        for criterion_id in criteria
    ]
    _write_csv_rows(oracle_file, oracle_rows)
    case_rows = [
        {
            "run_id": "run_1",
            "dataset_id": "D_dyn_real",
            "scenario_id": "s_real",
            "interaction_type": "browser_use_task",
            "run_status": "completed",
            "browser_task_success": "True",
            "scored": "False",
            "unscored_reason": "runtime_extraction_error",
            "runtime_trace_path": "source_results/raw/run_1/s_real/runtime_trace.json",
            "formal_report_path": "source_results/raw/run_1/s_real/formal_dynamic_wcag_results.json",
        }
    ]
    finding_rows = [
        {
            "run_id": "run_1",
            "dataset_id": "D_dyn_real",
            "scenario_id": "s_real",
            "baseline_id": "gemini-dynamic-wcag",
            "analyzer_baseline_id": "gemini-dynamic-wcag",
            "criterion_id": criterion_id,
            "criterion_name": DYNAMIC_WCAG_REGISTRY.get(criterion_id).name,
            "status": "pass",
            "summary": "Saved analyzer output.",
            "missing_evidence": "",
            "evidence_refs": "steps.0",
            "evidence_strength": "strong",
            "llm_status": "pass",
            "llm_error": "",
            "invalid_evidence_ref_count": "0",
            "invalid_evidence_refs": "",
            "run_status": "completed",
            "scored": "False",
            "unscored_reason": "runtime_extraction_error",
            "runtime_trace_path": "source_results/raw/run_1/s_real/runtime_trace.json",
            "formal_report_path": "source_results/raw/run_1/s_real/formal_dynamic_wcag_results.json",
        }
        for criterion_id in criteria
    ]
    naive_rows = [
        {
            **row,
            "baseline_id": "naive-browser-use-global-audit",
            "analyzer_baseline_id": "naive-browser-use-global-audit",
            "status": "incomplete",
            "summary": "Naive output did not assess this criterion.",
            "llm_status": "",
        }
        for row in finding_rows
    ]
    quality_rows = [
        {
            "run_id": "run_1",
            "dataset_id": "D_dyn_real",
            "scenario_id": "s_real",
            "baseline_id": "gemini-dynamic-wcag",
            "criterion_id": row["criterion_id"],
            "status": row["status"],
            "evidence_strength": "strong",
            "decisive_count": "1",
            "weak_count": "0",
        }
        for row in finding_rows
    ]
    _write_csv_rows(source_tables / "dynamic_case_records.csv", case_rows)
    _write_csv_rows(source_tables / "dynamic_finding_status_records.csv", finding_rows)
    _write_csv_rows(source_tables / "baseline_finding_status_records.csv", finding_rows + naive_rows)
    _write_csv_rows(source_tables / "dynamic_evidence_quality.csv", quality_rows)
    (source_dir / "benchmark_summary.json").write_text(
        json.dumps(
            {
                "analyzer_baseline_id": "gemini-dynamic-wcag",
                "comparison_baseline": "naive-browser-use-global-audit",
                "dynamic_judge": "gemini",
                "scenario_set": "real",
            }
        ),
        encoding="utf-8",
    )

    summary = rescore_dynamic_results(
        SimpleNamespace(
            source_results_dir=source_dir,
            scenario_file=scenario_file,
            oracle_file=oracle_file,
            output_dir=output_dir,
            analyzer_baseline_id="",
            comparison_baseline="",
            allow_sparse_oracle=False,
            overwrite=False,
        )
    )

    assert summary["rescore_mode"] == "previous_run_outputs_relabelled_no_browser_or_llm_rerun"
    assert summary["oracle_rows"] == len(criteria)
    assert summary["scored_rows"] == 0
    assert summary["primary_metric_oracle_rows"] == 0
    assert summary["publication_metrics"]["rows"] == 0
    attempted = summary["attempted_publication_metrics"]["point"]
    assert attempted["oracle_rows"] == len(criteria)
    assert attempted["exact_status_correct"] == len(criteria) - 1
    assert attempted["fail"]["micro"]["recall"] == 0.0
    assert attempted["pass"]["micro"]["recall"] == 1.0
    attempted_quality = {row["metric"]: row for row in _read_csv(output_dir / "tables" / "attempted_dynamic_quality_summary.csv")}
    assert attempted_quality["oracle_rows"]["value"] == str(len(criteria))
    assert attempted_quality["exact_status_accuracy"]["numerator"] == str(len(criteria) - 1)
    assert _read_csv(output_dir / "tables" / "baseline_comparison.csv") == []
    assert len(_read_csv(output_dir / "tables" / "attempted_baseline_comparison.csv")) == len(criteria) * 2
    scoring_audit = _read_csv(output_dir / "tables" / "dynamic_scoring_audit.csv")
    assert any(row["criterion_id"] == "2.1.2" and row["oracle_status"] == "fail" for row in scoring_audit)
    template_rows = _read_csv(output_dir / "manual_label_template.csv")
    assert len(template_rows) == len(criteria)
    assert {row["reviewer_status"] for row in template_rows} == {"fail", "pass"}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_single_real_scenario_with_complete_oracle(tmp_path: Path) -> tuple[Path, Path]:
    scenario_file = tmp_path / "real_scenarios.json"
    oracle_file = tmp_path / "real_oracle_labels.csv"
    scenario_file.write_text(
        json.dumps(
            {
                "dataset_id": "D_dyn_real",
                "name": "Runtime extraction scoring test",
                "baseline_kind": "human_oracle",
                "scenarios": [
                    {
                        "scenario_id": "s_real",
                        "scenario_type": "runtime extraction scoring",
                        "target": "https://example.com",
                        "interaction_type": "browser_use_task",
                        "task_description": "Open https://example.com and inspect the page.",
                        "scripted_steps": [],
                        "baseline_kind": "human_oracle",
                        "max_steps": 8,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _write_csv_rows(
        oracle_file,
        [
            {
                "scenario_id": "s_real",
                "criterion_id": criterion_id,
                "expected_status": "pass",
                "evidence": "reviewed",
                "reviewer": "R1",
                "confidence": "high",
                "notes": "",
            }
            for criterion_id in DYNAMIC_WCAG_REGISTRY.supported_ids()
        ],
    )
    return scenario_file, oracle_file


def _write_csv_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
