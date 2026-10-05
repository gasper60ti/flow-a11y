"""Offline binary analysis for the FlowA11y v5 publication artifact.

The analysis derives binary defect decisions from saved five-status result tables.
It never opens a browser and never calls Gemini.  The source inputs are the fixed
V4 765-row evaluation frame and ten saved repeated-run table sets.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import PercentFormatter


PRIMARY_KEYS = ["dataset_id", "scenario_id", "criterion_id"]
STATUS_TO_DECISION = {
    "fail": "TRIGGER",
    "warning": "NOT_TRIGGER",
    "pass": "NOT_TRIGGER",
    "not_applicable": "NOT_TRIGGER",
    "incomplete": "NOT_TRIGGER",
}
NOT_TRIGGER_REASONS = {
    "warning": "weak_signal",
    "pass": "assessed_pass",
    "not_applicable": "not_applicable",
    "incomplete": "insufficient_evidence",
}
REASON_ORDER = ["weak_signal", "assessed_pass", "not_applicable", "insufficient_evidence"]
DECISION_ORDER = ["TRIGGER", "NOT_TRIGGER"]


@dataclass(frozen=True)
class Cohort:
    cohort_id: str
    cohort: str
    result_prefix: str
    runs: int
    comparator_id: str
    comparator: str


COHORTS = (
    Cohort(
        "naive",
        "Original naive comparison",
        "results_real_19_repeat_2026-09-28_run",
        4,
        "naive-browser-use-global-audit",
        "Naive baseline",
    ),
    Cohort(
        "baseline_2",
        "Baseline 2.0 matched trace",
        "results_real_19_baseline2_run",
        3,
        "baseline-2.0-matched-trace-generic-judge",
        "Baseline 2.0",
    ),
    Cohort(
        "baseline_3",
        "Baseline 3.0 global-evidence ablation",
        "results_real_19_baseline3_run",
        3,
        "baseline-3.0-matched-global-evidence-ablation",
        "Baseline 3.0",
    ),
)


class ArtifactValidationError(ValueError):
    """Raised when a saved artifact does not meet the release contract."""


def find_project_root(start: Path | None = None) -> Path:
    """Find the directory containing the ``benchmarking`` package."""

    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "benchmarking").is_dir():
            return candidate
    module_root = Path(__file__).resolve().parents[2]
    if (module_root / "benchmarking").is_dir():
        return module_root
    raise ArtifactValidationError("Could not locate the FlowA11y project root.")


def package_root(project_root: Path) -> Path:
    return project_root / "benchmarking" / "dynamic" / "publication_results" / "v5"


def input_root(project_root: Path) -> Path:
    override = os.environ.get("FLOWA11Y_PUBLICATION_INPUT_DIR")
    return Path(override).expanduser().resolve() if override else package_root(project_root) / "source_inputs"


def default_output_dir() -> Path:
    override = os.environ.get("FLOWA11Y_PUBLICATION_OUTPUT_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(tempfile.gettempdir()) / "flow-a11y-v5-analysis"


def as_bool(values: pd.Series) -> pd.Series:
    return values.astype(str).str.strip().str.casefold().isin({"true", "1", "yes"})


def normalise_status(values: pd.Series, label: str) -> pd.Series:
    normalised = values.astype("string").str.strip().str.casefold()
    unknown = sorted(set(normalised.dropna()) - set(STATUS_TO_DECISION))
    if unknown:
        raise ArtifactValidationError(f"Unexpected {label} status values: {unknown}")
    if normalised.isna().any():
        raise ArtifactValidationError(f"Missing {label} status values")
    return normalised


def uppercase_or_empty(values: pd.Series) -> pd.Series:
    return values.astype("string").str.upper().fillna("")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def csv_with_row_number(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["_source_csv_row"] = np.arange(2, len(frame) + 2)
    return frame


def relpath(path: Path, project_root: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()


def assert_unique(frame: pd.DataFrame, keys: list[str], label: str) -> None:
    duplicates = int(frame.duplicated(keys).sum())
    if duplicates:
        raise ArtifactValidationError(f"{label} has {duplicates} duplicate primary keys")


def safe_div(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else float("nan")


def sample_sd(values: pd.Series) -> float:
    return float(values.std(ddof=1)) if len(values) > 1 else float("nan")


def _iter_method_specs(inputs: Path) -> Iterable[dict[str, str | Path]]:
    for cohort in COHORTS:
        for run_index in range(1, cohort.runs + 1):
            result_dir = inputs / f"{cohort.result_prefix}{run_index}"
            for method_id, method, role in (
                ("gemini-dynamic-wcag", "FlowA11y", "primary"),
                (cohort.comparator_id, cohort.comparator, "comparator"),
            ):
                yield {
                    "cohort_id": cohort.cohort_id,
                    "cohort": cohort.cohort,
                    "run": f"Run {run_index}",
                    "result_dir": result_dir,
                    "method_id": method_id,
                    "method": method,
                    "method_role": role,
                }


def _input_manifest_row(
    *,
    source_role: str,
    cohort: str,
    run: str,
    method: str,
    path: Path,
    project_root: Path,
) -> dict[str, str]:
    return {
        "source_role": source_role,
        "cohort": cohort,
        "run": run,
        "method": method,
        "artifact_path": relpath(path, project_root),
        "sha256": sha256(path),
    }


def load_saved_decisions(project_root: Path) -> dict[str, pd.DataFrame]:
    """Load source tables, align them to V4, and derive binary decisions."""

    inputs = input_root(project_root)
    v4_audit_path = inputs / "v4_primary" / "dynamic_scoring_audit.csv"
    fixture_path = inputs / "v4_primary" / "real_oracle_labels.csv"
    if not v4_audit_path.is_file() or not fixture_path.is_file():
        raise FileNotFoundError("The v5 V4-primary source inputs are missing.")

    v4_audit = csv_with_row_number(v4_audit_path)
    v4_primary = v4_audit.loc[as_bool(v4_audit["scored"])].copy()
    if len(v4_primary) != 765:
        raise ArtifactValidationError(f"Expected 765 scored V4 rows, found {len(v4_primary)}")
    assert_unique(v4_primary, PRIMARY_KEYS, "V4 primary audit")
    if v4_primary["scenario_id"].nunique() != 17:
        raise ArtifactValidationError("The V4 primary frame does not contain 17 scenarios.")
    if not v4_primary.groupby("scenario_id")["criterion_id"].nunique().eq(45).all():
        raise ArtifactValidationError("Every V4 primary scenario must contain 45 criteria.")
    v4_primary["_oracle_status"] = normalise_status(v4_primary["oracle_status"], "V4 oracle")

    fixture = pd.read_csv(fixture_path)
    fixture = fixture.loc[:, ["scenario_id", "criterion_id", "expected_status"]].copy()
    fixture["_fixture_status"] = normalise_status(fixture["expected_status"], "V4 fixture oracle")
    assert_unique(fixture, ["scenario_id", "criterion_id"], "V4 fixture oracle")

    primary = v4_primary[PRIMARY_KEYS + ["_oracle_status", "_source_csv_row"]].rename(
        columns={"_source_csv_row": "evaluation_oracle_row_number"}
    )
    primary["oracle_status_original"] = uppercase_or_empty(primary["_oracle_status"])
    primary["oracle_binary_decision"] = primary["_oracle_status"].map(STATUS_TO_DECISION)
    primary["evaluation_oracle_table"] = relpath(v4_audit_path, project_root)
    primary["evaluation_oracle_fixture"] = relpath(fixture_path, project_root)

    fixture_check = primary.merge(
        fixture[["scenario_id", "criterion_id", "_fixture_status"]],
        on=["scenario_id", "criterion_id"],
        how="left",
        validate="one_to_one",
    )
    if fixture_check["_fixture_status"].isna().any() or not fixture_check["_oracle_status"].eq(
        fixture_check["_fixture_status"]
    ).all():
        raise ArtifactValidationError("The anonymized full oracle fixture does not match the V4 primary frame.")

    manifest_rows = [
        _input_manifest_row(
            source_role="fixed evaluation oracle",
            cohort="V4 primary frame",
            run="V4 primary",
            method="All methods",
            path=v4_audit_path,
            project_root=project_root,
        ),
        _input_manifest_row(
            source_role="full oracle fixture validation",
            cohort="V4 primary frame",
            run="V4 primary",
            method="All methods",
            path=fixture_path,
            project_root=project_root,
        ),
    ]
    row_frames: list[pd.DataFrame] = []

    for spec in _iter_method_specs(inputs):
        result_dir = Path(spec["result_dir"])
        tables = result_dir / "tables"
        record_path = tables / (
            "dynamic_finding_status_records.csv"
            if spec["method_id"] == "gemini-dynamic-wcag"
            else "baseline_finding_status_records.csv"
        )
        native_path = tables / "attempted_baseline_comparison.csv"
        if not record_path.is_file() or not native_path.is_file():
            raise FileNotFoundError(f"Saved input table missing for {result_dir.name}")

        records = csv_with_row_number(record_path)
        records = records.loc[records["baseline_id"].eq(spec["method_id"])].copy()
        if len(records) != 855:
            raise ArtifactValidationError(
                f"Expected 855 saved statuses for {spec['cohort']} {spec['run']} {spec['method']}; found {len(records)}"
            )
        assert_unique(records, PRIMARY_KEYS, f"status records for {spec['cohort']} {spec['run']} {spec['method']}")
        records["_tool_status"] = normalise_status(records["status"], "tool")
        record_columns = PRIMARY_KEYS + [
            "_tool_status",
            "scored",
            "unscored_reason",
            "_source_csv_row",
        ]
        records = records.loc[:, record_columns].rename(
            columns={
                "scored": "source_tool_scored",
                "unscored_reason": "source_tool_unscored_reason",
                "_source_csv_row": "source_status_record_row_number",
            }
        )

        merged = primary.merge(records, on=PRIMARY_KEYS, how="left", validate="one_to_one")
        if merged["_tool_status"].isna().any():
            raise ArtifactValidationError(f"A stored tool status is missing from the V4 frame for {result_dir.name}")

        native = csv_with_row_number(native_path)
        native = native.loc[native["baseline_id"].eq(spec["method_id"])].copy()
        if len(native) != 315:
            raise ArtifactValidationError(
                f"Expected 315 sparse native labels for {spec['cohort']} {spec['run']} {spec['method']}; found {len(native)}"
            )
        assert_unique(native, PRIMARY_KEYS, f"native rows for {spec['cohort']} {spec['run']} {spec['method']}")
        native["_native_status"] = normalise_status(native["expected_status"], "native oracle")
        native["_native_actual_status"] = normalise_status(native["actual_status"], "native tool")
        native_check = native.merge(
            records[PRIMARY_KEYS + ["_tool_status"]], on=PRIMARY_KEYS, how="left", validate="one_to_one"
        )
        if not native_check["_native_actual_status"].eq(native_check["_tool_status"]).all():
            raise ArtifactValidationError(f"Native table and status table disagree in {result_dir.name}")
        native = native.loc[:, PRIMARY_KEYS + ["_native_status", "assessed", "_source_csv_row"]].rename(
            columns={
                "assessed": "source_native_oracle_assessed",
                "_source_csv_row": "source_native_oracle_row_number",
            }
        )
        merged = merged.merge(native, on=PRIMARY_KEYS, how="left", validate="one_to_one")

        merged["cohort"] = spec["cohort"]
        merged["cohort_id"] = spec["cohort_id"]
        merged["run"] = spec["run"]
        merged["method"] = spec["method"]
        merged["method_id"] = spec["method_id"]
        merged["method_role"] = spec["method_role"]
        merged["tool_status_original"] = uppercase_or_empty(merged["_tool_status"])
        merged["tool_binary_decision"] = merged["_tool_status"].map(STATUS_TO_DECISION)
        merged["not_trigger_reason"] = merged["_tool_status"].map(NOT_TRIGGER_REASONS).fillna("")
        merged["source_tool_scored"] = as_bool(merged["source_tool_scored"])
        merged["source_native_oracle_available"] = merged["_native_status"].notna()
        merged["source_native_oracle_status_original"] = uppercase_or_empty(merged["_native_status"])
        merged["source_native_oracle_binary_decision"] = merged["_native_status"].map(STATUS_TO_DECISION).fillna("")
        merged["source_native_oracle_matches_v4"] = np.where(
            merged["source_native_oracle_available"],
            merged["_native_status"].eq(merged["_oracle_status"]),
            pd.NA,
        )
        merged["source_status_table"] = relpath(record_path, project_root)
        merged["source_native_oracle_table"] = relpath(native_path, project_root)
        merged["confusion_outcome"] = np.select(
            [
                merged["oracle_binary_decision"].eq("TRIGGER") & merged["tool_binary_decision"].eq("TRIGGER"),
                merged["oracle_binary_decision"].eq("NOT_TRIGGER") & merged["tool_binary_decision"].eq("TRIGGER"),
                merged["oracle_binary_decision"].eq("NOT_TRIGGER")
                & merged["tool_binary_decision"].eq("NOT_TRIGGER"),
                merged["oracle_binary_decision"].eq("TRIGGER") & merged["tool_binary_decision"].eq("NOT_TRIGGER"),
            ],
            ["TP", "FP", "TN", "FN"],
            default="UNMAPPED",
        )
        row_frames.append(merged)
        manifest_rows.extend(
            [
                _input_manifest_row(
                    source_role="stored five-status tool output",
                    cohort=str(spec["cohort"]),
                    run=str(spec["run"]),
                    method=str(spec["method"]),
                    path=record_path,
                    project_root=project_root,
                ),
                _input_manifest_row(
                    source_role="native sparse oracle provenance",
                    cohort=str(spec["cohort"]),
                    run=str(spec["run"]),
                    method=str(spec["method"]),
                    path=native_path,
                    project_root=project_root,
                ),
            ]
        )

    rows = pd.concat(row_frames, ignore_index=True)
    row_columns = [
        "cohort",
        "cohort_id",
        "run",
        "method",
        "method_id",
        "method_role",
        "dataset_id",
        "scenario_id",
        "criterion_id",
        "oracle_status_original",
        "oracle_binary_decision",
        "tool_status_original",
        "tool_binary_decision",
        "not_trigger_reason",
        "confusion_outcome",
        "source_tool_scored",
        "source_tool_unscored_reason",
        "source_native_oracle_available",
        "source_native_oracle_status_original",
        "source_native_oracle_binary_decision",
        "source_native_oracle_matches_v4",
        "source_native_oracle_assessed",
        "evaluation_oracle_table",
        "evaluation_oracle_fixture",
        "evaluation_oracle_row_number",
        "source_status_table",
        "source_status_record_row_number",
        "source_native_oracle_table",
        "source_native_oracle_row_number",
    ]
    rows = rows.loc[:, row_columns].copy()
    return {
        "binary_decision_rows": rows,
        "binary_source_manifest": pd.DataFrame(manifest_rows),
    }


def derive_binary_tables(binary_decision_rows: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Validate mappings and calculate per-run and repeated-run metrics."""

    group_columns = ["cohort", "cohort_id", "run", "method", "method_id", "method_role"]
    validation_rows: list[dict[str, Any]] = []
    metrics_rows: list[dict[str, Any]] = []
    matrix_rows: list[dict[str, Any]] = []
    reason_rows: list[dict[str, Any]] = []
    provenance_rows: list[dict[str, Any]] = []

    for group_key, group in binary_decision_rows.groupby(group_columns, sort=False, dropna=False):
        meta = dict(zip(group_columns, group_key))
        total = len(group)
        unique_primary_keys = int(group[PRIMARY_KEYS].drop_duplicates().shape[0])
        statuses_mapped = group["tool_binary_decision"].isin(DECISION_ORDER).all()
        oracle_mapped = group["oracle_binary_decision"].isin(DECISION_ORDER).all()
        not_trigger = group["tool_binary_decision"].eq("NOT_TRIGGER")
        reasons_complete = group.loc[not_trigger, "not_trigger_reason"].isin(REASON_ORDER).all()
        trigger_reasons_blank = group.loc[~not_trigger, "not_trigger_reason"].eq("").all()
        mapped_once = (
            total == 765
            and unique_primary_keys == 765
            and statuses_mapped
            and oracle_mapped
            and reasons_complete
            and trigger_reasons_blank
            and group["confusion_outcome"].isin({"TP", "FP", "TN", "FN"}).all()
        )
        validation_rows.append(
            {
                **meta,
                "required_denominator": 765,
                "row_count": total,
                "unique_primary_keys": unique_primary_keys,
                "tool_statuses_mapped": int(statuses_mapped),
                "oracle_statuses_mapped": int(oracle_mapped),
                "not_trigger_reasons_complete": int(reasons_complete),
                "trigger_reasons_blank": int(trigger_reasons_blank),
                "source_tool_scored_rows": int(group["source_tool_scored"].sum()),
                "source_tool_unscored_rows": int((~group["source_tool_scored"]).sum()),
                "all_rows_mapped_exactly_once": int(mapped_once),
            }
        )
        if not mapped_once:
            raise ArtifactValidationError(f"Binary mapping validation failed for {meta}")

        tp = int(group["confusion_outcome"].eq("TP").sum())
        fp = int(group["confusion_outcome"].eq("FP").sum())
        tn = int(group["confusion_outcome"].eq("TN").sum())
        fn = int(group["confusion_outcome"].eq("FN").sum())
        if tp + fp + tn + fn != 765:
            raise ArtifactValidationError(f"Confusion counts do not equal 765 for {meta}")
        precision = safe_div(tp, tp + fp)
        recall = safe_div(tp, tp + fn)
        specificity = safe_div(tn, tn + fp)
        metrics_rows.append(
            {
                **meta,
                "denominator": 765,
                "TP": tp,
                "FP": fp,
                "TN": tn,
                "FN": fn,
                "precision": precision,
                "recall": recall,
                "f1": safe_div(2 * tp, 2 * tp + fp + fn),
                "mcc": safe_div(
                    tp * tn - fp * fn,
                    float(np.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))),
                ),
                "specificity": specificity,
                "balanced_accuracy": (recall + specificity) / 2,
                "accuracy": safe_div(tp + tn, 765),
                "predicted_trigger_rate": safe_div(tp + fp, 765),
                "predicted_not_trigger_rate": safe_div(tn + fn, 765),
                "oracle_trigger_rate": safe_div(tp + fn, 765),
                "oracle_not_trigger_rate": safe_div(tn + fp, 765),
            }
        )
        for oracle_decision in DECISION_ORDER:
            for tool_decision in DECISION_ORDER:
                matrix_rows.append(
                    {
                        **meta,
                        "oracle_binary_decision": oracle_decision,
                        "tool_binary_decision": tool_decision,
                        "count": int(
                            (
                                group["oracle_binary_decision"].eq(oracle_decision)
                                & group["tool_binary_decision"].eq(tool_decision)
                            ).sum()
                        ),
                    }
                )
        total_not_trigger = int(not_trigger.sum())
        for reason in REASON_ORDER:
            count = int(group["not_trigger_reason"].eq(reason).sum())
            reason_rows.append(
                {
                    **meta,
                    "not_trigger_reason": reason,
                    "count": count,
                    "share_of_not_trigger": safe_div(count, total_not_trigger),
                    "share_of_765_rows": safe_div(count, 765),
                }
            )
        native_available = group["source_native_oracle_available"]
        available_count = int(native_available.sum())
        matching_count = int(group.loc[native_available, "source_native_oracle_matches_v4"].fillna(False).sum())
        provenance_rows.append(
            {
                **meta,
                "fixed_v4_rows": 765,
                "native_oracle_labels_available": available_count,
                "native_oracle_labels_missing": 765 - available_count,
                "native_oracle_matches_v4": matching_count,
                "native_oracle_differs_from_v4": available_count - matching_count,
            }
        )

    metrics = pd.DataFrame(metrics_rows)
    summary_metrics = [
        "TP",
        "FP",
        "TN",
        "FN",
        "precision",
        "recall",
        "f1",
        "mcc",
        "specificity",
        "balanced_accuracy",
        "accuracy",
        "predicted_trigger_rate",
        "predicted_not_trigger_rate",
    ]
    summary_rows: list[dict[str, Any]] = []
    summary_groups = ["cohort", "cohort_id", "method", "method_id", "method_role"]
    for group_key, group in metrics.groupby(summary_groups, sort=False, dropna=False):
        meta = dict(zip(summary_groups, group_key))
        row: dict[str, Any] = {**meta, "runs": len(group), "rows_per_run": 765}
        for metric in summary_metrics:
            row[f"mean_{metric}"] = float(group[metric].mean())
            row[f"sd_{metric}"] = sample_sd(group[metric])
        summary_rows.append(row)

    reasons = pd.DataFrame(reason_rows)
    reason_summary_rows: list[dict[str, Any]] = []
    reason_groups = ["cohort", "cohort_id", "method", "method_id", "method_role", "not_trigger_reason"]
    for group_key, group in reasons.groupby(reason_groups, sort=False, dropna=False):
        meta = dict(zip(reason_groups, group_key))
        reason_summary_rows.append(
            {
                **meta,
                "runs": len(group),
                "mean_count": float(group["count"].mean()),
                "sd_count": sample_sd(group["count"]),
                "mean_share_of_not_trigger": float(group["share_of_not_trigger"].mean()),
                "sd_share_of_not_trigger": sample_sd(group["share_of_not_trigger"]),
                "mean_share_of_765_rows": float(group["share_of_765_rows"].mean()),
                "sd_share_of_765_rows": sample_sd(group["share_of_765_rows"]),
            }
        )

    return {
        "binary_mapping_validation": pd.DataFrame(validation_rows),
        "binary_metrics_by_run": metrics,
        "binary_confusion_counts": metrics[group_columns + ["denominator", "TP", "FP", "TN", "FN"]].copy(),
        "binary_confusion_matrix": pd.DataFrame(matrix_rows),
        "binary_not_trigger_reasons": reasons,
        "binary_not_trigger_reason_repeat_summary": pd.DataFrame(reason_summary_rows),
        "binary_oracle_provenance": pd.DataFrame(provenance_rows),
        "binary_repeat_summary": pd.DataFrame(summary_rows),
    }


