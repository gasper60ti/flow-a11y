"""Helpers shared by dynamic benchmark notebooks and tests."""

from __future__ import annotations

import os
import json
from pathlib import Path

import pandas as pd

REQUIRED_DYNAMIC_TABLES = {
    "case_records": "dynamic_case_records.csv",
    "finding_status_records": "dynamic_finding_status_records.csv",
    "quality_summary": "dynamic_quality_summary.csv",
    "evidence_quality": "dynamic_evidence_quality.csv",
    "quality_by_status": "dynamic_quality_by_status.csv",
    "status_confusion": "status_confusion.csv",
    "baseline_comparison": "baseline_comparison.csv",
    "baseline_status": "baseline_status.csv",
    "criteria_support": "criteria_support_matrix.csv",
    "false_positive_audit": "dynamic_false_positive_audit.csv",
    "false_negative_audit": "dynamic_false_negative_audit.csv",
    "scoring_audit": "dynamic_scoring_audit.csv",
}

OPTIONAL_DYNAMIC_TABLES = {
    "attempted_recall": "attempted_dynamic_recall.csv",
    "attempted_precision": "attempted_dynamic_precision.csv",
    "attempted_quality_summary": "attempted_dynamic_quality_summary.csv",
    "attempted_status_confusion": "attempted_status_confusion.csv",
    "attempted_baseline_comparison": "attempted_baseline_comparison.csv",
    "attempted_baseline_pairwise_comparison": "attempted_baseline_pairwise_comparison.csv",
    "error_analysis": "dynamic_error_analysis.csv",
    "invalid_evidence_refs": "invalid_evidence_refs.csv",
    "recall": "dynamic_recall.csv",
    "precision": "dynamic_precision.csv",
    "coverage": "dynamic_coverage.csv",
    "variability": "dynamic_variability.csv",
    "ablation": "dynamic_ablation.csv",
    "performance_cost": "dynamic_performance_cost.csv",
    "baseline_finding_status_records": "baseline_finding_status_records.csv",
    "baseline_pairwise_comparison": "baseline_pairwise_comparison.csv",
    "model_calls": "model_calls.csv",
    "model_call_verification": "model_call_verification.csv",
    "unsupported_incomplete": "unsupported_incomplete_counts.csv",
}


def resolve_dynamic_results_dir(
    notebook_dir: str | Path | None = None,
    requested: str | Path | None = None,
) -> Path:
    """Resolve the dynamic results directory for notebooks.

    Resolution order:
    1. explicit ``requested`` path;
    2. ``DYNAMIC_RESULTS_DIR`` environment variable;
    3. newest ``results*`` directory with a ``tables`` subdirectory;
    4. ``<notebook_dir>/results``.

    The reviewer-facing binary analysis uses
    ``publication_binary_analysis.py`` rather than this live-run helper.
    """

    base_dir = Path(notebook_dir or Path.cwd())
    if base_dir.name != "dynamic" and (base_dir / "a11y-agent" / "benchmarking" / "dynamic").exists():
        base_dir = base_dir / "a11y-agent" / "benchmarking" / "dynamic"
    elif base_dir.name != "dynamic" and (base_dir / "benchmarking" / "dynamic").exists():
        base_dir = base_dir / "benchmarking" / "dynamic"

    configured = requested or os.environ.get("DYNAMIC_RESULTS_DIR")
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_absolute():
            configured_path = base_dir / configured_path
        return configured_path

    candidates = [
        path
        for path in base_dir.glob("results*")
        if path.is_dir() and (path / "tables").is_dir()
    ]
    if candidates:
        return max(candidates, key=_results_sort_key)
    return base_dir / "results"


def safe_read_csv(path: str | Path, columns: list[str] | None = None) -> pd.DataFrame:
    """Read a CSV table, returning an empty frame for missing or empty files."""

    csv_path = Path(path)
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return pd.DataFrame(columns=columns or [])
    try:
        return pd.read_csv(csv_path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=columns or [])


def assert_required_tables(
    results_dir: str | Path,
    required_tables: dict[str, str] | None = None,
) -> dict[str, Path]:
    """Return required table paths, raising if any required CSV is missing."""

    results_path = Path(results_dir)
    tables_dir = results_path / "tables"
    required = required_tables or REQUIRED_DYNAMIC_TABLES
    missing = [
        filename for filename in required.values() if not (tables_dir / filename).exists()
    ]
    if missing:
        raise FileNotFoundError(
            f"{results_path}: missing required dynamic result tables: {', '.join(sorted(missing))}"
        )
    return {name: tables_dir / filename for name, filename in required.items()}


def load_dynamic_tables(
    results_dir: str | Path,
    *,
    required_tables: dict[str, str] | None = None,
    optional_tables: dict[str, str] | None = None,
) -> dict[str, pd.DataFrame]:
    """Load required and optional dynamic result tables for notebooks."""

    results_path = Path(results_dir)
    tables_dir = results_path / "tables"
    required = required_tables or REQUIRED_DYNAMIC_TABLES
    optional = optional_tables or OPTIONAL_DYNAMIC_TABLES
    assert_required_tables(results_path, required)
    tables = {
        name: safe_read_csv(tables_dir / filename)
        for name, filename in required.items()
    }
    tables.update(
        {
            name: safe_read_csv(tables_dir / filename)
            for name, filename in optional.items()
        }
    )
    return tables


def read_benchmark_summary(results_dir: str | Path) -> dict:
    """Read benchmark_summary.json if present; return an empty dict otherwise."""

    summary_path = Path(results_dir) / "benchmark_summary.json"
    if not summary_path.exists():
        return {}
    return json.loads(summary_path.read_text(encoding="utf-8"))


def _results_sort_key(path: Path) -> tuple[float, str]:
    summary = path / "benchmark_summary.json"
    if summary.exists():
        return (summary.stat().st_mtime, path.name)
    return (path.stat().st_mtime, path.name)
