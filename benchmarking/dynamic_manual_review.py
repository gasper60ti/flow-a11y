"""Manual labeling exports for dynamic benchmark human-oracle review."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from dynamic.schemas.wcag_dynamic import DynamicStatus

from benchmarking.dynamic_datasets import DynamicBenchmarkDataset, flatten_expected_results

DYNAMIC_LABEL_STATUSES = (
    DynamicStatus.pass_status.value,
    DynamicStatus.fail.value,
    DynamicStatus.warning.value,
    DynamicStatus.not_applicable.value,
    DynamicStatus.incomplete.value,
)

MANUAL_LABEL_QUEUE_COLUMNS = [
    "dataset_id",
    "scenario_id",
    "criterion_id",
    "scenario_type",
    "target",
    "task_description",
    "run_status",
    "current_oracle_status",
    "label_state",
    "analyzer_status",
    "analyzer_summary",
    "analyzer_missing_evidence",
    "reviewer_status",
    "reviewer_confidence",
    "reviewer",
    "reviewer_evidence",
    "reviewer_notes",
    "runtime_trace_path",
    "formal_report_path",
    "scenario_result_path",
    "screenshot_paths",
    "dom_snapshot_paths",
    "focus_state_paths",
    "keyboard_state_paths",
]

MANUAL_LABEL_TEMPLATE_COLUMNS = [
    "scenario_id",
    "criterion_id",
    "reviewer_status",
    "reviewer_confidence",
    "reviewer",
    "reviewer_evidence",
    "reviewer_notes",
]


def build_dynamic_manual_label_rows(
    dataset: DynamicBenchmarkDataset,
    case_records: list[dict[str, Any]],
    finding_records: list[dict[str, Any]],
    raw_dir: Path,
    oracle_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build criterion-by-scenario rows for human-oracle dynamic labeling."""

    case_by_scenario = {str(row.get("scenario_id", "")): row for row in case_records}
    finding_by_key = {
        (str(row.get("scenario_id", "")), str(row.get("criterion_id", ""))): row for row in finding_records
    }
    scenario_by_id = {scenario.scenario_id: scenario for scenario in dataset.scenarios}
    pending_scenarios = {scenario.scenario_id for scenario in dataset.scenarios if scenario.labels_pending}
    expected_by_key = {
        (str(row["scenario_id"]), str(row["criterion_id"])): row
        for row in (oracle_rows if oracle_rows is not None else flatten_expected_results(dataset))
    }
    keys_by_scenario: dict[str, set[str]] = {scenario.scenario_id: set() for scenario in dataset.scenarios}
    for scenario in dataset.scenarios:
        keys_by_scenario[scenario.scenario_id].update(result.criterion_id for result in scenario.expected_results)
    for scenario_id, criterion_id in expected_by_key:
        if scenario_id in keys_by_scenario and criterion_id:
            keys_by_scenario[scenario_id].add(criterion_id)
    for scenario_id, criterion_id in finding_by_key:
        if scenario_id in keys_by_scenario and criterion_id:
            keys_by_scenario[scenario_id].add(criterion_id)

    rows: list[dict[str, Any]] = []
    for scenario_id in sorted(keys_by_scenario):
        scenario = scenario_by_id[scenario_id]
        for criterion_id in sorted(keys_by_scenario[scenario_id]):
            expected = expected_by_key.get((scenario_id, criterion_id), {})
            case = case_by_scenario.get(scenario_id, {})
            finding = finding_by_key.get((scenario_id, criterion_id), {})
            scenario_raw_dir = raw_dir / str(case.get("run_id") or "run_1") / scenario_id
            current_status = str(expected.get("expected_status", ""))
            if current_status:
                label_state = "pending" if scenario_id in pending_scenarios else "labelled"
            else:
                label_state = "unlabelled"

            rows.append(
                {
                    "dataset_id": expected.get("dataset_id", dataset.dataset_id),
                    "scenario_id": scenario_id,
                    "criterion_id": criterion_id,
                    "scenario_type": scenario.scenario_type,
                    "target": scenario.target,
                    "task_description": scenario.task_description,
                    "run_status": case.get("run_status", ""),
                    "current_oracle_status": current_status,
                    "label_state": label_state,
                    "analyzer_status": finding.get("status", "missing"),
                    "analyzer_summary": finding.get("summary", ""),
                    "analyzer_missing_evidence": finding.get("missing_evidence", ""),
                    "reviewer_status": "",
                    "reviewer_confidence": "",
                    "reviewer": "",
                    "reviewer_evidence": "",
                    "reviewer_notes": "",
                    "runtime_trace_path": case.get("runtime_trace_path", ""),
                    "formal_report_path": case.get("formal_report_path", ""),
                    "scenario_result_path": str(scenario_raw_dir / "scenario_result.json")
                    if scenario_raw_dir.exists()
                    else "",
                    "screenshot_paths": _artifact_list(scenario_raw_dir, "screenshot_step_*.png"),
                    "dom_snapshot_paths": _artifact_list(scenario_raw_dir, "dom_snapshot_step_*.html"),
                    "focus_state_paths": _artifact_list(scenario_raw_dir, "focus_state_step_*.json"),
                    "keyboard_state_paths": _artifact_list(scenario_raw_dir, "keyboard_state_step_*.json"),
                }
            )

    return rows


