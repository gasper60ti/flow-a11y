# Real Dynamic Scenario Labeling Guide

`real_scenarios.json` contains the exact public-web scenario definitions used
by the saved v4 publication run. `real_oracle_labels.csv` contains the complete
855-row author-reviewed oracle used only for metrics.

`real_scenarios_reviewed_latest.json` preserves later wording changes that were
not used to compute the bundled v4 tables.

## Review Inputs

For each scenario, inspect:

- `benchmarking/dynamic/results_real/raw/run_1/<scenario_id>/runtime_trace.json`
- `benchmarking/dynamic/results_real/raw/run_1/<scenario_id>/scenario_result.json`
- `benchmarking/dynamic/results_real/raw/run_1/<scenario_id>/screenshot_step_*.png`
- `benchmarking/dynamic/results_real/raw/run_1/<scenario_id>/dom_snapshot_step_*.html`
- `benchmarking/dynamic/results_real/raw/run_1/<scenario_id>/focus_state_step_*.json`
- `benchmarking/dynamic/results_real/raw/run_1/<scenario_id>/keyboard_state_step_*.json`

Do not use `formal_dynamic_wcag_results.json` as the source of truth for manual labels. That file is the method output being evaluated.

## Label Statuses

- `pass`: the reviewer directly observed enough evidence that the criterion passed for this task.
- `fail`: the reviewer directly observed a WCAG failure for this task.
- `warning`: the evidence suggests a possible issue, but it is not strong enough for pass/fail.
- `not_applicable`: the criterion does not apply to the completed task state.
- `incomplete`: the scenario did not reach the required state, the site blocked the flow, or evidence is insufficient.

## Review Procedure

1. Open the raw scenario folder.
2. Read `scenario_result.json` for `run_status`, `steps_count`, and the task.
3. Inspect screenshots and focus/keyboard JSON in chronological order.
4. Repeat the task manually in a browser when the artifact is ambiguous.
5. Update `expected_status`, `evidence`, and `notes` in a copy of the external
   real-scenario oracle file.
6. Leave `labels_pending: false` only after a reviewer has adjudicated the scenario.

For publication, report how many labels are pass, fail, warning,
not applicable, and incomplete, and retain anonymized reviewer/adjudication
provenance with the oracle file.
