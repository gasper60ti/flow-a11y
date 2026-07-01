"""Rescore saved dynamic benchmark outputs against a replacement oracle.

This helper is intentionally metric-only: it never reruns browser-use, probes,
or LLM judging. It reads saved analyzer rows and recomputes oracle-dependent
tables so relabelled manual oracle data can be inspected quickly.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dynamic.preprocessors.wcag_mapper import DYNAMIC_WCAG_REGISTRY

from benchmarking.dynamic_manual_review import (
    build_dynamic_manual_label_rows,
    write_dynamic_manual_label_queue,
)
from benchmarking.dynamic_datasets import (
    DynamicBenchmarkDataset,
    flatten_oracle_labels,
    load_dynamic_dataset_file,
    load_real_oracle_labels,
)
from benchmarking.dynamic_metrics import (
    build_baseline_comparison_rows,
    build_dynamic_quality_summary_rows,
    build_false_audit_rows,
    compute_criterion_precision,
    compute_criterion_recall,
    compute_publication_metrics,
    compute_status_confusion_counts,
)
from benchmarking.dynamic.run_dynamic_benchmark import (
    DEFAULT_RESULTS_DIR,
    NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE,
    TABLES_DIR_NAME,
    _ablation_rows,
    _baseline_pairwise_rows,
    _coverage_rows,
    _criteria_support_rows,
    _error_analysis_rows,
    _invalid_evidence_ref_counts,
    _invalid_evidence_ref_rows,
    _oracle_rows_for_report_keys,
    _precision_rows,
    _primary_scored_report_rows,
    _quality_by_status_rows,
    _recall_rows,
    _scoring_audit_rows,
    _task_success_summary,
    _unscored_reason_counts,
    _write_csv,
    build_publication_preflight,
)


DEFAULT_SOURCE_RESULTS_DIR = DEFAULT_RESULTS_DIR.with_name("results_real_all_exploratory_naive_baseline")
DEFAULT_RESCORE_OUTPUT_DIR = DEFAULT_RESULTS_DIR.with_name(
    "results_real_all_NF_relabelled_rescore_previous_outputs"
)
DEFAULT_ANALYZER_BASELINE_ID = "gemini-dynamic-wcag"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-results-dir",
        type=Path,
        default=DEFAULT_SOURCE_RESULTS_DIR,
        help="Existing dynamic result directory whose saved outputs should be rescored.",
    )
    parser.add_argument(
        "--scenario-file",
        type=Path,
        required=True,
        help="Scenario JSON used to interpret oracle rows.",
    )
    parser.add_argument(
        "--oracle-file",
        type=Path,
        required=True,
        help="External oracle CSV to score against.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_RESCORE_OUTPUT_DIR,
        help="Fresh result directory for rescored tables.",
    )
    parser.add_argument(
        "--analyzer-baseline-id",
        default="",
        help="Analyzer baseline id to score; defaults to source benchmark_summary.json.",
    )
    parser.add_argument(
        "--comparison-baseline",
        default="",
        help="Comparison baseline id; defaults to source benchmark_summary.json.",
    )
    parser.add_argument(
        "--allow-sparse-oracle",
        action="store_true",
        help="Allow rescoring when the oracle does not cover every selected scenario/criterion row.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing output directory.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    summary = rescore_dynamic_results(parse_args(argv))
    attempted = summary.get("attempted_publication_metrics", {}).get("point", {})
    fail = attempted.get("fail", {}).get("micro", {})
    passed = attempted.get("pass", {}).get("micro", {})
    print("Dynamic rescore finished.")
    print(f"- Source results: {summary['source_results_dir']}")
    print(f"- Rescored results: {summary['results_dir']}")
    print(f"- Oracle rows: {summary['oracle_rows']}")
    print(f"- Strict primary scored rows: {summary['scored_rows']}")
    print(
        "- Attempted exact accuracy: "
        f"{attempted.get('exact_status_correct')} / {attempted.get('oracle_rows')} "
        f"= {attempted.get('exact_status_accuracy')}"
    )
    print(
        "- Attempted fail precision/recall/F1: "
        f"{fail.get('precision')} / {fail.get('recall')} / {fail.get('f1')}"
    )
    print(
        "- Attempted pass precision/recall/F1: "
        f"{passed.get('precision')} / {passed.get('recall')} / {passed.get('f1')}"
    )
    return 0


def rescore_dynamic_results(args: argparse.Namespace) -> dict[str, Any]:
    source_results_dir = Path(args.source_results_dir)
    source_tables_dir = source_results_dir / TABLES_DIR_NAME
    output_dir = Path(args.output_dir)
    output_tables_dir = output_dir / TABLES_DIR_NAME

    if not source_tables_dir.exists():
        raise FileNotFoundError(f"{source_results_dir}: missing tables directory")
    if output_dir.exists() and any(output_dir.iterdir()):
        if not bool(getattr(args, "overwrite", False)):
            raise FileExistsError(f"{output_dir} already exists; pass --overwrite to replace it")
        shutil.rmtree(output_dir)

    dataset = load_dynamic_dataset_file(Path(args.scenario_file))
    oracle_rows = flatten_oracle_labels(dataset, load_real_oracle_labels(Path(args.oracle_file)))
    expected_oracle_rows = len(dataset.scenarios) * len(DYNAMIC_WCAG_REGISTRY.supported_ids())
    if len(oracle_rows) != expected_oracle_rows and not bool(getattr(args, "allow_sparse_oracle", False)):
        raise ValueError(
            "Rescore requires a complete oracle by default: "
            f"found {len(oracle_rows)} rows, expected {expected_oracle_rows}. "
            "Pass --allow-sparse-oracle only for exploratory rescoring."
        )

    source_summary = _read_json(source_results_dir / "benchmark_summary.json")
    analyzer_baseline_id = str(
        getattr(args, "analyzer_baseline_id", "")
        or source_summary.get("analyzer_baseline_id")
        or DEFAULT_ANALYZER_BASELINE_ID
    )
    comparison_baseline_id = str(
        getattr(args, "comparison_baseline", "")
        or source_summary.get("comparison_baseline")
        or NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id
    )

    case_records = _read_csv_required(source_tables_dir / "dynamic_case_records.csv")
    finding_records = _read_csv_required(source_tables_dir / "dynamic_finding_status_records.csv")
    report_rows = [row for row in finding_records if str(row.get("baseline_id", "")) == analyzer_baseline_id]
    if not report_rows and finding_records:
        report_rows = list(finding_records)

    baseline_finding_rows = _read_csv_optional(source_tables_dir / "baseline_finding_status_records.csv")
    reports_by_baseline = _reports_by_baseline(
        baseline_finding_rows,
        analyzer_baseline_id=analyzer_baseline_id,
        analyzer_rows=report_rows,
    )
    quality_records = _read_csv_optional(source_tables_dir / "dynamic_evidence_quality.csv")
    unsupported_seen = {
        str(row.get("criterion_id", ""))
        for row in report_rows
        if str(row.get("status", "")) == "unsupported" and row.get("criterion_id")
    }

    complete_oracle_scenario_ids = _complete_oracle_scenario_ids(dataset, oracle_rows)
    primary_report_rows = _primary_scored_report_rows(report_rows, complete_oracle_scenario_ids)
    primary_oracle_rows = _oracle_rows_for_report_keys(oracle_rows, primary_report_rows)
    primary_reports_by_baseline = {
        baseline_id: _primary_scored_report_rows(rows, complete_oracle_scenario_ids)
        for baseline_id, rows in reports_by_baseline.items()
    }
    primary_reports_by_baseline = {
        baseline_id: rows for baseline_id, rows in primary_reports_by_baseline.items() if rows
    }

    recall = compute_criterion_recall(dataset, primary_report_rows, oracle_rows=primary_oracle_rows)
    precision = compute_criterion_precision(dataset, primary_report_rows, oracle_rows=primary_oracle_rows)
    publication_metrics = compute_publication_metrics(dataset, primary_report_rows, oracle_rows=primary_oracle_rows)
    attempted_recall = compute_criterion_recall(dataset, report_rows, oracle_rows=oracle_rows)
    attempted_precision = compute_criterion_precision(dataset, report_rows, oracle_rows=oracle_rows)
    attempted_publication_metrics = compute_publication_metrics(dataset, report_rows, oracle_rows=oracle_rows)
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
        scope="attempted_complete_oracle_previous_outputs",
    )
    false_positive_rows, false_negative_rows = build_false_audit_rows(
        dataset,
        report_rows,
        oracle_rows=oracle_rows,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_tables_dir, output_tables_dir, dirs_exist_ok=True)

    _write_csv(output_tables_dir / "dynamic_recall.csv", _recall_rows(recall))
    _write_csv(output_tables_dir / "dynamic_precision.csv", _precision_rows(precision))
    _write_csv(output_tables_dir / "attempted_dynamic_recall.csv", _recall_rows(attempted_recall))
    _write_csv(output_tables_dir / "attempted_dynamic_precision.csv", _precision_rows(attempted_precision))
    _write_csv(
        output_tables_dir / "status_confusion.csv",
        compute_status_confusion_counts(dataset, primary_report_rows, oracle_rows=primary_oracle_rows),
    )
    _write_csv(
        output_tables_dir / "attempted_status_confusion.csv",
        compute_status_confusion_counts(dataset, report_rows, oracle_rows=oracle_rows),
    )
    _write_csv(output_tables_dir / "dynamic_quality_summary.csv", dynamic_quality_summary_rows)
    _write_csv(output_tables_dir / "attempted_dynamic_quality_summary.csv", attempted_dynamic_quality_summary_rows)
    _write_csv(output_tables_dir / "dynamic_false_positive_audit.csv", false_positive_rows)
    _write_csv(output_tables_dir / "dynamic_false_negative_audit.csv", false_negative_rows)
    _write_csv(output_tables_dir / "dynamic_error_analysis.csv", _error_analysis_rows(false_positive_rows, false_negative_rows))
    _write_csv(output_tables_dir / "dynamic_scoring_audit.csv", _scoring_audit_rows(dataset, report_rows, oracle_rows=oracle_rows))
    _write_csv(output_tables_dir / "invalid_evidence_refs.csv", _invalid_evidence_ref_rows(report_rows))
    _write_csv(output_tables_dir / "dynamic_coverage.csv", _coverage_rows(dataset, report_rows, unsupported_seen, oracle_rows=oracle_rows))
    _write_csv(
        output_tables_dir / "criteria_support_matrix.csv",
        _criteria_support_rows(dataset, report_rows, unsupported_seen, oracle_rows=oracle_rows),
    )
    _write_csv(
        output_tables_dir / "baseline_comparison.csv",
        build_baseline_comparison_rows(dataset, primary_reports_by_baseline, oracle_rows=primary_oracle_rows),
    )
    _write_csv(
        output_tables_dir / "attempted_baseline_comparison.csv",
        build_baseline_comparison_rows(dataset, reports_by_baseline, oracle_rows=oracle_rows),
    )
    _write_csv(
        output_tables_dir / "baseline_pairwise_comparison.csv",
        _baseline_pairwise_rows(
            dataset,
            primary_reports_by_baseline,
            primary_baseline_id=analyzer_baseline_id,
            comparison_baseline_id=comparison_baseline_id,
            oracle_rows=primary_oracle_rows,
        ),
    )
    _write_csv(
        output_tables_dir / "attempted_baseline_pairwise_comparison.csv",
        _baseline_pairwise_rows(
            dataset,
            reports_by_baseline,
            primary_baseline_id=analyzer_baseline_id,
            comparison_baseline_id=comparison_baseline_id,
            oracle_rows=oracle_rows,
        ),
    )
    _write_csv(
        output_tables_dir / "dynamic_ablation.csv",
        _ablation_rows(
            dataset,
            primary_reports_by_baseline,
            analyzer_baseline_id,
            str(source_summary.get("dynamic_judge", "gemini")),
            oracle_rows=primary_oracle_rows,
        ),
    )
    _write_csv(
        output_tables_dir / "attempted_dynamic_ablation.csv",
        _ablation_rows(
            dataset,
            reports_by_baseline,
            analyzer_baseline_id,
            str(source_summary.get("dynamic_judge", "gemini")),
            oracle_rows=oracle_rows,
        ),
    )
    _write_csv(output_tables_dir / "rescore_oracle_rows.csv", oracle_rows)

    if quality_records:
        _write_csv(output_tables_dir / "dynamic_quality_by_status.csv", _quality_by_status_rows(quality_records))

    manual_dataset = _dataset_with_completed_label_flags(dataset, complete_oracle_scenario_ids)
    manual_label_rows = build_dynamic_manual_label_rows(
        manual_dataset,
        case_records,
        report_rows,
        source_results_dir / "raw",
        oracle_rows=oracle_rows,
    )
    manual_label_queue_path = write_dynamic_manual_label_queue(
        manual_label_rows,
        output_dir / "manual_label_queue.csv",
    )
    manual_label_template_path = _copy_oracle_labels_as_manual_template(
        Path(args.oracle_file),
        output_dir / "manual_label_template.csv",
    )
    shutil.copy2(Path(args.oracle_file), output_dir / Path(args.oracle_file).name)
    if (source_results_dir / "benchmark_summary.json").exists():
        shutil.copy2(source_results_dir / "benchmark_summary.json", output_dir / "source_benchmark_summary.json")

    preflight = build_publication_preflight(
        args=SimpleNamespace(
            scenario_set=source_summary.get("scenario_set", "real"),
            scenario_file=Path(args.scenario_file),
            oracle_file=Path(args.oracle_file),
            scenario_id=[],
            criteria=[],
            max_scenarios=None,
            dynamic_criteria_scope="all-prompts",
            allow_sparse_real_oracle=bool(getattr(args, "allow_sparse_oracle", False)),
        ),
        dataset=dataset,
        requested_criteria=[],
        criteria_scope="all-prompts",
        oracle_rows=oracle_rows,
    )

    summary = dict(source_summary)
    summary.update(
        {
            "results_dir": str(output_dir),
            "tables_dir": str(output_tables_dir),
            "raw_dir": str(source_results_dir / "raw"),
            "source_results_dir": str(source_results_dir),
            "source_benchmark_summary": str(output_dir / "source_benchmark_summary.json"),
            "rescore_mode": "previous_run_outputs_relabelled_no_browser_or_llm_rerun",
            "rescore_generated_at": datetime.now(timezone.utc).isoformat(),
            "rescore_scope_note": (
                "Strict publication_metrics use scored rows and may be empty when the source run "
                "marked all rows unscored. Complete-oracle previous-run numbers are exported under "
                "attempted_publication_metrics."
            ),
            "scenario_file": str(Path(args.scenario_file)),
            "oracle_file": str(Path(args.oracle_file)),
            "oracle_rows": len(oracle_rows),
            "oracle_label_status_counts": dict(sorted(Counter(row["expected_status"] for row in oracle_rows).items())),
            "oracle_cardinality": {
                "status": "passed" if len(oracle_rows) == expected_oracle_rows else "sparse_allowed",
                "oracle_rows": len(oracle_rows),
                "expected_rows": expected_oracle_rows,
                "scenario_count": len(dataset.scenarios),
                "supported_dynamic_criteria_count": len(DYNAMIC_WCAG_REGISTRY.supported_ids()),
                "allow_sparse_real_oracle": bool(getattr(args, "allow_sparse_oracle", False)),
            },
            "preflight": preflight,
            "leakage_check": {
                "status": "not_rerun_rescore_only",
                "source_status": source_summary.get("leakage_check", {}).get("status", ""),
                "source_files_scanned": source_summary.get("leakage_check", {}).get("files_scanned", ""),
            },
            "case_records": len(case_records),
            "finding_status_records": len(report_rows),
            "attempted_rows": len(report_rows),
            "scored_rows": len(primary_report_rows),
            "unscored_rows": len(report_rows) - len(primary_report_rows),
            "unscored_reasons": _unscored_reason_counts(report_rows),
            "baseline_finding_status_records": sum(len(rows) for rows in reports_by_baseline.values()),
            "comparison_baselines": sorted(reports_by_baseline),
            "primary_metric_oracle_rows": len(primary_oracle_rows),
            "publication_metrics": publication_metrics,
            "attempted_publication_metrics": attempted_publication_metrics,
            "recall": recall.get("overall", {}),
            "precision": precision.get("overall", {}),
            "attempted_recall": attempted_recall.get("overall", {}),
            "attempted_precision": attempted_precision.get("overall", {}),
            "dynamic_quality_summary": _metric_value_map(dynamic_quality_summary_rows),
            "attempted_dynamic_quality_summary": _metric_value_map(attempted_dynamic_quality_summary_rows),
            "task_success": task_success,
            "false_positive_audit_rows": len(false_positive_rows),
            "false_negative_audit_rows": len(false_negative_rows),
            "invalid_evidence_ref_rows": len(_invalid_evidence_ref_rows(report_rows)),
            "invalid_evidence_ref_counts": _invalid_evidence_ref_counts(report_rows),
            "manual_label_queue": str(manual_label_queue_path),
            "manual_label_template": str(manual_label_template_path),
            "manual_oracle_template": "",
        }
    )
    _write_json(output_dir / "benchmark_summary.json", summary)
    return summary


def _read_csv_required(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"{path}: required source table is missing")
    return _read_csv_optional(path)


def _read_csv_optional(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")


def _reports_by_baseline(
    baseline_finding_rows: list[dict[str, str]],
    *,
    analyzer_baseline_id: str,
    analyzer_rows: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in baseline_finding_rows:
        baseline_id = str(row.get("baseline_id", ""))
        if baseline_id:
            grouped[baseline_id].append(row)
    grouped[analyzer_baseline_id] = list(analyzer_rows)
    return dict(sorted(grouped.items()))


def _complete_oracle_scenario_ids(
    dataset: DynamicBenchmarkDataset,
    oracle_rows: list[dict[str, Any]],
) -> set[str]:
    expected_count = len(DYNAMIC_WCAG_REGISTRY.supported_ids())
    counts: Counter[str] = Counter(str(row["scenario_id"]) for row in oracle_rows)
    return {scenario.scenario_id for scenario in dataset.scenarios if counts[scenario.scenario_id] == expected_count}


def _dataset_with_completed_label_flags(
    dataset: DynamicBenchmarkDataset,
    complete_oracle_scenario_ids: set[str],
) -> DynamicBenchmarkDataset:
    return replace(
        dataset,
        scenarios=tuple(
            replace(scenario, labels_pending=scenario.scenario_id not in complete_oracle_scenario_ids)
            for scenario in dataset.scenarios
        ),
    )


def _copy_oracle_labels_as_manual_template(oracle_file: Path, output_path: Path) -> Path:
    labels = load_real_oracle_labels(oracle_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "scenario_id",
                "criterion_id",
                "reviewer_status",
                "reviewer_confidence",
                "reviewer",
                "reviewer_evidence",
                "reviewer_notes",
            ],
        )
        writer.writeheader()
        for label in labels:
            writer.writerow(
                {
                    "scenario_id": label.scenario_id,
                    "criterion_id": label.criterion_id,
                    "reviewer_status": label.expected_status.value,
                    "reviewer_confidence": label.confidence,
                    "reviewer": label.reviewer,
                    "reviewer_evidence": ";".join(label.evidence),
                    "reviewer_notes": label.notes,
                }
            )
    return output_path


def _metric_value_map(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {str(row["metric"]): row.get("value") for row in rows}


if __name__ == "__main__":
    raise SystemExit(main())
