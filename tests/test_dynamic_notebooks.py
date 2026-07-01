from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from benchmarking.dynamic.notebook_support import (
    OPTIONAL_DYNAMIC_TABLES,
    REQUIRED_DYNAMIC_TABLES,
    assert_required_tables,
    load_dynamic_tables,
    safe_read_csv,
)


NOTEBOOK_DIR = Path("benchmarking/dynamic")
STALE_NOTEBOOK_STRINGS = (
    "results_real_reviewed_naive_baseline",
    "expected_results",
    "criteria_expected",
    "dynamic_precision.csv",
    "dynamic_recall.csv",
)


def test_notebook_support_helpers_validate_required_and_optional_tables(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    tables_dir = results_dir / "tables"
    tables_dir.mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="missing required dynamic result tables"):
        assert_required_tables(results_dir)

    for filename in REQUIRED_DYNAMIC_TABLES.values():
        (tables_dir / filename).write_text("", encoding="utf-8")

    paths = assert_required_tables(results_dir)
    assert set(paths) == set(REQUIRED_DYNAMIC_TABLES)

    tables = load_dynamic_tables(results_dir)
    assert set(REQUIRED_DYNAMIC_TABLES).issubset(tables)
    assert set(OPTIONAL_DYNAMIC_TABLES).issubset(tables)
    assert all(frame.empty for frame in tables.values())

    missing_optional = safe_read_csv(tables_dir / "optional_missing.csv", columns=["a", "b"])
    assert list(missing_optional.columns) == ["a", "b"]
    assert missing_optional.empty


def test_dynamic_notebooks_parse_and_reference_current_tables(tmp_path: Path) -> None:
    results_dir = _minimal_results_dir(tmp_path)
    allowed_tables = set(REQUIRED_DYNAMIC_TABLES.values()) | set(OPTIONAL_DYNAMIC_TABLES.values())
    notebook_paths = sorted(NOTEBOOK_DIR.glob("*.ipynb"))
    assert notebook_paths

    for path in notebook_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload.get("nbformat") == 4
        source = "\n".join(
            "".join(cell.get("source", []))
            for cell in payload.get("cells", [])
        )
        for stale in STALE_NOTEBOOK_STRINGS:
            assert stale not in source, f"{path} still references stale notebook source {stale!r}"

        csv_refs = sorted(set(re.findall(r"['\"]([^'\"]+\\.csv)['\"]", source)))
        assert set(csv_refs).issubset(allowed_tables)
        for filename in csv_refs:
            assert (results_dir / "tables" / filename).exists(), f"{path} references missing {filename}"


def test_dynamic_notebooks_execute_when_jupyter_is_available(tmp_path: Path) -> None:
    if shutil.which("jupyter") is None:
        pytest.skip("jupyter is not installed; notebook execution check skipped")

    results_dir = _minimal_results_dir(tmp_path)
    output_dir = tmp_path / "executed"
    output_dir.mkdir()
    env = {**os.environ, "DYNAMIC_RESULTS_DIR": str(results_dir)}

    for path in sorted(NOTEBOOK_DIR.glob("*.ipynb")):
        completed = subprocess.run(
            [
                "jupyter",
                "nbconvert",
                "--to",
                "notebook",
                "--execute",
                str(path),
                "--output-dir",
                str(output_dir),
                "--ExecutePreprocessor.timeout=120",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr


def _minimal_results_dir(tmp_path: Path) -> Path:
    results_dir = tmp_path / "results"
    tables_dir = results_dir / "tables"
    tables_dir.mkdir(parents=True)
    (results_dir / "benchmark_summary.json").write_text(
        json.dumps(
            {
                "analyzer_baseline_id": "rules-dynamic-wcag",
                "task_success": {
                    "task_success_count": 0,
                    "task_success_denominator": 0,
                    "task_success_rate": None,
                },
                "publication_metrics": {},
            }
        ),
        encoding="utf-8",
    )
    for filename in set(REQUIRED_DYNAMIC_TABLES.values()) | set(OPTIONAL_DYNAMIC_TABLES.values()):
        (tables_dir / filename).write_text("", encoding="utf-8")
    return results_dir
