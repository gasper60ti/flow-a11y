from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from dynamic.preprocessors.wcag_mapper import DYNAMIC_WCAG_REGISTRY


ARTIFACT_ROOT = Path("benchmarking/dynamic/publication_results/v5")
DERIVED_ROOT = ARTIFACT_ROOT / "derived"
SOURCE_ROOT = ARTIFACT_ROOT / "source_inputs"

STATUS_TO_DECISION = {
    "FAIL": "TRIGGER",
    "WARNING": "NOT_TRIGGER",
    "PASS": "NOT_TRIGGER",
    "NOT_APPLICABLE": "NOT_TRIGGER",
    "INCOMPLETE": "NOT_TRIGGER",
}
NOT_TRIGGER_REASONS = {
    "WARNING": "weak_signal",
    "PASS": "assessed_pass",
    "NOT_APPLICABLE": "not_applicable",
    "INCOMPLETE": "insufficient_evidence",
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_publication_inventory_matches_source_registry_and_fixture() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    scenario_payload = json.loads(
        Path("benchmarking/fixtures/dynamic/real_scenarios.json").read_text(encoding="utf-8")
    )

    assert len(DYNAMIC_WCAG_REGISTRY.supported_ids()) == 45
    assert len(DYNAMIC_WCAG_REGISTRY.supported_probes()) == 9
    assert len(scenario_payload["scenarios"]) == 19
    assert "publication_results/v5" in readme
    assert (ARTIFACT_ROOT / "README.md").is_file()
    assert (ARTIFACT_ROOT / "ARTIFACT_MANIFEST.md").is_file()


def test_publication_oracle_is_complete_and_anonymous() -> None:
    rows = _read_csv(Path("benchmarking/fixtures/dynamic/real_oracle_labels.csv"))

    assert len(rows) == 855
    assert len({row["scenario_id"] for row in rows}) == 19
    assert {row["reviewer"] for row in rows} == {"anonymous_author_review"}
    assert all(row["expected_status"] for row in rows)


def test_v5_source_manifest_and_checksums_are_complete() -> None:
    manifest = _read_csv(DERIVED_ROOT / "binary_source_manifest.csv")

    assert len(manifest) == 42
    input_paths = {row["artifact_path"] for row in manifest}
    assert len(input_paths) == 32
    for row in manifest:
        path = Path(row["artifact_path"])
        assert not path.is_absolute()
        assert ".." not in path.parts
        resolved = Path(row["artifact_path"])
        assert resolved.is_file()
        assert hashlib.sha256(resolved.read_bytes()).hexdigest() == row["sha256"]

    checksum_lines = (ARTIFACT_ROOT / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    assert checksum_lines
    for line in checksum_lines:
        expected, relative_path = line.split("  ", 1)
        path = ARTIFACT_ROOT / relative_path
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


def test_derivation_manifest_links_every_release_output_to_saved_inputs() -> None:
    sources = _read_csv(DERIVED_ROOT / "binary_source_manifest.csv")
    source_hashes = {row["artifact_path"]: row["sha256"] for row in sources}
    derivation = _read_csv(DERIVED_ROOT / "binary_derivation_manifest.csv")

    output_files = {
        path.relative_to(DERIVED_ROOT).as_posix()
        for path in DERIVED_ROOT.rglob("*")
        if path.is_file()
    }
    by_output: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in derivation:
        by_output[row["derived_artifact"]].append(row)

    assert set(by_output) == output_files
    assert len(source_hashes) == 32
    for artifact, rows in by_output.items():
        if artifact == "binary_mapping_contract.csv":
            assert len(rows) == 1
            assert rows[0]["derivation_kind"] == "static binary mapping contract"
            assert rows[0]["source_input_count"] == "0"
            assert rows[0]["source_artifact"] == ""
            continue
        assert {row["source_artifact"] for row in rows} == set(source_hashes)
        assert {row["source_sha256"] for row in rows} == {
            source_hashes[row["source_artifact"]] for row in rows
        }
        assert {row["source_input_count"] for row in rows} == {"32"}
        assert {row["derivation_kind"] for row in rows} == {
            "offline analysis transitive dependency"
        }


def test_binary_rows_map_every_status_once_and_preserve_reasons() -> None:
    rows = _read_csv(DERIVED_ROOT / "binary_decision_rows.csv")
    by_matrix = Counter(
        (row["cohort_id"], row["run"], row["method_id"]) for row in rows
    )

    assert len(rows) == 15_300
    assert len(by_matrix) == 20
    assert set(by_matrix.values()) == {765}

    for row in rows:
        original = row["tool_status_original"]
        assert row["tool_binary_decision"] == STATUS_TO_DECISION[original]
        expected_reason = NOT_TRIGGER_REASONS.get(original, "")
        assert row["not_trigger_reason"] == expected_reason
        assert row["oracle_binary_decision"] == STATUS_TO_DECISION[row["oracle_status_original"]]
        assert not Path(row["source_status_table"]).is_absolute()


def test_binary_confusion_matrices_and_metrics_use_the_fixed_denominator() -> None:
    matrices = _read_csv(DERIVED_ROOT / "binary_confusion_matrix.csv")
    metrics = _read_csv(DERIVED_ROOT / "binary_metrics_by_run.csv")
    validation = _read_csv(DERIVED_ROOT / "binary_mapping_validation.csv")

    matrix_counts: dict[tuple[str, str, str], int] = defaultdict(int)
    matrix_cells: dict[tuple[str, str, str], set[tuple[str, str]]] = defaultdict(set)
    for row in matrices:
        key = (row["cohort_id"], row["run"], row["method_id"])
        matrix_counts[key] += int(row["count"])
        matrix_cells[key].add((row["oracle_binary_decision"], row["tool_binary_decision"]))
    assert len(matrix_counts) == 20
    assert all(count == 765 for count in matrix_counts.values())
    assert all(len(cells) == 4 for cells in matrix_cells.values())

    assert len(metrics) == 20
    required = {"TP", "FP", "TN", "FN", "precision", "recall", "f1", "mcc", "specificity", "balanced_accuracy", "accuracy"}
    assert required.issubset(metrics[0])
    assert all(row["denominator"] == "765" for row in metrics)
    assert all(sum(int(row[key]) for key in ("TP", "FP", "TN", "FN")) == 765 for row in metrics)

    assert len(validation) == 20
    assert all(row["all_rows_mapped_exactly_once"] == "1" for row in validation)
    assert all(row["required_denominator"] == "765" for row in validation)


def test_notebook_is_executed_and_contains_no_local_identity_or_secret() -> None:
    path = Path("benchmarking/dynamic/publication_analysis.ipynb")
    payload = json.loads(path.read_text(encoding="utf-8"))
    code_cells = [cell for cell in payload["cells"] if cell["cell_type"] == "code"]

    assert len(code_cells) == 5
    assert all(cell.get("execution_count") is not None for cell in code_cells)
    assert not any(
        output.get("output_type") == "error"
        for cell in code_cells
        for output in cell.get("outputs", [])
    )

    serialized = json.dumps(payload).casefold()
    forbidden_fragments = (
        "/" + "users/",
        "/" + "home/",
        "gemini_api" + "_key=",
        "AIza",
        "ghp_",
        "sk-",
    )
    for forbidden in forbidden_fragments:
        assert forbidden not in serialized
