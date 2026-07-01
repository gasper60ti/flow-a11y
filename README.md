# Flow-A11y: Flow-Aware Accessibility Testing

Flow-A11y evaluates accessibility barriers that emerge while a user
completes an interaction scenario. It executes a natural-language browser
task, records an ordered runtime trace, projects the trace into
criterion-specific evidence, and produces auditable WCAG judgments.

This anonymous research artifact contains the restored v4 implementation,
the 19-scenario benchmark and 855-row oracle, all 45 criterion prompts,
deterministic tests, one publication analysis notebook, and the normalized
tables used for the paper. The 2.6 GB raw browser traces and screenshots are
not included.

## Architecture

```text
interaction scenario
  -> browser-use execution
  -> runtime instrumentation and per-step capture
  -> nine dynamic probe families
  -> criterion evidence packet
  -> applicability/evidence gate
  -> Gemini criterion judgment when applicable
  -> evidence-reference and fail-confidence validation
  -> calibrated status and normalized result tables
```

The formal statuses are `pass`, `fail`, `warning`, `not_applicable`, and
`incomplete`. Oracle labels are never injected into browser tasks, prompts,
runtime traces, or criterion judgments.

## Artifact Layout

| Path | Contents |
| --- | --- |
| `a11y-agent/dynamic/` | Runtime capture, probes, evidence packets, analyzers, schemas, and prompts |
| `a11y-agent/benchmarking/` | Benchmark runners, fixtures, oracle loading, metrics, and baseline |
| `a11y-agent/benchmarking/dynamic/publication_results/v4/` | Saved normalized v4 tables and checksums |
| `a11y-agent/benchmarking/dynamic/publication_analysis.ipynb` | Executable publication analysis |
| `a11y-agent/tests/` | Deterministic regression and artifact-integrity tests |

## Installation

Python 3.11 or newer and a Chrome-compatible browser are required for live
execution. Offline table reproduction does not require a browser or API key.

```bash
cd a11y-agent
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

For live runs only:

```bash
python -m playwright install chromium
cp .env.example .env
# Set GEMINI_API_KEY in .env or the shell.
```

## Verify the Artifact

Run deterministic tests, execute the notebook, and verify result checksums:

```bash
cd a11y-agent
make verify
```

The live integration test is excluded from `make verify` because it requires
network access and a Gemini API key.

## Reproduce the Reported Results

Execute the notebook against the bundled tables:

```bash
cd a11y-agent
python -m jupyter nbconvert \
  --to notebook \
  --execute benchmarking/dynamic/publication_analysis.ipynb \
  --output /tmp/flow-a11y-publication-analysis.ipynb \
  --ExecutePreprocessor.timeout=180
```

To use another compatible result directory:

```bash
export DYNAMIC_RESULTS_DIR=/absolute/path/to/results
```

## Run a Deterministic Smoke Test

```bash
cd a11y-agent
python -m benchmarking.run_benchmark \
  --mode dynamic \
  --scenario-set fixture \
  --dry-run \
  --max-scenarios 2 \
  --dynamic-output-dir /tmp/flow-a11y-fixture
```

## Run the Live Benchmark

```bash
cd a11y-agent
python -m benchmarking.dynamic.run_dynamic_benchmark \
  --scenario-set real \
  --scenario-file benchmarking/fixtures/dynamic/real_scenarios.json \
  --oracle-file benchmarking/fixtures/dynamic/real_oracle_labels.csv \
  --execution-mode browser-use \
  --dynamic-judge gemini \
  --dynamic-criteria-scope all-prompts \
  --comparison-baseline naive-browser-use-global-audit \
  --browser-use-model gemini-3.5-flash \
  --browser-use-fallback-model gemini-2.5-flash-lite \
  --gemini-model gemini-2.5-flash \
  --output-dir /tmp/flow-a11y-live-results
