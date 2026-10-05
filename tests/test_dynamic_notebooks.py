from __future__ import annotations

from pathlib import Path

import pytest

from benchmarking.dynamic.notebook_support import (
    OPTIONAL_DYNAMIC_TABLES,
    REQUIRED_DYNAMIC_TABLES,
    assert_required_tables,
    load_dynamic_tables,
    safe_read_csv,
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