def write_dynamic_manual_label_queue(rows: list[dict[str, Any]], path: Path) -> Path:
    """Write the full dynamic human-oracle review queue."""

    _write_csv(rows, path, MANUAL_LABEL_QUEUE_COLUMNS)
    return path


def write_dynamic_manual_label_template(rows: list[dict[str, Any]], path: Path) -> Path:
    """Write the compact reviewer-fillable dynamic label template.

    The template includes all rows, not only pending labels, so existing
    oracle labels can be reviewed and corrected without changing the dataset
    before review.
    """

    template_rows = [
        {
            "scenario_id": row.get("scenario_id", ""),
            "criterion_id": row.get("criterion_id", ""),
            "reviewer_status": "",
            "reviewer_confidence": "",
            "reviewer": "",
            "reviewer_evidence": "",
            "reviewer_notes": "",
        }
        for row in rows
    ]
    _write_csv(template_rows, path, MANUAL_LABEL_TEMPLATE_COLUMNS)
    return path


def write_dynamic_oracle_json_template(
    dataset: DynamicBenchmarkDataset,
    rows: list[dict[str, Any]],
    path: Path,
) -> Path:
    """Write a JSON scaffold that mirrors real_scenarios.json for reviewer labels."""

    row_by_key = {(str(row["scenario_id"]), str(row["criterion_id"])): row for row in rows}
    payload = {
        "dataset_id": dataset.dataset_id,
        "name": dataset.name,
        "description": (
            "Reviewer-fillable oracle template. Convert adjudicated statuses and evidence into "
            "benchmarking/fixtures/dynamic/real_oracle_labels.csv after review."
        ),
        "allowed_statuses": list(DYNAMIC_LABEL_STATUSES),
        "scenarios": [],
    }
    for scenario in dataset.scenarios:
        scenario_payload = {
            "scenario_id": scenario.scenario_id,
            "labels_pending": scenario.labels_pending,
            "task_description": scenario.task_description,
            "criteria": [],
        }
        scenario_criteria = sorted(
            str(row["criterion_id"])
            for row in rows
            if str(row.get("scenario_id", "")) == scenario.scenario_id and row.get("criterion_id")
        )
        for criterion_id in scenario_criteria:
            row = row_by_key.get((scenario.scenario_id, criterion_id), {})
            scenario_payload["criteria"].append(
                {
                    "criterion_id": criterion_id,
                    "current_oracle_status": row.get("current_oracle_status", ""),
                    "analyzer_status": row.get("analyzer_status", ""),
                    "reviewer_status": "",
                    "reviewer_confidence": "",
                    "reviewer_evidence": [],
                    "reviewer_notes": "",
                    "runtime_trace_path": row.get("runtime_trace_path", ""),
                    "formal_report_path": row.get("formal_report_path", ""),
                    "screenshot_paths": _split_artifacts(str(row.get("screenshot_paths", ""))),
                }
            )
        payload["scenarios"].append(scenario_payload)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _write_csv(rows: list[dict[str, Any]], path: Path, columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _artifact_list(directory: Path, pattern: str) -> str:
    if not directory.exists():
        return ""
    return ";".join(str(path) for path in sorted(directory.glob(pattern)))


def _split_artifacts(value: str) -> list[str]:
    return [item for item in value.split(";") if item]
