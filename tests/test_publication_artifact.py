from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from dynamic.preprocessors.wcag_mapper import DYNAMIC_WCAG_REGISTRY


ARTIFACT_ROOT = Path("benchmarking/dynamic/publication_results/v4")


def test_publication_inventory_matches_source_registry_and_fixture() -> None:
    readme = Path("../README.md").read_text(encoding="utf-8")
    scenario_payload = json.loads(
        Path("benchmarking/fixtures/dynamic/real_scenarios.json").read_text(encoding="utf-8")
    )
    criterion_ids = DYNAMIC_WCAG_REGISTRY.supported_ids()
    probe_names = DYNAMIC_WCAG_REGISTRY.supported_probes()

    assert len(criterion_ids) == 45
    assert len(probe_names) == 9
    assert len(scenario_payload["scenarios"]) == 19
    assert all(f"| {criterion_id} |" in readme for criterion_id in criterion_ids)
    assert all(
        f"`{scenario['scenario_id']}`" in readme
        for scenario in scenario_payload["scenarios"]
    )


def test_publication_oracle_is_complete_and_anonymous() -> None:
    path = Path("benchmarking/fixtures/dynamic/real_oracle_labels.csv")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 855
    assert len({row["scenario_id"] for row in rows}) == 19
    assert {row["reviewer"] for row in rows} == {"anonymous_author_review"}
    assert all(row["expected_status"] for row in rows)


def test_publication_tables_and_checksums_are_complete() -> None:
    summary = json.loads((ARTIFACT_ROOT / "benchmark_summary.json").read_text(encoding="utf-8"))
    assert summary["attempted_rows"] == 855
    assert summary["scored_rows"] == 765
    assert summary["artifact_scope"] == "normalized_tables_only"

    checksum_lines = (ARTIFACT_ROOT / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    assert len(checksum_lines) == 32
    for line in checksum_lines:
        expected, relative_path = line.split("  ", 1)
        path = ARTIFACT_ROOT / relative_path
        assert path.exists()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


def test_publication_notebook_is_executed_without_errors_or_local_paths() -> None:
    path = Path("benchmarking/dynamic/publication_analysis.ipynb")
    payload = json.loads(path.read_text(encoding="utf-8"))
    code_cells = [cell for cell in payload["cells"] if cell["cell_type"] == "code"]

    assert code_cells
    assert all(cell.get("execution_count") is not None for cell in code_cells)
    assert not any(
        output.get("output_type") == "error"
        for cell in code_cells
        for output in cell.get("outputs", [])
    )

    serialized = json.dumps(payload)
    assert "/Users/" not in serialized
    secret_assignment = "GEMINI_API" + "_KEY="
    assert secret_assignment not in serialized
