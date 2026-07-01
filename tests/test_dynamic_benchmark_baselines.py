from __future__ import annotations

from datetime import datetime, timezone

from benchmarking.dynamic.naive_browser_use_baseline import (
    build_naive_browser_use_task,
    normalize_naive_output_to_report,
)
from benchmarking.dynamic_baselines import (
    BaselineAvailabilityStatus,
    DynamicBaselineKind,
    DynamicBaselineSpec,
    NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE,
    baseline_availability,
    get_baseline_specs,
)
from benchmarking.dynamic_datasets import (
    D_dyn_fixture,
    D_dyn_real,
    DynamicBenchmarkDataset,
    DynamicBenchmarkScenario,
    DynamicExpectedResult,
    flatten_expected_results,
    flatten_oracle_labels,
    load_dynamic_dataset,
    load_real_oracle_labels,
    load_real_dynamic_scenario_metadata,
    load_synthetic_dynamic_fixture_metadata,
    validate_expected_results,
)
from benchmarking.dynamic_metrics import (
    build_baseline_comparison_rows,
    build_dynamic_quality_summary_rows,
    build_false_audit_rows,
    compute_publication_metrics,
    compute_criterion_precision,
    compute_criterion_recall,
    compute_status_confusion_counts,
    compute_unsupported_incomplete_counts,
)
from dynamic.schemas.runtime_state import RuntimeState
from dynamic.schemas.runtime_trace import RuntimeTrace, RuntimeTraceMetadata
from dynamic.schemas.wcag_dynamic import DynamicStatus


def test_synthetic_dynamic_fixture_metadata_loads() -> None:
    dataset = load_synthetic_dynamic_fixture_metadata()

    assert dataset.dataset_id == D_dyn_fixture
    assert dataset.baseline_kind == DynamicBaselineKind.SYNTHETIC_FIXTURE
    assert len(dataset.scenarios) >= 8
    assert validate_expected_results(dataset) == []

    scenario_types = {scenario.scenario_type for scenario in dataset.scenarios}
    assert {
        "keyboard trap",
        "missing focus indicator",
        "incorrect focus order",
        "modal focus escaping",
        "hover-only content",
        "status message without live region",
        "timeout without warning",
        "form error not announced",
        "flashing content",
        "pointer cancellation",
        "drag-only control",
        "media captions missing",
        "keyboard navigation pass",
        "focus indicator pass",
        "focus order pass",
        "status message pass",
        "timeout warning pass",
        "media captions pass",
    }.issubset(scenario_types)
    statuses = {
        result.expected_status
        for scenario in dataset.scenarios
        for result in scenario.expected_results
    }
    assert DynamicStatus.fail in statuses
    assert DynamicStatus.pass_status in statuses


def test_every_scenario_has_expected_criteria_and_valid_status() -> None:
    dataset = load_synthetic_dynamic_fixture_metadata()
    allowed_statuses = {status.value for status in DynamicStatus}

    for scenario in dataset.scenarios:
        assert scenario.criteria_expected
        assert scenario.expected_results
        assert set(result.criterion_id for result in scenario.expected_results).issubset(
            set(scenario.criteria_expected)
        )
        for result in scenario.expected_results:
            assert result.expected_status.value in allowed_statuses
            assert result.expected_evidence


