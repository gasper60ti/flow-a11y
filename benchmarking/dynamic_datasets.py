"""Dynamic benchmark scenario metadata loaders."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dynamic.preprocessors.wcag_mapper import DYNAMIC_WCAG_REGISTRY
from dynamic.schemas.wcag_dynamic import DynamicStatus

from benchmarking.dynamic_baselines import DynamicBaselineKind

D_dyn_fixture = "D_dyn_fixture"
D_dyn_real = "D_dyn_real"

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "dynamic"
SYNTHETIC_FIXTURES_PATH = FIXTURES_DIR / "synthetic_scenarios.json"
REAL_SCENARIOS_PATH = FIXTURES_DIR / "real_scenarios.json"
REAL_ORACLE_LABELS_PATH = FIXTURES_DIR / "real_oracle_labels.csv"


@dataclass(frozen=True)
class DynamicExpectedResult:
    """Expected criterion-level result for a dynamic scenario."""

    criterion_id: str
    expected_status: DynamicStatus
    expected_evidence: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True)
class DynamicOracleLabel:
    """External criterion-level oracle label used only for scoring."""

    scenario_id: str
    criterion_id: str
    expected_status: DynamicStatus
    evidence: tuple[str, ...]
    reviewer: str = ""
    confidence: str = ""
    notes: str = ""


@dataclass(frozen=True)
class DynamicBenchmarkScenario:
    """Metadata for one dynamic benchmark scenario."""

    scenario_id: str
    target: str
    interaction_type: str
    task_description: str
    scripted_steps: tuple[str, ...]
    baseline_kind: DynamicBaselineKind
    expected_results: tuple[DynamicExpectedResult, ...] = ()
    criteria_expected: tuple[str, ...] = ()
    scenario_type: str = ""
    labels_pending: bool = False
    max_steps: int = 8


@dataclass(frozen=True)
class DynamicBenchmarkDataset:
    """A named set of dynamic benchmark scenarios."""

    dataset_id: str
    name: str
    baseline_kind: DynamicBaselineKind
    scenarios: tuple[DynamicBenchmarkScenario, ...]
    description: str = ""

    @property
    def criteria_expected(self) -> tuple[str, ...]:
        criteria = {criterion for scenario in self.scenarios for criterion in scenario.criteria_expected}
        return tuple(sorted(criteria))


def load_synthetic_dynamic_fixture_metadata(
    path: Path = SYNTHETIC_FIXTURES_PATH,
) -> DynamicBenchmarkDataset:
    """Load deterministic synthetic dynamic fixture metadata."""

    return _load_dataset(path)


def load_real_dynamic_scenario_metadata(
    path: Path = REAL_SCENARIOS_PATH,
) -> DynamicBenchmarkDataset:
    """Load real-world scenario metadata without oracle labels."""

    return _load_dataset(path)


def default_real_oracle_path(scenario_path: Path | None = None) -> Path:
    """Return the default oracle path next to the real scenario dataset."""

    if scenario_path is not None:
        return scenario_path.with_name("real_oracle_labels.csv")
    return REAL_ORACLE_LABELS_PATH


def load_real_oracle_labels(path: Path = REAL_ORACLE_LABELS_PATH) -> tuple[DynamicOracleLabel, ...]:
    """Load external real-scenario oracle labels used only for metrics."""

    source_path = Path(path)
    if not source_path.exists():
        return ()
    with source_path.open("r", encoding="utf-8", newline="") as handle:
        return tuple(
            _oracle_label_from_row(row, line_number, source_path)
            for line_number, row in enumerate(csv.DictReader(handle), start=2)
        )


def load_dynamic_dataset(dataset_id: str) -> DynamicBenchmarkDataset:
    """Load a known dynamic dataset by id."""

    if dataset_id == D_dyn_fixture:
        return load_synthetic_dynamic_fixture_metadata()
    if dataset_id == D_dyn_real:
        return load_real_dynamic_scenario_metadata()
    raise KeyError(f"Unknown dynamic dataset: {dataset_id}")


def load_dynamic_dataset_file(path: Path) -> DynamicBenchmarkDataset:
    """Load a dynamic benchmark dataset from an explicit JSON path."""

    return _load_dataset(path)


def validate_expected_results(dataset: DynamicBenchmarkDataset) -> list[str]:
    """Validate optional inline expectations and criterion ids."""

    errors: list[str] = []
    try:
        DynamicBaselineKind(dataset.baseline_kind)
    except ValueError:
        errors.append(f"{dataset.dataset_id}: unknown baseline kind {dataset.baseline_kind}")

    require_inline_expectations = dataset.baseline_kind != DynamicBaselineKind.HUMAN_ORACLE
    for scenario in dataset.scenarios:
        if require_inline_expectations and not scenario.expected_results:
            errors.append(f"{scenario.scenario_id}: expected_results must not be empty")
        if require_inline_expectations and not scenario.criteria_expected:
            errors.append(f"{scenario.scenario_id}: criteria_expected must not be empty")

        criteria_from_results = {result.criterion_id for result in scenario.expected_results}
        criteria_expected = set(scenario.criteria_expected)
        missing_from_criteria = sorted(criteria_from_results - criteria_expected)
        if missing_from_criteria:
            errors.append(
                f"{scenario.scenario_id}: criteria_expected missing {','.join(missing_from_criteria)}"
            )

        for criterion_id in sorted(criteria_expected):
            if not DYNAMIC_WCAG_REGISTRY.is_supported(criterion_id):
                errors.append(f"{scenario.scenario_id}: unsupported criterion {criterion_id}")

        for result in scenario.expected_results:
            if not DYNAMIC_WCAG_REGISTRY.is_supported(result.criterion_id):
                errors.append(f"{scenario.scenario_id}: unsupported expected criterion {result.criterion_id}")
            try:
                DynamicStatus(result.expected_status)
            except ValueError:
                errors.append(
                    f"{scenario.scenario_id}: invalid expected status "
                    f"{result.criterion_id}={result.expected_status}"
                )
            if not result.expected_evidence:
                errors.append(f"{scenario.scenario_id}: {result.criterion_id} missing expected_evidence")

    return errors


def flatten_expected_results(dataset: DynamicBenchmarkDataset) -> list[dict[str, str]]:
    """Flatten scenario expectations into simple metric rows."""

    rows: list[dict[str, str]] = []
    for scenario in dataset.scenarios:
        for expected in scenario.expected_results:
            rows.append(
                {
                    "dataset_id": dataset.dataset_id,
                    "scenario_id": scenario.scenario_id,
                    "criterion_id": expected.criterion_id,
                    "expected_status": expected.expected_status.value,
                    "baseline_kind": scenario.baseline_kind.value,
                }
            )
    return rows


def flatten_oracle_labels(
    dataset: DynamicBenchmarkDataset,
    oracle_labels: tuple[DynamicOracleLabel, ...],
) -> list[dict[str, str]]:
    """Flatten external oracle labels into metric rows."""

    scenario_by_id = {scenario.scenario_id: scenario for scenario in dataset.scenarios}
    rows: list[dict[str, str]] = []
    for label in oracle_labels:
        scenario = scenario_by_id.get(label.scenario_id)
        if scenario is None:
            continue
        rows.append(
            {
                "dataset_id": dataset.dataset_id,
                "scenario_id": label.scenario_id,
                "criterion_id": label.criterion_id,
                "expected_status": label.expected_status.value,
                "expected_evidence": ";".join(label.evidence),
                "reviewer": label.reviewer,
                "confidence": label.confidence,
                "notes": label.notes,
                "baseline_kind": scenario.baseline_kind.value,
            }
        )
    return rows


def _load_dataset(path: Path) -> DynamicBenchmarkDataset:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _dataset_from_payload(payload)


def _dataset_from_payload(payload: dict[str, Any]) -> DynamicBenchmarkDataset:
    scenarios = tuple(_scenario_from_payload(item) for item in payload.get("scenarios", []))
    return DynamicBenchmarkDataset(
        dataset_id=str(payload["dataset_id"]),
        name=str(payload.get("name", payload["dataset_id"])),
        baseline_kind=DynamicBaselineKind(str(payload["baseline_kind"])),
        scenarios=scenarios,
        description=str(payload.get("description", "")),
    )


def _scenario_from_payload(payload: dict[str, Any]) -> DynamicBenchmarkScenario:
    expected_results = tuple(_expected_from_payload(item) for item in payload.get("expected_results", []))
    criteria_expected = tuple(str(item) for item in payload.get("criteria_expected", []))
    return DynamicBenchmarkScenario(
        scenario_id=str(payload["scenario_id"]),
        target=str(payload["target"]),
        interaction_type=str(payload["interaction_type"]),
        task_description=str(payload["task_description"]),
        scripted_steps=tuple(str(step) for step in payload.get("scripted_steps", [])),
        expected_results=expected_results,
        baseline_kind=DynamicBaselineKind(str(payload["baseline_kind"])),
        criteria_expected=criteria_expected,
        scenario_type=str(payload.get("scenario_type", "")),
        labels_pending=bool(payload.get("labels_pending", False)),
        max_steps=int(payload.get("max_steps", 8)),
    )


def _expected_from_payload(payload: dict[str, Any]) -> DynamicExpectedResult:
    return DynamicExpectedResult(
        criterion_id=str(payload["criterion_id"]),
        expected_status=DynamicStatus(str(payload["expected_status"])),
        expected_evidence=tuple(str(item) for item in payload.get("expected_evidence", [])),
        notes=str(payload.get("notes", "")),
    )


def _oracle_label_from_row(row: dict[str, str], line_number: int, source_path: Path) -> DynamicOracleLabel:
    scenario_id = str(row.get("scenario_id", "")).strip()
    criterion_id = str(row.get("criterion_id", "")).strip()
    if not scenario_id or not criterion_id:
        raise ValueError(f"{source_path}:{line_number}: scenario_id and criterion_id are required")
    try:
        expected_status = DynamicStatus(str(row.get("expected_status", "")).strip())
    except ValueError as exc:
        raise ValueError(
            f"{source_path}:{line_number}: invalid expected_status {row.get('expected_status', '')!r}"
        ) from exc
    return DynamicOracleLabel(
        scenario_id=scenario_id,
        criterion_id=criterion_id,
        expected_status=expected_status,
        evidence=_evidence_items(str(row.get("evidence", "")).strip()),
        reviewer=str(row.get("reviewer", "")).strip(),
        confidence=str(row.get("confidence", "")).strip(),
        notes=str(row.get("notes", "")).strip(),
    )


def _evidence_items(value: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in value.split(";") if item.strip())
    return items or ("reviewed but no evidence note supplied",)
