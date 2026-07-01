"""Convert completed dynamic manual labels into external oracle data."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from benchmarking.dynamic_datasets import REAL_ORACLE_LABELS_PATH, REAL_SCENARIOS_PATH
from benchmarking.dynamic_manual_review import DYNAMIC_LABEL_STATUSES

ORACLE_COLUMNS = [
    "scenario_id",
    "criterion_id",
    "expected_status",
    "evidence",
    "reviewer",
    "confidence",
    "notes",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True, help="Completed manual_label_template.csv path.")
    parser.add_argument("--dataset", type=Path, default=REAL_SCENARIOS_PATH, help="Scenario dataset JSON to update.")
    parser.add_argument(
        "--output",
        type=Path,
        default=REAL_ORACLE_LABELS_PATH,
        help="Output oracle CSV path. Defaults to benchmarking/fixtures/dynamic/real_oracle_labels.csv.",
    )
    parser.add_argument("--in-place", action="store_true", help="Overwrite --dataset with applied labels.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.in_place:
        output = apply_manual_labels(labels_path=args.labels, dataset_path=args.dataset, output_path=args.dataset)
        print(f"Applied manual labels to {output}")
        return 0

    output = write_external_oracle_from_labels(labels_path=args.labels, output_path=args.output)
    print(f"Wrote external dynamic oracle labels to {output}")
    return 0


def write_external_oracle_from_labels(
    *,
    labels_path: Path,
    output_path: Path = REAL_ORACLE_LABELS_PATH,
) -> Path:
    """Write a complete external oracle CSV from reviewer-completed labels."""

    labels = _read_labels(labels_path)
    rows = [
        {
            "scenario_id": scenario_id,
            "criterion_id": criterion_id,
            "expected_status": label["reviewer_status"],
            "evidence": label["reviewer_evidence"] or "reviewed but no evidence note supplied",
            "reviewer": label["reviewer"],
            "confidence": label["reviewer_confidence"],
            "notes": label["reviewer_notes"],
        }
        for (scenario_id, criterion_id), label in sorted(labels.items())
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ORACLE_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return output_path


def apply_manual_labels(
    *,
    labels_path: Path,
    dataset_path: Path = REAL_SCENARIOS_PATH,
    output_path: Path | None = None,
) -> Path:
    """Apply non-empty reviewer statuses from CSV into the dynamic scenario dataset."""

    labels = _read_labels(labels_path)
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    applied_by_scenario: dict[str, set[str]] = {}

    for scenario in payload.get("scenarios", []):
        scenario_id = str(scenario.get("scenario_id", ""))
        expected_results = scenario.get("expected_results", [])
        for result in expected_results:
            criterion_id = str(result.get("criterion_id", ""))
            label = labels.get((scenario_id, criterion_id))
            if not label:
                continue
            result["expected_status"] = label["reviewer_status"]
            result["expected_evidence"] = _evidence_items(label.get("reviewer_evidence", ""))
            if label.get("reviewer_notes"):
                result["notes"] = label["reviewer_notes"]
            applied_by_scenario.setdefault(scenario_id, set()).add(criterion_id)

        expected_criteria = {str(item.get("criterion_id", "")) for item in expected_results}
        if expected_criteria and expected_criteria.issubset(applied_by_scenario.get(scenario_id, set())):
            scenario["labels_pending"] = False

    output = output_path or dataset_path.with_name(f"{dataset_path.stem}.labelled{dataset_path.suffix}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output


def _read_labels(labels_path: Path) -> dict[tuple[str, str], dict[str, str]]:
    labels: dict[tuple[str, str], dict[str, str]] = {}
    allowed = set(DYNAMIC_LABEL_STATUSES)
    with labels_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for line_number, row in enumerate(reader, start=2):
            scenario_id = str(row.get("scenario_id", "")).strip()
            criterion_id = str(row.get("criterion_id", "")).strip()
            status = str(row.get("reviewer_status", "")).strip()
            if not scenario_id or not criterion_id or not status:
                continue
            if status not in allowed:
                raise ValueError(
                    f"{labels_path}:{line_number}: invalid reviewer_status {status!r}; "
                    f"expected one of {', '.join(sorted(allowed))}"
                )
            labels[(scenario_id, criterion_id)] = {
                "reviewer_status": status,
                "reviewer_confidence": str(row.get("reviewer_confidence", "")).strip(),
                "reviewer": str(row.get("reviewer", "")).strip(),
                "reviewer_evidence": str(row.get("reviewer_evidence", "")).strip(),
                "reviewer_notes": str(row.get("reviewer_notes", "")).strip(),
            }
    return labels


def _evidence_items(value: str) -> list[str]:
    items = [item.strip() for item in value.split(";") if item.strip()]
    return items or ["reviewed but no evidence note supplied"]


if __name__ == "__main__":
    raise SystemExit(main())