def test_real_dynamic_human_oracle_labels_load() -> None:
    dataset = load_real_dynamic_scenario_metadata()
    oracle_rows = flatten_oracle_labels(dataset, load_real_oracle_labels())

    assert dataset.dataset_id == D_dyn_real
    assert dataset.baseline_kind == DynamicBaselineKind.HUMAN_ORACLE
    assert len(dataset.scenarios) == 19
    labelled_scenario_ids = {row["scenario_id"] for row in oracle_rows}
    pending_scenario_ids = {scenario.scenario_id for scenario in dataset.scenarios if scenario.labels_pending}
    assert len(labelled_scenario_ids) == 19
    assert not pending_scenario_ids
    assert labelled_scenario_ids.issubset({scenario.scenario_id for scenario in dataset.scenarios})
    assert {
        "real_nasa_navigation_overlay",
        "real_bbc_homepage_media_controls",
        "real_youtube_player_captions",
    }.isdisjoint({scenario.scenario_id for scenario in dataset.scenarios})
    removed_challenge_scenario_ids = {
        "real_reddit_share_dialog_focus_return",
        "real_yelp_filter_panel_results_reload",
        "real_imdb_lightbox_keyboard_navigation",
    }
    assert removed_challenge_scenario_ids.isdisjoint({scenario.scenario_id for scenario in dataset.scenarios})
    assert all(not scenario.expected_results for scenario in dataset.scenarios)
    assert all(not scenario.criteria_expected for scenario in dataset.scenarios)
    assert validate_expected_results(dataset) == []
    assert load_dynamic_dataset(D_dyn_real).dataset_id == D_dyn_real

    statuses = {row["expected_status"] for row in oracle_rows}
    assert len(oracle_rows) == 855
    assert DynamicStatus.pass_status.value in statuses
    assert DynamicStatus.fail.value in statuses
    assert DynamicStatus.not_applicable.value in statuses


def test_real_dynamic_scenarios_are_one_sentence_browser_tasks() -> None:
    dataset = load_real_dynamic_scenario_metadata()

    for scenario in dataset.scenarios:
        assert scenario.interaction_type == "browser_use_task"
        assert scenario.task_description.startswith("Open https://")
        assert "\n" not in scenario.task_description
        assert scenario.scripted_steps == ()
        assert scenario.max_steps >= 8


def test_baseline_kinds_are_recognized() -> None:
    specs = get_baseline_specs()
    kinds = {spec.kind for spec in specs}
    baseline_ids = {spec.baseline_id for spec in specs}

    assert DynamicBaselineKind.SYNTHETIC_FIXTURE in kinds
    assert DynamicBaselineKind.HUMAN_ORACLE in kinds
    assert DynamicBaselineKind.NAIVE_BROWSER_USE_GLOBAL_AUDIT in kinds
    assert NAIVE_BROWSER_USE_GLOBAL_AUDIT_BASELINE.baseline_id in baseline_ids
    assert "non-llm-dynamic-probes" not in baseline_ids
    assert "static-after-each-state" not in baseline_ids


def test_build_naive_browser_use_task_is_generic() -> None:
    task = build_naive_browser_use_task(
        "https://example.com/audit",
        12,
        ["2.1.2", "2.4.7", "9.9.9"],
    )

    assert task.startswith("Open https://example.com/audit.")
    assert "Within 12 browser-use steps" in task
    assert "explore the page freely as an accessibility auditor" in task
    assert "keyboard focus" in task
    assert "forms" in task
    assert "pointer/drag controls" in task
    assert "Return structured JSON only" in task
    assert "2.1.2, 2.4.7" in task
    assert "9.9.9" not in task


def test_normalize_naive_output_to_report_omits_invalid_and_marks_unassessed_incomplete() -> None:
    now = datetime.now(timezone.utc)
    trace = RuntimeTrace(
        scenario="s_keyboard",
        metadata=RuntimeTraceMetadata(
            start_time=now,
            end_time=now,
            duration_seconds=0.1,
            artifacts_dir="artifacts",
        ),
        steps=[
            RuntimeState(
                timestamp=now,
                url="https://example.com",
                title="Example",
                screenshot_path="screenshot_step_1.png",
            )
        ],
    )
    output = {
        "overall_summary": "Global audit summary",
        "assessments": [
            {
                "criterion_id": "2.1.2",
                "status": "fail",
                "summary": "Keyboard focus became trapped in the dialog.",
                "step_numbers": [1],
                "violations": [
                    {
                        "title": "Keyboard trap",
                        "summary": "Tab remained inside the same control sequence.",
                        "severity": "high",
                        "step_numbers": [1],
                    }
                ],
            },
            {
                "criterion_id": "9.9.9",
                "status": "fail",
                "summary": "Invalid criterion should be ignored.",
            },
        ],
    }

    report = normalize_naive_output_to_report(
        output,
        scenario_id="s_keyboard",
        requested_criteria=["2.1.2", "2.4.7", "9.9.9"],
        runtime_trace=trace,
        runtime_trace_path="runtime_trace.json",
        final_output_path="browser_use_final_result.txt",
        history_path="browser_use_history.json",
    )

    assert report.scenario == "s_keyboard"
    assert [result.criterion_id for result in report.results] == ["2.1.2", "2.4.7"]
    keyboard = report.results[0]
    assert keyboard.criterion_name == "No Keyboard Trap"
    assert keyboard.status == DynamicStatus.fail
    assert keyboard.violations
    assert "browser_use_step_1" in keyboard.violations[0].evidence_refs
    assert "screenshot_step_1.png" in keyboard.violations[0].evidence_refs
    focus = report.results[1]
    assert focus.status == DynamicStatus.incomplete
    assert focus.missing_evidence == ["naive_baseline_not_assessed"]


