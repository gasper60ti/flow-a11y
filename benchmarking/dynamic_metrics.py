"""Deterministic metrics for dynamic benchmark baselines."""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from typing import Any, Iterable

from dynamic.schemas.wcag_dynamic import DynamicStatus

from benchmarking.dynamic_datasets import DynamicBenchmarkDataset, flatten_expected_results

POSITIVE_STATUSES = (DynamicStatus.fail,)
LABELLED_EXPECTED_STATUSES = (
    DynamicStatus.pass_status,
    DynamicStatus.fail,
    DynamicStatus.warning,
    DynamicStatus.not_applicable,
)
AUDIT_TARGET_STATUSES = ("fail", "pass")


def compute_criterion_recall(
    dataset: DynamicBenchmarkDataset,
    report_rows: Iterable[dict[str, Any]],
    positive_statuses: Iterable[DynamicStatus | str] = POSITIVE_STATUSES,
    *,
    oracle_rows: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute criterion-level recall over positive expected statuses."""

    positives = _status_values(positive_statuses)
    expected_rows = [
        row for row in _expected_rows(dataset, oracle_rows) if str(row["expected_status"]) in positives
    ]
    actual_by_key = _actual_by_key(report_rows)

    by_criterion: dict[str, dict[str, Any]] = {}
    for expected in expected_rows:
        criterion_id = expected["criterion_id"]
        actual_status = actual_by_key.get((expected["scenario_id"], criterion_id))
        detected = actual_status in positives
        bucket = by_criterion.setdefault(criterion_id, {"expected": 0, "detected": 0, "missed": 0, "recall": None})
        bucket["expected"] += 1
        if detected:
            bucket["detected"] += 1
        else:
            bucket["missed"] += 1

    for bucket in by_criterion.values():
        bucket["recall"] = bucket["detected"] / bucket["expected"] if bucket["expected"] else None

    total_expected = sum(bucket["expected"] for bucket in by_criterion.values())
    total_detected = sum(bucket["detected"] for bucket in by_criterion.values())
    return {
        "overall": {
            "expected": total_expected,
            "detected": total_detected,
            "missed": total_expected - total_detected,
            "recall": total_detected / total_expected if total_expected else None,
        },
        "by_criterion": by_criterion,
    }


def compute_criterion_precision(
    dataset: DynamicBenchmarkDataset,
    report_rows: Iterable[dict[str, Any]],
    positive_statuses: Iterable[DynamicStatus | str] = POSITIVE_STATUSES,
    labelled_expected_statuses: Iterable[DynamicStatus | str] = LABELLED_EXPECTED_STATUSES,
    *,
    oracle_rows: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute criterion precision for report rows with matching labels."""

    positives = _status_values(positive_statuses)
    labelled_statuses = _status_values(labelled_expected_statuses)
    expected_by_key = {
        (row["scenario_id"], row["criterion_id"]): str(row["expected_status"])
        for row in _expected_rows(dataset, oracle_rows)
        if str(row["expected_status"]) in labelled_statuses
    }

    by_criterion: dict[str, dict[str, Any]] = {}
    labelled_predictions = 0
    correct_predictions = 0

    for row in report_rows:
        scenario_id = str(row.get("scenario_id", ""))
        criterion_id = str(row.get("criterion_id", ""))
        status = _coerce_status_value(row.get("status"))
        if status not in positives:
            continue

        expected_status = expected_by_key.get((scenario_id, criterion_id))
        if expected_status is None:
            continue

        correct = expected_status in positives
        bucket = by_criterion.setdefault(criterion_id, {"labelled_predictions": 0, "correct": 0, "precision": None})
        bucket["labelled_predictions"] += 1
        labelled_predictions += 1
        if correct:
            bucket["correct"] += 1
            correct_predictions += 1

    for bucket in by_criterion.values():
        total = bucket["labelled_predictions"]
        bucket["precision"] = bucket["correct"] / total if total else None

    return {
        "overall": {
            "labelled_predictions": labelled_predictions,
            "correct": correct_predictions,
            "precision": correct_predictions / labelled_predictions if labelled_predictions else None,
        },
        "by_criterion": by_criterion,
    }


def compute_status_confusion_counts(
    dataset: DynamicBenchmarkDataset,
    report_rows: Iterable[dict[str, Any]],
    *,
    oracle_rows: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return expected-status by actual-status confusion counts."""

    actual_by_key = _actual_by_key(report_rows)
    counts: Counter[tuple[str, str]] = Counter()

    for expected in _expected_rows(dataset, oracle_rows):
        actual = actual_by_key.get((expected["scenario_id"], expected["criterion_id"]), DynamicStatus.incomplete.value)
        counts[(expected["expected_status"], actual)] += 1

    return [
        {"expected_status": expected, "actual_status": actual, "count": count}
        for (expected, actual), count in sorted(counts.items())
    ]


def compute_unsupported_incomplete_counts(
    report_rows: Iterable[dict[str, Any]],
    unsupported_criteria: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Count incomplete rows and unsupported criteria for a dynamic report."""

    rows = list(report_rows)
    incomplete_rows = [row for row in rows if _coerce_status_value(row.get("status")) == DynamicStatus.incomplete.value]
    unsupported = [str(item) for item in (unsupported_criteria or [])]
    return {
        "results": len(rows),
        "incomplete": len(incomplete_rows),
        "unsupported": len(unsupported),
        "unsupported_criteria": unsupported,
    }


def build_baseline_comparison_rows(
    dataset: DynamicBenchmarkDataset,
    reports_by_baseline: dict[str, Iterable[dict[str, Any]]],
    *,
    oracle_rows: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build long-form comparison rows for one or more baselines."""

    expected_rows = _expected_rows(dataset, oracle_rows)
    actual_by_baseline = {
        baseline_id: _actual_by_key(rows) for baseline_id, rows in reports_by_baseline.items()
    }

    rows: list[dict[str, Any]] = []
    for baseline_id, actual_by_key in sorted(actual_by_baseline.items()):
        for expected in expected_rows:
            key = (expected["scenario_id"], expected["criterion_id"])
            assessed = key in actual_by_key
            actual_status = actual_by_key.get(key, DynamicStatus.incomplete.value)
            rows.append(
                {
                    "dataset_id": expected["dataset_id"],
                    "baseline_id": baseline_id,
                    "analyzer_baseline_id": baseline_id,
                    "scenario_id": expected["scenario_id"],
                    "criterion_id": expected["criterion_id"],
                    "expected_status": expected["expected_status"],
                    "actual_status": actual_status,
                    "assessed": assessed,
                    "unassessed_marked_incomplete": not assessed,
                    "matched": actual_status == expected["expected_status"],
                    "baseline_kind": expected["baseline_kind"],
                    "expected_label_baseline_id": _expected_label_baseline_id(expected["baseline_kind"]),
                }
            )
    return rows


def compute_publication_metrics(
    dataset: DynamicBenchmarkDataset,
    report_rows: Iterable[dict[str, Any]],
    *,
    oracle_rows: Iterable[dict[str, Any]] | None = None,
    bootstrap_iterations: int = 1000,
    bootstrap_seed: int = 1337,
) -> dict[str, Any]:
    """Compute publication-facing metrics over every row in the oracle."""

    comparison_rows = _comparison_records(dataset, report_rows, oracle_rows=oracle_rows)
    point = _publication_point_metrics(comparison_rows)
    intervals = _bootstrap_intervals(
        comparison_rows,
        iterations=bootstrap_iterations,
        seed=bootstrap_seed,
    )
    return {
        "rows": len(comparison_rows),
        "point": point,
        "confidence_intervals": intervals,
        "bootstrap_iterations": bootstrap_iterations,
        "bootstrap_seed": bootstrap_seed,
    }


def build_dynamic_quality_summary_rows(
    publication_metrics: dict[str, Any],
    *,
    baseline_id: str,
    task_success_summary: dict[str, Any] | None = None,
    scope: str = "full_oracle",
) -> list[dict[str, Any]]:
    """Flatten publication metrics and CIs into a CSV-friendly summary table."""

    point = publication_metrics.get("point", {})
    intervals = publication_metrics.get("confidence_intervals", {})
    task_success_summary = task_success_summary or {}

    metric_specs = [
        ("exact_status_accuracy", "exact_status_accuracy"),
        ("fail_precision_micro", "fail.micro.precision"),
        ("fail_recall_micro", "fail.micro.recall"),
        ("fail_f1_micro", "fail.micro.f1"),
        ("fail_precision_macro", "fail.macro.precision"),
        ("fail_recall_macro", "fail.macro.recall"),
        ("fail_f1_macro", "fail.macro.f1"),
        ("pass_precision_micro", "pass.micro.precision"),
        ("pass_recall_micro", "pass.micro.recall"),
        ("pass_f1_micro", "pass.micro.f1"),
        ("pass_accuracy", "pass.binary_accuracy"),
        ("pass_precision_macro", "pass.macro.precision"),
        ("pass_recall_macro", "pass.macro.recall"),
        ("pass_f1_macro", "pass.macro.f1"),
        ("not_applicable_accuracy", "not_applicable_accuracy"),
        ("incomplete_rate", "incomplete_rate"),
    ]
    rows: list[dict[str, Any]] = []
    for metric_name, path in metric_specs:
        numerator, denominator = _metric_fraction(point, metric_name)
        interval = intervals.get(metric_name, {})
        rows.append(
            {
                "baseline_id": baseline_id,
                "metric": metric_name,
                "value": _path_get(point, path),
                "ci_low": interval.get("ci_low"),
                "ci_high": interval.get("ci_high"),
                "ci_level": interval.get("ci_level", 0.95) if interval else "",
                "bootstrap_iterations": publication_metrics.get("bootstrap_iterations", ""),
                "bootstrap_seed": publication_metrics.get("bootstrap_seed", ""),
                "numerator": numerator,
                "denominator": denominator,
                "scope": scope,
            }
        )

    rows.extend(
        [
            {
                "baseline_id": baseline_id,
                "metric": "oracle_rows",
                "value": publication_metrics.get("rows", 0),
                "ci_low": "",
                "ci_high": "",
                "ci_level": "",
                "bootstrap_iterations": "",
                "bootstrap_seed": "",
                "numerator": publication_metrics.get("rows", 0),
                "denominator": publication_metrics.get("rows", 0),
                "scope": scope,
            },
            {
                "baseline_id": baseline_id,
                "metric": "task_success_rate",
                "value": task_success_summary.get("task_success_rate"),
                "ci_low": "",
                "ci_high": "",
                "ci_level": "",
                "bootstrap_iterations": "",
                "bootstrap_seed": "",
                "numerator": task_success_summary.get("task_success_count", 0),
                "denominator": task_success_summary.get("task_success_denominator", 0),
                "scope": f"{scope}:browser_use_tasks",
            },
        ]
    )
    return rows


def build_false_audit_rows(
    dataset: DynamicBenchmarkDataset,
    report_rows: Iterable[dict[str, Any]],
    *,
    oracle_rows: Iterable[dict[str, Any]] | None = None,
    target_statuses: Iterable[str] = AUDIT_TARGET_STATUSES,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build false-positive and false-negative audit rows for target statuses."""

    comparison_rows = _comparison_records(dataset, report_rows, oracle_rows=oracle_rows)
    targets = {str(status) for status in target_statuses}
    false_positives: list[dict[str, Any]] = []
    false_negatives: list[dict[str, Any]] = []

    for row in comparison_rows:
        for target in sorted(targets):
            expected_is_target = row["expected_status"] == target
            actual_is_target = row["actual_status"] == target
            audit_row = {
                "dataset_id": row["dataset_id"],
                "baseline_id": row.get("baseline_id", ""),
                "scenario_id": row["scenario_id"],
                "criterion_id": row["criterion_id"],
                "target_status": target,
                "oracle_status": row["expected_status"],
                "actual_status": row["actual_status"],
                "decision_level": row.get("decision_level", ""),
                "llm_status": row.get("llm_status", ""),
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
                "gate_decision": row.get("gate_decision", ""),
                "gate_reason": row.get("gate_reason", ""),
                "evidence_strength": row.get("evidence_strength", ""),
                "llm_error": row.get("llm_error", ""),
                "summary": row.get("summary", ""),
                "assessed": row.get("assessed", False),
                "run_status": row.get("run_status", ""),
                "scored": row.get("scored", ""),
                "unscored_reason": row.get("unscored_reason", ""),
                "runtime_trace_path": row.get("runtime_trace_path", ""),
                "formal_report_path": row.get("formal_report_path", ""),
            }
            if actual_is_target and not expected_is_target:
                false_positives.append({**audit_row, "audit_type": "false_positive"})
            if expected_is_target and not actual_is_target:
                false_negatives.append({**audit_row, "audit_type": "false_negative"})

    return false_positives, false_negatives


def _actual_by_key(report_rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], str]:
    output: dict[tuple[str, str], str] = {}
    for row in report_rows:
        scenario_id = str(row.get("scenario_id", ""))
        criterion_id = str(row.get("criterion_id", ""))
        if not scenario_id or not criterion_id:
            continue
        output[(scenario_id, criterion_id)] = _coerce_status_value(row.get("status"))
    return output


def _report_by_key(report_rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for row in report_rows:
        scenario_id = str(row.get("scenario_id", ""))
        criterion_id = str(row.get("criterion_id", ""))
        if not scenario_id or not criterion_id:
            continue
        output[(scenario_id, criterion_id)] = dict(row)
    return output


def _comparison_records(
    dataset: DynamicBenchmarkDataset,
    report_rows: Iterable[dict[str, Any]],
    *,
    oracle_rows: Iterable[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    report_by_key = _report_by_key(report_rows)
    records: list[dict[str, Any]] = []
    for expected in _expected_rows(dataset, oracle_rows):
        key = (str(expected["scenario_id"]), str(expected["criterion_id"]))
        actual = report_by_key.get(key, {})
        assessed = key in report_by_key
        actual_status = _coerce_status_value(actual.get("status", DynamicStatus.incomplete.value))
        records.append(
            {
                **actual,
                "dataset_id": expected["dataset_id"],
                "scenario_id": key[0],
                "criterion_id": key[1],
                "expected_status": _coerce_status_value(expected["expected_status"]),
                "actual_status": actual_status,
                "status": actual_status,
                "baseline_kind": expected["baseline_kind"],
                "assessed": assessed,
                "unassessed_marked_incomplete": not assessed,
                "matched": actual_status == _coerce_status_value(expected["expected_status"]),
            }
        )
    return records


def _publication_point_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    exact_matches = sum(1 for row in rows if row["actual_status"] == row["expected_status"])
    fail_metrics = _binary_metrics(rows, "fail")
    pass_metrics = _binary_metrics(rows, "pass")
    scenario_metrics = _macro_metrics_by_scenario(rows, ("fail", "pass"))
    expected_na = [row for row in rows if row["expected_status"] == DynamicStatus.not_applicable.value]
    na_correct = sum(1 for row in expected_na if row["actual_status"] == DynamicStatus.not_applicable.value)
    incomplete_count = sum(1 for row in rows if row["actual_status"] == DynamicStatus.incomplete.value)

    return {
        "oracle_rows": total,
        "exact_status_accuracy": _safe_div(exact_matches, total),
        "exact_status_correct": exact_matches,
        "fail": {
            "micro": fail_metrics,
            "macro": scenario_metrics["fail"],
        },
        "pass": {
            "micro": pass_metrics,
            "macro": scenario_metrics["pass"],
            "binary_accuracy": pass_metrics["accuracy"],
        },
        "not_applicable_accuracy": _safe_div(na_correct, len(expected_na)),
        "not_applicable_correct": na_correct,
        "not_applicable_expected": len(expected_na),
        "incomplete_rate": _safe_div(incomplete_count, total),
        "incomplete_count": incomplete_count,
    }


def _binary_metrics(rows: list[dict[str, Any]], target_status: str) -> dict[str, Any]:
    tp = sum(1 for row in rows if row["expected_status"] == target_status and row["actual_status"] == target_status)
    fp = sum(1 for row in rows if row["expected_status"] != target_status and row["actual_status"] == target_status)
    fn = sum(1 for row in rows if row["expected_status"] == target_status and row["actual_status"] != target_status)
    tn = sum(1 for row in rows if row["expected_status"] != target_status and row["actual_status"] != target_status)
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = _f1(precision, recall)
    return {
        "target_status": target_status,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "predicted": tp + fp,
        "expected": tp + fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": _safe_div(tp + tn, tp + fp + fn + tn),
    }


def _macro_metrics_by_scenario(rows: list[dict[str, Any]], target_statuses: Iterable[str]) -> dict[str, dict[str, Any]]:
    by_scenario: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_scenario[str(row["scenario_id"])].append(row)

    output: dict[str, dict[str, Any]] = {}
    for target in target_statuses:
        metric_values: dict[str, list[float]] = {"precision": [], "recall": [], "f1": [], "accuracy": []}
        for scenario_rows in by_scenario.values():
            metrics = _binary_metrics(scenario_rows, target)
            for metric_name in metric_values:
                value = metrics.get(metric_name)
                if value is not None:
                    metric_values[metric_name].append(float(value))
        output[target] = {
            metric_name: _mean(values) for metric_name, values in metric_values.items()
        }
        output[target]["scenarios"] = len(by_scenario)
    return output


def _bootstrap_intervals(rows: list[dict[str, Any]], *, iterations: int, seed: int) -> dict[str, dict[str, Any]]:
    if not rows or iterations <= 0:
        return {}

    by_scenario: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_scenario[str(row["scenario_id"])].append(row)
    scenario_ids = sorted(by_scenario)
    if not scenario_ids:
        return {}

    rng = random.Random(seed)
    samples: dict[str, list[float]] = defaultdict(list)
    for _ in range(iterations):
        sampled_rows: list[dict[str, Any]] = []
        for scenario_id in (rng.choice(scenario_ids) for _ in scenario_ids):
            sampled_rows.extend(by_scenario[scenario_id])
        metrics = _publication_point_metrics(sampled_rows)
        for metric_name, value in {
            "fail_precision_micro": metrics["fail"]["micro"]["precision"],
            "fail_recall_micro": metrics["fail"]["micro"]["recall"],
            "fail_f1_micro": metrics["fail"]["micro"]["f1"],
            "fail_precision_macro": metrics["fail"]["macro"]["precision"],
            "fail_recall_macro": metrics["fail"]["macro"]["recall"],
            "fail_f1_macro": metrics["fail"]["macro"]["f1"],
            "pass_precision_micro": metrics["pass"]["micro"]["precision"],
            "pass_recall_micro": metrics["pass"]["micro"]["recall"],
            "pass_f1_micro": metrics["pass"]["micro"]["f1"],
            "pass_precision_macro": metrics["pass"]["macro"]["precision"],
            "pass_recall_macro": metrics["pass"]["macro"]["recall"],
            "pass_f1_macro": metrics["pass"]["macro"]["f1"],
            "pass_accuracy": metrics["pass"]["binary_accuracy"],
            "not_applicable_accuracy": metrics["not_applicable_accuracy"],
            "incomplete_rate": metrics["incomplete_rate"],
            "exact_status_accuracy": metrics["exact_status_accuracy"],
        }.items():
            if value is not None:
                samples[metric_name].append(float(value))

    return {
        metric_name: {
            "metric": metric_name,
            "ci_low": _percentile(values, 0.025),
            "ci_high": _percentile(values, 0.975),
            "ci_level": 0.95,
            "samples": len(values),
        }
        for metric_name, values in sorted(samples.items())
    }


def _metric_fraction(point: dict[str, Any], metric_name: str) -> tuple[Any, Any]:
    if metric_name == "exact_status_accuracy":
        return point.get("exact_status_correct"), point.get("oracle_rows")
    if metric_name.startswith("fail_"):
        return _binary_fraction(point["fail"]["micro"], metric_name)
    if metric_name.startswith("pass_") and metric_name != "pass_accuracy":
        return _binary_fraction(point["pass"]["micro"], metric_name)
    if metric_name == "pass_accuracy":
        micro = point["pass"]["micro"]
        return micro.get("tp", 0) + micro.get("tn", 0), point.get("oracle_rows")
    if metric_name == "not_applicable_accuracy":
        return point.get("not_applicable_correct"), point.get("not_applicable_expected")
    if metric_name == "incomplete_rate":
        return point.get("incomplete_count"), point.get("oracle_rows")
    return "", ""


def _binary_fraction(metrics: dict[str, Any], metric_name: str) -> tuple[Any, Any]:
    if metric_name.endswith("_precision_micro"):
        return metrics.get("tp"), metrics.get("predicted")
    if metric_name.endswith("_recall_micro"):
        return metrics.get("tp"), metrics.get("expected")
    if metric_name.endswith("_f1_micro"):
        return "", ""
    return "", ""


def _path_get(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _safe_div(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return numerator / denominator


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return 2 * precision * recall / (precision + recall)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _expected_rows(
    dataset: DynamicBenchmarkDataset,
    oracle_rows: Iterable[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if oracle_rows is not None:
        return [dict(row) for row in oracle_rows]
    return flatten_expected_results(dataset)


def _status_values(values: Iterable[DynamicStatus | str]) -> set[str]:
    return {_coerce_status_value(value) for value in values}


def _coerce_status_value(value: Any) -> str:
    if isinstance(value, DynamicStatus):
        return value.value
    return str(value)


def _expected_label_baseline_id(baseline_kind: str) -> str:
    if baseline_kind == "human_oracle":
        return "human-oracle"
    if baseline_kind == "synthetic_fixture":
        return "synthetic-fixture"
    return baseline_kind