def _plot_metric_summary(summary: pd.DataFrame, output_path: Path) -> None:
    metrics = [("Precision", "precision"), ("Recall", "recall"), ("F1", "f1"), ("MCC", "mcc"), ("Balanced accuracy", "balanced_accuracy")]
    cohorts = [cohort.cohort for cohort in COHORTS]
    fig, axes = plt.subplots(1, len(cohorts), figsize=(15, 4.8), sharey=True)
    for ax, cohort_name in zip(axes, cohorts, strict=True):
        frame = summary.loc[summary["cohort"].eq(cohort_name)].copy()
        methods = frame["method"].tolist()
        x = np.arange(len(metrics))
        width = 0.78 / max(1, len(methods))
        for index, (_, row) in enumerate(frame.iterrows()):
            offsets = x + (index - (len(methods) - 1) / 2) * width
            means = [row[f"mean_{column}"] for _, column in metrics]
            errors = [row[f"sd_{column}"] for _, column in metrics]
            ax.bar(offsets, means, width, yerr=errors, capsize=3, label=row["method"])
        ax.set_title(cohort_name)
        ax.set_xticks(x, [name.replace("Balanced ", "Bal. ") for name, _ in metrics], rotation=35, ha="right")
        ax.yaxis.set_major_formatter(PercentFormatter(1.0))
        ax.set_ylim(0, 1)
        ax.grid(axis="y", alpha=0.25)
    axes[0].set_ylabel("Score")
    axes[-1].legend(loc="lower right", fontsize=8)
    fig.suptitle("Binary defect-decision metrics: mean ± sample SD", y=1.02)
    fig.tight_layout()
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def _plot_paired_deltas(metrics: pd.DataFrame, output_path: Path) -> None:
    selected = ["precision", "recall", "f1", "mcc", "balanced_accuracy"]
    labels = ["Precision", "Recall", "F1", "MCC", "Bal. acc."]
    cohorts = [cohort for cohort in COHORTS if cohort.cohort_id in {"baseline_2", "baseline_3"}]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)
    for ax, cohort in zip(axes, cohorts, strict=True):
        frame = metrics.loc[metrics["cohort_id"].eq(cohort.cohort_id)]
        primary = frame.loc[frame["method_role"].eq("primary")].set_index("run")
        comparator = frame.loc[frame["method_role"].eq("comparator")].set_index("run")
        deltas = primary[selected] - comparator[selected]
        means = deltas.mean(axis=0)
        errors = deltas.std(axis=0, ddof=1)
        ax.bar(np.arange(len(selected)), means, yerr=errors, capsize=4, color="#3973ac")
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_title(cohort.cohort)
        ax.set_xticks(np.arange(len(selected)), labels, rotation=35, ha="right")
        ax.yaxis.set_major_formatter(PercentFormatter(1.0))
        ax.grid(axis="y", alpha=0.25)
    axes[0].set_ylabel("FlowA11y − baseline")
    fig.suptitle("Matched-input per-run binary metric differences", y=1.02)
    fig.tight_layout()
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def _plot_not_trigger_reasons(reason_summary: pd.DataFrame, output_path: Path) -> None:
    rows: list[tuple[str, str, list[float]]] = []
    for cohort in COHORTS:
        frame = reason_summary.loc[reason_summary["cohort_id"].eq(cohort.cohort_id)]
        for method in frame["method"].drop_duplicates().tolist():
            method_frame = frame.loc[frame["method"].eq(method)].set_index("not_trigger_reason")
            rows.append(
                (cohort.cohort, method, [float(method_frame.loc[reason, "mean_share_of_not_trigger"]) for reason in REASON_ORDER])
            )
    labels = [f"{cohort}\n{method}" for cohort, method, _ in rows]
    fig, ax = plt.subplots(figsize=(12, 5))
    bottoms = np.zeros(len(rows))
    colors = ["#9ecae1", "#74c476", "#fdae6b", "#bcbddc"]
    for reason, color, index in zip(REASON_ORDER, colors, range(len(REASON_ORDER)), strict=True):
        values = np.array([parts[index] for _, _, parts in rows])
        ax.bar(np.arange(len(rows)), values, bottom=bottoms, label=reason.replace("_", " "), color=color)
        bottoms += values
    ax.set_xticks(np.arange(len(rows)), labels, rotation=35, ha="right")
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.set_ylim(0, 1)
    ax.set_ylabel("Share of NOT_TRIGGER outputs")
    ax.set_title("Reasons retained for NOT_TRIGGER outputs")
    ax.legend(ncol=2, fontsize=8)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def _plot_output_rates(summary: pd.DataFrame, output_path: Path) -> None:
    cohorts = [cohort.cohort for cohort in COHORTS]
    fig, axes = plt.subplots(1, len(cohorts), figsize=(14, 4.5), sharey=True)
    for ax, cohort_name in zip(axes, cohorts, strict=True):
        frame = summary.loc[summary["cohort"].eq(cohort_name)]
        methods = frame["method"].tolist()
        x = np.arange(len(methods))
        width = 0.34
        trigger = frame["mean_predicted_trigger_rate"].to_numpy()
        trigger_sd = frame["sd_predicted_trigger_rate"].to_numpy()
        not_trigger = frame["mean_predicted_not_trigger_rate"].to_numpy()
        not_trigger_sd = frame["sd_predicted_not_trigger_rate"].to_numpy()
        ax.bar(x - width / 2, trigger, width, yerr=trigger_sd, capsize=3, label="TRIGGER", color="#d95f02")
        ax.bar(x + width / 2, not_trigger, width, yerr=not_trigger_sd, capsize=3, label="NOT_TRIGGER", color="#1b9e77")
        ax.set_title(cohort_name)
        ax.set_xticks(x, methods, rotation=35, ha="right")
        ax.yaxis.set_major_formatter(PercentFormatter(1.0))
        ax.set_ylim(0, 1)
        ax.grid(axis="y", alpha=0.25)
    axes[0].set_ylabel("Output rate")
    axes[-1].legend(loc="upper right")
    fig.suptitle("Automatic defect-report and no-report rates", y=1.02)
    fig.tight_layout()
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def _write_report(tables: dict[str, pd.DataFrame], output_path: Path) -> None:
    summary = tables["binary_repeat_summary"]
    lines = [
        "# Derived binary defect-decision report",
        "",
        "This report derives binary decisions from saved five-status artifacts only. It does not rerun browser-use, Gemini, or any evaluation, and the original five-status artifacts remain unchanged.",
        "",
        "The fixed evaluation frame contains 765 rows across 17 scenarios and 45 criteria per scenario. Every method/run is aligned to that same frame.",
        "",
        "`TRIGGER` maps from `FAIL`. `NOT_TRIGGER` maps from `WARNING`, `PASS`, `NOT_APPLICABLE`, and `INCOMPLETE`. `NOT_TRIGGER` means no automatic defect report from the observed trace; it does not establish accessibility compliance.",
        "",
        "| Cohort | Method | Runs | Precision | Recall | F1 | MCC | Balanced accuracy |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for _, row in summary.iterrows():
        fmt = lambda metric: f"{row[f'mean_{metric}']:.1%} ± {row[f'sd_{metric}']:.1%}"
        lines.append(
            f"| {row['cohort']} | {row['method']} | {int(row['runs'])} | {fmt('precision')} | {fmt('recall')} | {fmt('f1')} | {fmt('mcc')} | {fmt('balanced_accuracy')} |"
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_derivation_manifest(
    tables: dict[str, pd.DataFrame],
    output_dir: Path,
    paths: dict[str, Path],
) -> Path:
    """Map every release output to its transitive saved-input dependencies."""

    source_inputs = (
        tables["binary_source_manifest"]
        .loc[:, ["artifact_path", "sha256"]]
        .drop_duplicates()
        .sort_values("artifact_path")
        .reset_index(drop=True)
    )
    if len(source_inputs) != 32:
        raise ArtifactValidationError(
            f"Expected 32 unique saved source inputs, found {len(source_inputs)}"
        )

    derived_artifacts = {
        path.relative_to(output_dir).as_posix()
        for path in paths.values()
        if path.is_file()
    }
    derived_artifacts.add("binary_derivation_manifest.csv")
    rows: list[dict[str, str | int]] = []
    for derived_artifact in sorted(derived_artifacts):
        if derived_artifact == "binary_mapping_contract.csv":
            rows.append(
                {
                    "derived_artifact": derived_artifact,
                    "derivation_kind": "static binary mapping contract",
                    "source_input_count": 0,
                    "source_artifact": "",
                    "source_sha256": "",
                }
            )
            continue
        for _, source in source_inputs.iterrows():
            rows.append(
                {
                    "derived_artifact": derived_artifact,
                    "derivation_kind": "offline analysis transitive dependency",
                    "source_input_count": len(source_inputs),
                    "source_artifact": str(source["artifact_path"]),
                    "source_sha256": str(source["sha256"]),
                }
            )
    manifest_path = output_dir / "binary_derivation_manifest.csv"
    pd.DataFrame(rows).to_csv(manifest_path, index=False)
    return manifest_path


def write_analysis_outputs(tables: dict[str, pd.DataFrame], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    exports = {
        "binary_decision_rows": "binary_decision_rows.csv",
        "binary_mapping_contract": "binary_mapping_contract.csv",
        "binary_mapping_validation": "binary_mapping_validation.csv",
        "binary_confusion_counts": "binary_confusion_counts.csv",
        "binary_confusion_matrix": "binary_confusion_matrix.csv",
        "binary_metrics_by_run": "binary_metrics_by_run.csv",
        "binary_repeat_summary": "binary_repeat_summary.csv",
        "binary_not_trigger_reasons": "binary_not_trigger_reasons.csv",
        "binary_not_trigger_reason_repeat_summary": "binary_not_trigger_reason_repeat_summary.csv",
        "binary_oracle_provenance": "binary_oracle_provenance.csv",
        "binary_source_manifest": "binary_source_manifest.csv",
    }
    paths: dict[str, Path] = {}
    for key, filename in exports.items():
        path = output_dir / filename
        tables[key].to_csv(path, index=False)
        paths[key] = path
    report_path = output_dir / "binary_report.md"
    _write_report(tables, report_path)
    paths["binary_report"] = report_path

    figure_dir = output_dir / "figures"
    figure_dir.mkdir(exist_ok=True)
    figure_paths = {
        "binary_metrics_by_cohort_figure": figure_dir / "binary_metrics_by_cohort.png",
        "binary_matched_paired_deltas_figure": figure_dir / "binary_matched_paired_deltas.png",
        "binary_not_trigger_reason_composition_figure": figure_dir / "binary_not_trigger_reason_composition.png",
        "binary_trigger_not_trigger_rates_figure": figure_dir / "binary_trigger_not_trigger_rates.png",
    }
    _plot_metric_summary(tables["binary_repeat_summary"], figure_paths["binary_metrics_by_cohort_figure"])
    _plot_paired_deltas(tables["binary_metrics_by_run"], figure_paths["binary_matched_paired_deltas_figure"])
    _plot_not_trigger_reasons(
        tables["binary_not_trigger_reason_repeat_summary"],
        figure_paths["binary_not_trigger_reason_composition_figure"],
    )
    _plot_output_rates(tables["binary_repeat_summary"], figure_paths["binary_trigger_not_trigger_rates_figure"])
    paths.update(figure_paths)
    paths["figures"] = figure_dir
    paths["binary_derivation_manifest"] = _write_derivation_manifest(tables, output_dir, paths)
    return paths


def run_analysis(
    project_root: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Run the complete offline binary analysis and write CSV/figure outputs."""

    root = find_project_root(project_root)
    loaded = load_saved_decisions(root)
    mapping_contract = pd.DataFrame(
        [
            {"original_status": "FAIL", "binary_decision": "TRIGGER", "not_trigger_reason": ""},
            {"original_status": "WARNING", "binary_decision": "NOT_TRIGGER", "not_trigger_reason": "weak_signal"},
            {"original_status": "PASS", "binary_decision": "NOT_TRIGGER", "not_trigger_reason": "assessed_pass"},
            {"original_status": "NOT_APPLICABLE", "binary_decision": "NOT_TRIGGER", "not_trigger_reason": "not_applicable"},
            {"original_status": "INCOMPLETE", "binary_decision": "NOT_TRIGGER", "not_trigger_reason": "insufficient_evidence"},
        ]
    )
    tables = {
        **loaded,
        **derive_binary_tables(loaded["binary_decision_rows"]),
        "binary_mapping_contract": mapping_contract,
    }
    chosen_output = Path(output_dir).expanduser().resolve() if output_dir else default_output_dir()
    paths = write_analysis_outputs(tables, chosen_output)
    return {"project_root": root, "output_dir": chosen_output, "paths": paths, "tables": tables}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=None, help="Directory for derived CSVs and figures.")
    args = parser.parse_args(argv)
    result = run_analysis(output_dir=args.output_dir)
    validations = result["tables"]["binary_mapping_validation"]
    print(
        "Offline binary analysis completed: "
        f"{len(result['tables']['binary_decision_rows']):,} derived rows; "
        f"{int(validations['all_rows_mapped_exactly_once'].sum())} validated 765-row matrices; "
        f"outputs in {result['output_dir']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