def test_unavailable_optional_dynamic_baseline_command_skips_cleanly() -> None:
    spec = DynamicBaselineSpec(
        baseline_id="missing-tool",
        name="Missing tool",
        kind=DynamicBaselineKind.NAIVE_BROWSER_USE_GLOBAL_AUDIT,
        command="flow-a11y-definitely-not-installed-tool",
    )

    status = baseline_availability(spec)

    assert status.status == BaselineAvailabilityStatus.SKIPPED
    assert status.available is False
    assert "not found" in status.reason


def test_dynamic_metrics_compute_recall_precision_and_confusion() -> None:
    dataset = _fake_dataset()
    reports = [
        {"scenario_id": "s_keyboard", "criterion_id": "2.1.2", "status": "fail"},
        {"scenario_id": "s_focus", "criterion_id": "2.4.7", "status": "warning"},
        {"scenario_id": "s_order_pass", "criterion_id": "2.4.3", "status": "fail"},
    ]

    recall = compute_criterion_recall(dataset, reports)
    assert recall["overall"] == {"expected": 2, "detected": 1, "missed": 1, "recall": 0.5}
    assert recall["by_criterion"]["2.1.2"]["recall"] == 1.0
    assert recall["by_criterion"]["2.4.7"]["recall"] == 0.0

    precision = compute_criterion_precision(dataset, reports)
    assert precision["overall"] == {"labelled_predictions": 2, "correct": 1, "precision": 0.5}

    confusion = {
        (row["expected_status"], row["actual_status"]): row["count"]
        for row in compute_status_confusion_counts(dataset, reports)
    }
    assert confusion[("fail", "fail")] == 1
    assert confusion[("fail", "warning")] == 1
    assert confusion[("pass", "fail")] == 1