```

Live reruns are not expected to reproduce identical traces. Public websites,
network conditions, browser-agent actions, and model responses can change.

## Publication Results

The benchmark attempted 19 scenarios and 45 criteria, producing 855 attempted
scenario-criterion rows. Seventeen scenarios completed the scoring gate,
yielding 765 primary rows.

### RQ1: Effectiveness on Real Interaction Flows

| Metric | Result |
| --- | ---: |
| Completed scenarios | 17 / 19 |
| Primary scored rows | 765 / 855 |
| Exact five-way status accuracy (95% CI) | 349 / 765 (45.6%; 43.0-48.2%) |
| Fail precision (95% CI) | 12 / 29 (41.4%; 17.5-64.7%) |
| Fail recall (95% CI) | 12 / 31 (38.7%; 13.8-68.2%) |
| Fail F1 | 40.0% |
| Pass precision | 83 / 105 (79.0%) |
| Pass recall | 83 / 182 (45.6%) |
| Pass F1 | 57.8% |
| Fail-or-warning issue recall | 18 / 31 (58.1%) |
| Not-applicable accuracy | 252 / 517 (48.7%) |
| Incomplete rate | 304 / 765 (39.7%) |

The exact metric requires the predicted status to match one of the five oracle
statuses: `fail`, `warning`, `pass`, `not_applicable`, or `incomplete`.

#### Status Distribution

| Status | Oracle | Flow-A11y | Naive audit |
| --- | ---: | ---: | ---: |
| Fail | 31 | 29 | 3 |
| Warning | 35 | 35 | 5 |
| Pass | 182 | 105 | 34 |
| Not applicable | 517 | 292 | 0 |
| Incomplete | 0 | 304 | 723 |

#### Flow-A11y Status Confusion

Rows are oracle statuses and columns are Flow-A11y outputs.

| Oracle \ Output | Fail | Warning | Pass | Not applicable | Incomplete |
| --- | ---: | ---: | ---: | ---: | ---: |
| Fail | 12 | 6 | 7 | 0 | 6 |
| Warning | 2 | 2 | 2 | 5 | 24 |
| Pass | 12 | 15 | 83 | 35 | 37 |
| Not applicable | 3 | 12 | 13 | 252 | 237 |

### RQ2: Comparison with the Naive Browser-Agent Audit

The naive browser-use global audit achieved 25 exact matches (3.3%), returned
723 rows as `incomplete` (94.5%), and detected none of the 31 oracle failures.
These results support criterion-specific runtime evidence over unconstrained
browser-agent accessibility review, but they are not sufficient for autonomous
conformance assessment.

| Metric | Flow-A11y | Naive audit |
| --- | ---: | ---: |
| Exact matches | 349 / 765 | 25 / 765 |
| Exact accuracy | 45.6% | 3.3% |
| Fail true positives | 12 | 0 |
| Fail predictions | 29 | 3 |
| Fail precision | 41.4% | 0.0% |
| Fail recall | 38.7% | 0.0% |
| Incomplete rate | 39.7% | 94.5% |

### RQ3: Criterion-Family Outcomes

| Probe family | Criteria | Rows | Exact | Accuracy | Fail TP / Oracle fail |
| --- | ---: | ---: | ---: | ---: | ---: |
| Media | 10 | 170 | 170 | 100.0% | 0 / 0 |
| Keyboard and focus | 10 | 170 | 100 | 58.8% | 10 / 23 |
| Pointer and gesture | 5 | 85 | 47 | 55.3% | 0 / 0 |
| Status messages | 1 | 17 | 8 | 47.1% | 0 / 2 |
| Hover/focus-revealed content | 1 | 17 | 7 | 41.2% | 2 / 3 |
| Forms, errors, and authentication | 8 | 136 | 17 | 12.5% | 0 / 0 |
| Animation and flashing | 3 | 51 | 0 | 0.0% | 0 / 0 |
| Timing and interruption | 4 | 68 | 0 | 0.0% | 0 / 0 |
| Consistency and navigation | 3 | 51 | 0 | 0.0% | 0 / 3 |

The media result reflects an all-`not_applicable` oracle distribution and is
not evidence of media-analysis capability. The zero-accuracy families identify
limitations of the current scenario-probe configuration rather than stable
population-level estimates.

### Model-Call Validity and Cost

| Quantity | Result |
| --- | ---: |
| Formal Gemini calls | 203 |
| Valid verified responses | 201 |
| Invalid JSON responses | 2 |
| Unresolvable evidence references | 0 |
| Prompt tokens | 17,102,567 |
| Output tokens | 31,083 |
| Total tokens | 17,507,120 |
| Formal-judge time | 2,746.16 s |
| Average formal-call time | 13.53 s |

### Historical Pre-Layer Comparison

The paper also reports a descriptive comparison with a prior pipeline lacking
the criterion evidence packet, fail-confidence validator, and calibrated
status router.

| Metric | Pre-layer run | Final pipeline |
| --- | ---: | ---: |
| Scored rows | 630 | 765 |
| Exact status accuracy | 57.5% | 45.6% |
| Fail precision | 23.5% | 41.4% |
| Fail recall | 72.7% | 38.7% |
| API calls | 609 | 203 |
| Invalid evidence references | 35 | 0 |

Because the runs contain different completed scenarios and scored
denominators, this comparison is historical and descriptive; it does not
isolate the causal effect of an individual pipeline component.

The bundled publication notebook and normalized CSV files are the source of
truth for these results. In particular, see `dynamic_quality_summary.csv`,
`status_confusion.csv`, `baseline_comparison.csv`,
`criteria_support_matrix.csv`, `model_calls.csv`, and
`model_call_verification.csv`.

## Supported Dynamic WCAG Criteria

The registry contains 45 WCAG 2.2 criteria mapped to nine probe families.

| Criterion | Name | Level | Probe family | Required evidence |
| --- | --- | --- | --- | --- |
| 1.2.1 | Audio-only and Video-only (Prerecorded) | A | `media_probe` | media_elements |
| 1.2.2 | Captions (Prerecorded) | A | `media_probe` | media_elements, caption_tracks |
| 1.2.3 | Audio Description or Media Alternative (Prerecorded) | A | `media_probe` | media_elements, transcript_indicators |
| 1.2.4 | Captions (Live) | AA | `media_probe` | media_elements, caption_tracks |
| 1.2.5 | Audio Description (Prerecorded) | AA | `media_probe` | media_elements, transcript_indicators |
| 1.2.6 | Sign Language (Prerecorded) | AAA | `media_probe` | media_elements, transcript_indicators |
| 1.2.7 | Extended Audio Description (Prerecorded) | AAA | `media_probe` | media_elements, transcript_indicators |
| 1.2.8 | Media Alternative (Prerecorded) | AAA | `media_probe` | media_elements, transcript_indicators |
| 1.2.9 | Audio-only (Live) | AAA | `media_probe` | media_elements |
| 1.4.7 | Low or No Background Audio | AAA | `media_probe` | media_elements, background_audio_indicators |
| 1.4.13 | Content on Hover or Focus | AA | `hover_focus_content_probe` | hover_focus_revealed_content, focus_sequence |
| 2.1.1 | Keyboard | A | `keyboard_focus_probe` | keyboard_events, focus_sequence |
| 2.1.2 | No Keyboard Trap | A | `keyboard_focus_probe` | keyboard_events, focus_sequence |
| 2.1.3 | Keyboard (No Exception) | AAA | `keyboard_focus_probe` | keyboard_events, focus_sequence |
| 2.1.4 | Character Key Shortcuts | A | `keyboard_focus_probe` | character_key_shortcuts, keyboard_events |
| 2.2.3 | No Timing | AAA | `timing_interruption_probe` | timing_snapshots |
| 2.2.4 | Interruptions | AAA | `timing_interruption_probe` | interruption_signals |
| 2.2.5 | Re-authenticating | AAA | `timing_interruption_probe` | reauthentication_events |
| 2.2.6 | Timeouts | AAA | `timing_interruption_probe` | timeout_warnings |
| 2.3.1 | Three Flashes or Below Threshold | A | `animation_flashing_probe` | flash_events |
| 2.3.2 | Three Flashes | AAA | `animation_flashing_probe` | flash_events |
| 2.3.3 | Animation from Interactions | AAA | `animation_flashing_probe` | interaction_animation_events |
| 2.4.3 | Focus Order | A | `keyboard_focus_probe` | focus_sequence |
| 2.4.7 | Focus Visible | AA | `keyboard_focus_probe` | focus_visible_trace, screenshot_paths |
| 2.4.11 | Focus Not Obscured (Minimum) | AA | `keyboard_focus_probe` | focus_obscured_trace, focus_sequence |
| 2.4.12 | Focus Not Obscured (Enhanced) | AAA | `keyboard_focus_probe` | focus_obscured_trace, focus_sequence |
| 2.4.13 | Focus Appearance | AAA | `keyboard_focus_probe` | focus_visible_trace, screenshot_paths |
| 2.5.1 | Pointer Gestures | A | `pointer_gesture_probe` | pointer_handlers |
| 2.5.2 | Pointer Cancellation | A | `pointer_gesture_probe` | pointer_cancellation_indicators |
| 2.5.4 | Motion Actuation | A | `pointer_gesture_probe` | motion_actuation_indicators |
| 2.5.6 | Concurrent Input Mechanisms | AAA | `pointer_gesture_probe` | concurrent_input_indicators |
| 2.5.7 | Dragging Movements | AA | `pointer_gesture_probe` | dragging_indicators |
| 3.2.1 | On Focus | A | `keyboard_focus_probe` | focus_change_events, focus_sequence |
| 3.2.3 | Consistent Navigation | AA | `consistency_navigation_probe` | navigation_snapshots, url_sequence |
| 3.2.4 | Consistent Identification | AA | `consistency_navigation_probe` | identification_snapshots, url_sequence |
| 3.2.6 | Consistent Help | A | `consistency_navigation_probe` | help_snapshots, url_sequence |
| 3.3.1 | Error Identification | A | `forms_errors_auth_probe` | form_snapshots, error_messages |
| 3.3.3 | Error Suggestion | AA | `forms_errors_auth_probe` | form_snapshots, error_messages |
| 3.3.4 | Error Prevention (Legal, Financial, Data) | AA | `forms_errors_auth_probe` | form_snapshots |
| 3.3.5 | Help | AAA | `forms_errors_auth_probe` | help_for_forms |
| 3.3.6 | Error Prevention (All) | AAA | `forms_errors_auth_probe` | form_snapshots |
| 3.3.7 | Redundant Entry | A | `forms_errors_auth_probe` | redundant_entry_indicators, form_snapshots |
| 3.3.8 | Accessible Authentication (Minimum) | AA | `forms_errors_auth_probe` | auth_snapshots |
| 3.3.9 | Accessible Authentication (Enhanced) | AAA | `forms_errors_auth_probe` | auth_snapshots |
| 4.1.3 | Status Messages | AA | `status_message_probe` | status_message_mutations |

## Public-Web Scenarios

| Scenario | Website | Interaction | Steps | v4 status |
| --- | --- | --- | ---: | --- |
| `real_govuk_search_subscribe` | www.gov.uk | Search and footer subscription | 14 | Scored |
| `real_wikipedia_search_navigation` | www.wikipedia.org | Search result navigation | 14 | Scored |
| `real_w3c_menu_navigation` | www.w3.org | Global navigation menu | 10 | Scored |
| `real_mdn_sidebar_toc_navigation` | developer.mozilla.org | Documentation sidebar and table of contents | 16 | Scored |
| `real_usps_zip_lookup_errors` | tools.usps.com | Form validation and error feedback | 12 | Scored |
| `real_webaim_contrast_checker_status` | webaim.org | Contrast checker form feedback | 10 | Scored |
| `real_github_repository_menu` | github.com | Repository disclosure menu | 12 | Scored |
| `real_uswds_accordion_keyboard` | designsystem.digital.gov | Accordion keyboard expansion | 18 | Scored |
| `real_bootstrap_modal_focus_escape` | getbootstrap.com | Modal focus trap and Escape | 14 | Scored |
| `real_nps_find_a_park_filter` | www.nps.gov | Search form and filter controls | 14 | Scored |
| `real_nhs_search_results_navigation` | www.nhs.uk | Health-site search navigation | 12 | Scored |
| `real_cdc_search_results_navigation` | www.cdc.gov | Public-health search navigation | 12 | Scored |
| `real_data_gov_dataset_filter` | catalog.data.gov | Dataset filters and result update | 14 | Scored |
| `real_wordpress_plugin_search_navigation` | wordpress.org | Plugin-directory search navigation | 12 | Scored |
| `real_python_docs_search_navigation` | docs.python.org | Documentation search navigation | 12 | Unscored: extraction error |
| `real_usajobs_filter_panel` | www.usajobs.gov | Job filter panel and result update | 14 | Unscored: scoring gate |
| `real_microsoft_learn_search_filter` | learn.microsoft.com | Documentation filter and navigation | 14 | Scored |
| `real_amazon_image_lightbox_keyboard` | www.amazon.com | Image thumbnails and zoom lightbox | 16 | Scored |
| `real_weather_tab_accordion_expansion` | weather.com | Tab panel and accordion expansion | 12 | Scored |

## Artifact Scope and Limitations

- The saved tables reproduce the paper's metrics without browser or model calls.
- Raw trace-level artifacts are intentionally excluded from this compact repository.
- The benchmark is manually curated rather than a random sample of websites.
- Two scenarios were unscored, and only 31 scored rows are oracle failures.
- The artifact does not execute screen readers or other assistive technologies.
- Live results can change as websites, browser agents, and models evolve.

No software license is granted in this anonymous review artifact.
