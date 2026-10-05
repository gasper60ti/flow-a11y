from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarking.dynamic.matched_global_evidence_baseline import GlobalEvidencePreprocessor
from benchmarking.dynamic.matched_trace_baseline import TRACE_VIEW_BUDGET_CHARS, build_generic_trace_view
from benchmarking.dynamic.run_dynamic_benchmark import build_fixture_runtime_trace, parse_args, run_dynamic_benchmark
from benchmarking.dynamic_baselines import (
    MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE,
    MATCHED_TRACE_GENERIC_JUDGE_BASELINE,
)
from benchmarking.dynamic_datasets import load_synthetic_dynamic_fixture_metadata
from benchmarking.run_benchmark import parse_args as dispatch_parse_args


def _fixture_scenario_and_trace():  # noqa: ANN202
    scenario = next(
        item
        for item in load_synthetic_dynamic_fixture_metadata().scenarios
        if item.scenario_id == "keyboard_trap_fail"
    )
    return scenario, build_fixture_runtime_trace(scenario)


def _fixture_args(output_dir: Path, baseline_id: str, judge: str) -> SimpleNamespace:
    return SimpleNamespace(
        scenario_set="fixture",
        scenario_file=None,
        oracle_file=None,
        scenario_id=[],
        criteria=[],
        max_scenarios=1,
        browser_max_steps=None,
        baseline_max_steps=None,
        dry_run=True,
        execution_mode="fixture-trace",
        output_dir=output_dir,
        dynamic_judge=judge,
        dynamic_criteria_scope="scenario",
        comparison_baseline=baseline_id,
    )


def _baseline_ids(path: Path) -> set[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["baseline_id"] for row in csv.DictReader(handle)}


def test_cli_accepts_matched_baseline_identifiers() -> None:
    for baseline in (
        MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id,
        MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id,
    ):
        direct = parse_args(["--comparison-baseline", baseline])
        dispatched = dispatch_parse_args(["--mode", "dynamic", "--comparison-baseline", baseline])
        assert direct.comparison_baseline == baseline
        assert dispatched.comparison_baseline == baseline


def test_generic_trace_view_is_stable_and_bounded() -> None:
    _, trace = _fixture_scenario_and_trace()

    first_view, first_contract = build_generic_trace_view(trace)
    second_view, second_contract = build_generic_trace_view(trace)

    assert first_view == second_view
    assert first_contract == second_contract
    assert first_contract["trace_view_characters"] <= TRACE_VIEW_BUDGET_CHARS
    assert len(first_view["steps"]) == len(trace.steps)


def test_global_evidence_preprocessor_shares_one_packet_across_requested_probes() -> None:
    _, trace = _fixture_scenario_and_trace()
    bundle = GlobalEvidencePreprocessor().preprocess(trace, criteria=["2.1.2", "2.4.7"])

    packets = list(bundle.probe_evidence.values())
    assert len(packets) == 1
    assert packets[0] == bundle.flat_evidence


def test_baseline_two_dry_run_exports_matched_trace_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "baseline2"
    summary = asyncio.run(
        run_dynamic_benchmark(
            _fixture_args(output_dir, MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id, "rules")
        )
    )

    assert summary["comparison_baseline"] == MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id
    assert MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id in _baseline_ids(
        output_dir / "tables" / "baseline_finding_status_records.csv"
    )
    root = output_dir / "raw" / "run_1" / "keyboard_trap_fail" / "baselines" / MATCHED_TRACE_GENERIC_JUDGE_BASELINE.baseline_id
    assert (root / "input_contract.json").is_file()
    assert (root / "raw_trace_view.json").is_file()
    assert (output_dir / "tables" / "baseline_2_0_model_calls.csv").is_file()


def test_baseline_three_dry_run_exports_global_evidence_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "baseline3"
    summary = asyncio.run(
        run_dynamic_benchmark(
            _fixture_args(output_dir, MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id, "gemini")
        )
    )

    assert summary["comparison_baseline"] == MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id
    assert MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id in _baseline_ids(
        output_dir / "tables" / "baseline_finding_status_records.csv"
    )
    root = output_dir / "raw" / "run_1" / "keyboard_trap_fail" / "baselines" / MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id
    assert (root / "global_evidence.json").is_file()
    assert (root / "shared_gate_decisions.json").is_file()
    assert (output_dir / "tables" / "baseline_3_0_model_calls.csv").is_file()


def test_baseline_three_rejects_non_gemini_primary_judge(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Baseline 3.0 requires --dynamic-judge gemini"):
        asyncio.run(
            run_dynamic_benchmark(
                _fixture_args(
                    tmp_path / "invalid",
                    MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id,
                    "rules",
                )
            )
        )