def test_publication_metrics_cover_full_oracle_and_audit_rows() -> None:
    dataset = _fake_publication_dataset()
    oracle_rows = _fake_publication_oracle_rows()
    reports = [
        {"scenario_id": "s1", "criterion_id": "2.1.2", "status": "fail", "evidence_refs": "focus_sequence[0]"},
        {"scenario_id": "s1", "criterion_id": "2.4.7", "status": "fail", "evidence_refs": "focus_visible_trace[0]"},
        {"scenario_id": "s1", "criterion_id": "4.1.3", "status": "incomplete", "gate_decision": "incomplete"},
        {"scenario_id": "s2", "criterion_id": "2.1.2", "status": "pass", "evidence_refs": "focus_sequence[0]"},
        {"scenario_id": "s2", "criterion_id": "2.4.7", "status": "pass", "evidence_refs": "focus_visible_trace[0]"},
        {"scenario_id": "s2", "criterion_id": "4.1.3", "status": "not_applicable"},
    ]

    metrics = compute_publication_metrics(
        dataset,
        reports,
        oracle_rows=oracle_rows,
        bootstrap_iterations=100,
        bootstrap_seed=7,
    )
    point = metrics["point"]
    assert point["oracle_rows"] == 6
    assert point["exact_status_accuracy"] == 0.5
    assert point["fail"]["micro"]["precision"] == 0.5
    assert point["fail"]["micro"]["recall"] == 0.5
    assert point["fail"]["micro"]["f1"] == 0.5
    assert point["pass"]["micro"]["precision"] == 0.5
    assert point["pass"]["micro"]["recall"] == 0.5
    assert point["pass"]["binary_accuracy"] == 4 / 6
    assert point["not_applicable_accuracy"] == 0.5
    assert point["incomplete_rate"] == 1 / 6
    assert {"ci_low", "ci_high"}.issubset(metrics["confidence_intervals"]["exact_status_accuracy"])

    rows = build_dynamic_quality_summary_rows(
        metrics,
        baseline_id="rules-dynamic-wcag",
        task_success_summary={"task_success_count": 1, "task_success_denominator": 2, "task_success_rate": 0.5},
    )
    by_metric = {row["metric"]: row for row in rows}
    assert by_metric["exact_status_accuracy"]["value"] == 0.5
    assert by_metric["fail_precision_micro"]["ci_low"] is not None
    assert by_metric["task_success_rate"]["denominator"] == 2

    false_positives, false_negatives = build_false_audit_rows(dataset, reports, oracle_rows=oracle_rows)
    assert {(row["target_status"], row["scenario_id"], row["criterion_id"]) for row in false_positives} == {
        ("fail", "s1", "2.4.7"),
        ("pass", "s2", "2.1.2"),
    }
    assert {(row["target_status"], row["scenario_id"], row["criterion_id"]) for row in false_negatives} == {
        ("fail", "s2", "2.1.2"),
        ("pass", "s1", "2.4.7"),
    }


def test_bootstrap_confidence_intervals_are_deterministic() -> None:
    dataset = _fake_publication_dataset()
    oracle_rows = _fake_publication_oracle_rows()
    reports = [
        {"scenario_id": "s1", "criterion_id": "2.1.2", "status": "fail"},
        {"scenario_id": "s1", "criterion_id": "2.4.7", "status": "pass"},
        {"scenario_id": "s1", "criterion_id": "4.1.3", "status": "not_applicable"},
        {"scenario_id": "s2", "criterion_id": "2.1.2", "status": "incomplete"},
        {"scenario_id": "s2", "criterion_id": "2.4.7", "status": "pass"},
        {"scenario_id": "s2", "criterion_id": "4.1.3", "status": "not_applicable"},
    ]

    first = compute_publication_metrics(dataset, reports, oracle_rows=oracle_rows, bootstrap_iterations=50, bootstrap_seed=42)
    second = compute_publication_metrics(dataset, reports, oracle_rows=oracle_rows, bootstrap_iterations=50, bootstrap_seed=42)

    assert first["confidence_intervals"] == second["confidence_intervals"]


def test_metrics_accept_external_real_oracle_rows_not_in_scenario_json() -> None:
    dataset = DynamicBenchmarkDataset(
        dataset_id=D_dyn_real,
        name="Fake real labels",
        baseline_kind=DynamicBaselineKind.HUMAN_ORACLE,
        scenarios=(
            DynamicBenchmarkScenario(
                scenario_id="s_real_1",
                target="https://example.com/one",
                interaction_type="browser_use_task",
                task_description="Open https://example.com/one and inspect a menu.",
                scripted_steps=(),
                baseline_kind=DynamicBaselineKind.HUMAN_ORACLE,
            ),
            DynamicBenchmarkScenario(
                scenario_id="s_real_2",
                target="https://example.com/two",
                interaction_type="browser_use_task",
                task_description="Open https://example.com/two and inspect a form.",
                scripted_steps=(),
                baseline_kind=DynamicBaselineKind.HUMAN_ORACLE,
            ),
        ),
    )
    oracle_rows = [
        {
            "dataset_id": D_dyn_real,
            "scenario_id": "s_real_1",
            "criterion_id": "2.1.2",
            "expected_status": "fail",
            "baseline_kind": "human_oracle",
        },
        {
            "dataset_id": D_dyn_real,
            "scenario_id": "s_real_1",
            "criterion_id": "4.1.3",
            "expected_status": "warning",
            "baseline_kind": "human_oracle",
        },
        {
            "dataset_id": D_dyn_real,
            "scenario_id": "s_real_2",
            "criterion_id": "2.4.7",
            "expected_status": "pass",
            "baseline_kind": "human_oracle",
        },
    ]
    reports = [{"scenario_id": "s_real_1", "criterion_id": "2.1.2", "status": "fail"}]

    recall = compute_criterion_recall(dataset, reports, oracle_rows=oracle_rows)
    confusion = compute_status_confusion_counts(dataset, reports, oracle_rows=oracle_rows)
    comparison = build_baseline_comparison_rows(
        dataset,
        {"rules-dynamic-wcag": reports},
        oracle_rows=oracle_rows,
    )

    assert recall["overall"] == {"expected": 1, "detected": 1, "missed": 0, "recall": 1.0}
    assert sum(row["count"] for row in confusion) == 3
    assert len(comparison) == 3
    assert {row["criterion_id"] for row in comparison} == {"2.1.2", "4.1.3", "2.4.7"}


def test_precision_ignores_pending_incomplete_oracle_labels() -> None:
    base = _fake_dataset()
    pending = DynamicBenchmarkScenario(
        scenario_id="s_pending_real",
        target="https://example.com",
        interaction_type="browser_use_task",
        task_description="Open https://example.com and inspect a menu.",
        scripted_steps=(),
        baseline_kind=DynamicBaselineKind.HUMAN_ORACLE,
        criteria_expected=("2.4.7",),
        labels_pending=True,
        expected_results=(
            DynamicExpectedResult(
                criterion_id="2.4.7",
                expected_status=DynamicStatus.incomplete,
                expected_evidence=("pending human reviewer label",),
            ),
        ),
    )
    dataset = DynamicBenchmarkDataset(
        dataset_id=D_dyn_real,
        name="Pending real labels",
        baseline_kind=DynamicBaselineKind.HUMAN_ORACLE,
        scenarios=(*base.scenarios, pending),
    )
    reports = [
        {"scenario_id": "s_keyboard", "criterion_id": "2.1.2", "status": "fail"},
        {"scenario_id": "s_order_pass", "criterion_id": "2.4.3", "status": "fail"},
        {"scenario_id": "s_pending_real", "criterion_id": "2.4.7", "status": "fail"},
    ]

    precision = compute_criterion_precision(dataset, reports)

    assert precision["overall"] == {"labelled_predictions": 2, "correct": 1, "precision": 0.5}
    assert "2.4.7" not in precision["by_criterion"]


def test_unlabelled_all_prompt_outputs_are_excluded_from_metrics() -> None:
    dataset = _fake_dataset()
    reports = [
        {"scenario_id": "s_keyboard", "criterion_id": "2.1.2", "status": "fail"},
        {"scenario_id": "s_keyboard", "criterion_id": "4.1.3", "status": "fail"},
        {"scenario_id": "s_keyboard", "criterion_id": "2.4.7", "status": "pass"},
    ]

    recall = compute_criterion_recall(dataset, reports)
    precision = compute_criterion_precision(dataset, reports)
    confusion = compute_status_confusion_counts(dataset, reports)
    comparison = build_baseline_comparison_rows(dataset, {"gemini-dynamic-wcag": reports})

    assert recall["overall"] == {"expected": 2, "detected": 1, "missed": 1, "recall": 0.5}
    assert precision["overall"] == {"labelled_predictions": 1, "correct": 1, "precision": 1.0}
    assert len(confusion) == len(flatten_expected_results(dataset))
    assert len(comparison) == len(flatten_expected_results(dataset))
    assert all(row["criterion_id"] != "4.1.3" for row in comparison)


def test_unsupported_incomplete_counts_and_comparison_rows() -> None:
    dataset = _fake_dataset()
    reports = [
        {"scenario_id": "s_keyboard", "criterion_id": "2.1.2", "status": "fail"},
        {"scenario_id": "s_focus", "criterion_id": "2.4.7", "status": "incomplete"},
    ]

    counts = compute_unsupported_incomplete_counts(reports, unsupported_criteria=["9.9.9"])
    assert counts == {
        "results": 2,
        "incomplete": 1,
        "unsupported": 1,
        "unsupported_criteria": ["9.9.9"],
    }

    rows = build_baseline_comparison_rows(dataset, {"naive-browser-use-global-audit": reports})
    assert len(rows) == len(flatten_expected_results(dataset))
    assert rows[0]["baseline_id"] == "naive-browser-use-global-audit"
    unassessed = [row for row in rows if row["unassessed_marked_incomplete"]]
    assert unassessed
    assert all(row["actual_status"] == "incomplete" for row in unassessed)


def _fake_dataset() -> DynamicBenchmarkDataset:
    scenarios = (
        DynamicBenchmarkScenario(
            scenario_id="s_keyboard",
            target="fixture://keyboard",
            interaction_type="playwright_script",
            task_description="Keyboard trap fixture",
            scripted_steps=("press Tab",),
            baseline_kind=DynamicBaselineKind.SYNTHETIC_FIXTURE,
            criteria_expected=("2.1.2",),
            expected_results=(
                DynamicExpectedResult(
                    criterion_id="2.1.2",
                    expected_status=DynamicStatus.fail,
                    expected_evidence=("focus repeats",),
                ),
            ),
        ),
        DynamicBenchmarkScenario(
            scenario_id="s_focus",
            target="fixture://focus",
            interaction_type="playwright_script",
            task_description="Focus indicator fixture",
            scripted_steps=("press Tab",),
            baseline_kind=DynamicBaselineKind.SYNTHETIC_FIXTURE,
            criteria_expected=("2.4.7",),
            expected_results=(
                DynamicExpectedResult(
                    criterion_id="2.4.7",
                    expected_status=DynamicStatus.fail,
                    expected_evidence=("no focus indicator",),
                ),
            ),
        ),
        DynamicBenchmarkScenario(
            scenario_id="s_order_pass",
            target="fixture://order",
            interaction_type="playwright_script",
            task_description="Focus order pass fixture",
            scripted_steps=("press Tab",),
            baseline_kind=DynamicBaselineKind.SYNTHETIC_FIXTURE,
            criteria_expected=("2.4.3",),
            expected_results=(
                DynamicExpectedResult(
                    criterion_id="2.4.3",
                    expected_status=DynamicStatus.pass_status,
                    expected_evidence=("focus order is logical",),
                ),
            ),
        ),
    )
    return DynamicBenchmarkDataset(
        dataset_id=D_dyn_fixture,
        name="Fake fixture metrics dataset",
        baseline_kind=DynamicBaselineKind.SYNTHETIC_FIXTURE,
        scenarios=scenarios,
    )


def _fake_publication_dataset() -> DynamicBenchmarkDataset:
    scenarios = tuple(
        DynamicBenchmarkScenario(
            scenario_id=scenario_id,
            target=f"https://example.com/{scenario_id}",
            interaction_type="browser_use_task",
            task_description=f"Open https://example.com/{scenario_id} and inspect dynamic behavior.",
            scripted_steps=(),
            baseline_kind=DynamicBaselineKind.HUMAN_ORACLE,
        )
        for scenario_id in ("s1", "s2")
    )
    return DynamicBenchmarkDataset(
        dataset_id=D_dyn_real,
        name="Fake publication real dataset",
        baseline_kind=DynamicBaselineKind.HUMAN_ORACLE,
        scenarios=scenarios,
    )


def _fake_publication_oracle_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    expected = {
        "s1": {"2.1.2": "fail", "2.4.7": "pass", "4.1.3": "not_applicable"},
        "s2": {"2.1.2": "fail", "2.4.7": "pass", "4.1.3": "not_applicable"},
    }
    for scenario_id, labels in expected.items():
        for criterion_id, status in labels.items():
            rows.append(
                {
                    "dataset_id": D_dyn_real,
                    "scenario_id": scenario_id,
                    "criterion_id": criterion_id,
                    "expected_status": status,
                    "baseline_kind": "human_oracle",
                }
            )
    return rows
