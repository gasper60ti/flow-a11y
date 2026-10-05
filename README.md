# FlowA11y: Flow-Aware Accessibility Testing

FlowA11y evaluates accessibility barriers that emerge while a user completes an
interaction scenario. It executes a natural-language browser task, records an
ordered runtime trace, projects that trace into criterion-specific evidence,
and produces auditable WCAG defect decisions.

This anonymous research artifact contains the dynamic implementation, the
19-scenario benchmark and anonymized 855-row oracle, all 45 criterion prompts,
deterministic tests, Baseline 2.0 and Baseline 3.0, and the compact V5 package
used to reproduce the paper's repeated-run binary analysis. Raw browser traces,
screenshots, model responses, caches, and live-site outputs are excluded.

## Architecture

```text
interaction scenario
  -> browser-use execution
  -> runtime instrumentation and per-step capture
  -> nine dynamic probe families
  -> criterion-specific evidence packet
  -> applicability and evidence gate
  -> Gemini criterion judgment when applicable
  -> evidence-reference and fail-confidence validation
  -> calibrated binary decision and normalized result tables
```

A scenario is the unit of assessment. The runtime trace contains browser states
reached while performing a concrete task, rather than a page snapshot alone.
For each WCAG criterion, FlowA11y builds a compact evidence packet from the
relevant dynamic probes. The publication outcome is `TRIGGER` when the observed
scenario supports an automatic defect report and `NOT_TRIGGER` otherwise.
`NOT_TRIGGER` means no automatic defect report from the observed trace; it does
not establish WCAG conformance or website-wide accessibility.

Oracle labels are never injected into browser tasks, prompts, runtime traces,
or criterion judgments.

## Baselines

- **Naive baseline:** a separate generic browser-use accessibility audit. This
  is an unequal-input descriptive reference.
- **Baseline 2.0:** reuses FlowA11y's completed scenario, criterion targets,
  and runtime trace, but judges a bounded generic trace view without the
  structured runtime-analysis layers.
- **Baseline 3.0:** shares the trace, criterion targets, Gemini configuration,
  prompt templates, gate decisions, parser, and verifier. It replaces
  FlowA11y's criterion-specific packet with one global all-probes evidence
  packet.

The two matched-input baselines test the contribution of the structured
runtime-analysis pipeline and criterion-specific evidence selection without
rerunning the browser for the comparator.

## Artifact Layout

| Path | Contents |
| --- | --- |
| `dynamic/` | Runtime capture, probes, evidence packets, analyzers, schemas, and prompts. |
| `benchmarking/` | Benchmark runners, fixtures, oracle loading, metrics, and baseline implementations. |
| `benchmarking/dynamic/publication_results/v5/` | Compact saved inputs, derived binary tables, figures, manifests, and checksums. |
| `benchmarking/dynamic/publication_analysis.ipynb` | Executable binary-only publication analysis. |
| `benchmarking/dynamic/publication_binary_analysis.py` | Offline analysis used by the notebook and verification workflow. |
| `tests/` | Deterministic regression, baseline-contract, and artifact-integrity tests. |

The V5 package has 32 saved input CSVs: the fixed 765-row evaluation frame, a
complete anonymized oracle, and three normalized result tables for each of ten
repeated runs. `binary_derivation_manifest.csv` links every regenerated table,
report, and figure to those inputs.

## Installation

Python 3.11 or newer is required. A Chrome-compatible browser is needed only
for new live executions; offline reproduction uses saved tables and needs no
browser or API key.

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r benchmarking/requirements-benchmark.txt
```

For live runs only:

```bash
python -m playwright install chromium
cp .env.example .env
# Set GEMINI_API_KEY in .env or the shell.
```

## Verify the Artifact

Run the deterministic tests, execute the binary notebook, and verify every V5
checksum:

```bash
make verify
```

The verification workflow does not launch a browser or call Gemini. The live
integration test remains excluded because it requires network access and a
Gemini API key.

## Reproduce the Reported Results

The reviewer-facing notebook runs entirely offline against the bundled V5
inputs and writes regenerated files to a temporary directory:

```bash
FLOWA11Y_PUBLICATION_OUTPUT_DIR=/tmp/flow-a11y-v5-analysis \
  python -m jupyter nbconvert \
  --to notebook \
  --execute benchmarking/dynamic/publication_analysis.ipynb \
  --output /tmp/flow-a11y-publication-analysis.ipynb \
  --ExecutePreprocessor.timeout=180
```

The generated output contains the row-level binary decisions, 2x2 confusion
matrices, TP/FP/TN/FN counts, precision, recall, F1, MCC, specificity,
balanced accuracy, ordinary accuracy, `NOT_TRIGGER` reason breakdowns, and
publication figures. Set `FLOWA11Y_PUBLICATION_INPUT_DIR` only when using a
compatible copy of the bundled `source_inputs` directory.

## Run a Deterministic Smoke Test

```bash
python -m benchmarking.run_benchmark \
  --mode dynamic \
  --scenario-set fixture \
  --dry-run \
  --max-scenarios 2 \
  --dynamic-output-dir /tmp/flow-a11y-fixture
```

## Run the Live Benchmark

Complete live commands are in [`benchmarking/README.md`](benchmarking/README.md).
Choose one comparator for each run:

```text
naive-browser-use-global-audit
baseline-2.0-matched-trace-generic-judge
baseline-3.0-matched-global-evidence-ablation
```

For example, Baseline 3.0 uses the following comparator flag:

```bash
python -m benchmarking.dynamic.run_dynamic_benchmark \
  --scenario-set real \
  --scenario-file benchmarking/fixtures/dynamic/real_scenarios.json \
  --oracle-file benchmarking/fixtures/dynamic/real_oracle_labels.csv \
  --execution-mode browser-use \
  --browser-max-steps 12 \
  --dynamic-judge gemini \
  --dynamic-criteria-scope all-prompts \
  --comparison-baseline baseline-3.0-matched-global-evidence-ablation \
  --gemini-model gemini-2.5-flash \
  --output-dir /tmp/flow-a11y-baseline3
```

Live reruns require a Gemini API key, a Chrome-compatible browser,
`browser-use`, network access, and the public websites in the scenario fixture.
Browser actions, websites, network conditions, and model responses can change,
so a live rerun is not expected to reproduce the saved traces exactly.

## Publication Results

The binary report evaluates 765 fixed scenario--criterion rows: 17 successfully
completed scenarios by 45 criteria. `FAIL` maps to `TRIGGER`; `WARNING`, `PASS`,
`NOT_APPLICABLE`, and `INCOMPLETE` map to `NOT_TRIGGER`. Values below are means
$\pm$ sample standard deviations across the repeated runs in each cohort.

### RQ1: Effectiveness on Real Interaction Flows

Across the four original repeated runs, FlowA11y matched the binary oracle on
`694.0 ± 7.1` of 765 rows per run (`90.7% ± 0.9%` accuracy). There are 31
oracle `TRIGGER` rows per run. FlowA11y detects `21.5 ± 2.6` of them, with
`26.0% ± 2.6%` precision, `69.4% ± 8.5%` recall, `37.7% ± 3.7%` F1,
`38.7% ± 4.6%` MCC, and `80.5% ± 4.1%` balanced accuracy.

Overall accuracy is strongly affected by the class distribution: 734 of 765
oracle rows (`95.9%`) are `NOT_TRIGGER`. It should therefore be read together
with trigger precision, recall, F1, MCC, and balanced accuracy.

| Metric | FlowA11y, 4 original runs |
| --- | ---: |
| Binary decision accuracy | `90.7% ± 0.9%` |
| `TRIGGER` precision | `26.0% ± 2.6%` |
| `TRIGGER` recall | `69.4% ± 8.5%` |
| `TRIGGER` F1 | `37.7% ± 3.7%` |
| MCC | `38.7% ± 4.6%` |
| `NOT_TRIGGER` specificity | `91.6% ± 1.0%` |
| Balanced accuracy | `80.5% ± 4.1%` |

### RQ2: Comparison with Generic and Matched-Input Baselines

| Cohort | Method | Runs | Precision | Recall | F1 | MCC | Balanced accuracy |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Original naive comparison | FlowA11y | 4 | `26.0% ± 2.6%` | `69.4% ± 8.5%` | `37.7% ± 3.7%` | `38.7% ± 4.6%` | `80.5% ± 4.1%` |
| Original naive comparison | Naive baseline | 4 | `3.6% ± 7.1%` | `0.8% ± 1.6%` | `1.3% ± 2.6%` | `0.2% ± 3.3%` | `50.1% ± 0.8%` |
| Baseline 2.0 matched trace | FlowA11y | 3 | `27.2% ± 0.4%` | `64.5% ± 5.6%` | `38.2% ± 1.0%` | `38.2% ± 1.9%` | `78.6% ± 2.5%` |
| Baseline 2.0 matched trace | Baseline 2.0 | 3 | `19.7% ± 4.2%` | `41.9% ± 8.5%` | `26.8% ± 5.6%` | `24.4% ± 6.4%` | `67.4% ± 4.5%` |
| Baseline 3.0 global-evidence ablation | FlowA11y | 3 | `25.7% ± 1.4%` | `62.4% ± 3.7%` | `36.4% ± 1.9%` | `36.2% ± 2.3%` | `77.4% ± 1.9%` |
| Baseline 3.0 global-evidence ablation | Baseline 3.0 | 3 | `22.9% ± 3.2%` | `60.2% ± 6.7%` | `33.2% ± 4.3%` | `33.0% ± 5.0%` | `75.8% ± 3.6%` |

The naive audit is an unequal-input descriptive reference and detects almost no
oracle `TRIGGER` decisions. Baseline 2.0 shows the contribution of FlowA11y's
structured runtime-analysis layers. Baseline 3.0 isolates criterion-specific
evidence selection; its smaller gap shows that global evidence retains useful
information, while the full criterion-specific pipeline remains better on this
benchmark.

### RQ3: Criterion-Family Capability and Limitations

Observed `TRIGGER` detections concentrate in keyboard/focus and hover/focus
content. Across the four original FlowA11y runs, the keyboard/focus probe
correctly detects 68 of 92 oracle trigger occurrences, and the hover/focus
content probe detects 8 of 12. Consistency/navigation also detects 8 of 12,
while status messages detects 2 of 8.

Several families have no oracle trigger rows in the 765-row frame. Their high
binary accuracy assesses `NOT_TRIGGER` decisions only; it does not establish
defect-detection capability for media, pointer/gesture, forms/errors/
authentication, animation/flashing, or timing/interruption criteria.

## Supported Dynamic WCAG Criteria

The registry covers 45 WCAG 2.2 criteria through nine probe families.

| Probe family | Criteria |
| --- | ---: |
| Media | 10 |
| Keyboard and focus | 10 |
| Pointer and gesture | 5 |
| Status messages | 1 |
| Hover and focus-revealed content | 1 |
| Forms, errors, and authentication | 8 |
| Animation and flashing | 3 |
| Timing and interruption | 4 |
| Consistency and navigation | 3 |

The criterion definitions, required evidence, prompts, and registry mappings
are available under `dynamic/preprocessors/` and `dynamic/prompts/`.

## Public-Web Scenarios

The benchmark contains 19 manually specified public-web interaction scenarios.
Seventeen form the fixed primary scoring frame; two are retained in the fixture
but are outside that frame because they did not complete the scoring gate.

| Scenario | Website | Interaction | Primary frame |
| --- | --- | --- | --- |
| `real_govuk_search_subscribe` | www.gov.uk | Search and footer subscription | Yes |
| `real_wikipedia_search_navigation` | www.wikipedia.org | Search result navigation | Yes |
| `real_w3c_menu_navigation` | www.w3.org | Global navigation menu | Yes |
| `real_mdn_sidebar_toc_navigation` | developer.mozilla.org | Sidebar and table of contents | Yes |
| `real_usps_zip_lookup_errors` | tools.usps.com | Form validation and error feedback | Yes |
| `real_webaim_contrast_checker_status` | webaim.org | Contrast-checker feedback | Yes |
| `real_github_repository_menu` | github.com | Repository disclosure menu | Yes |
| `real_uswds_accordion_keyboard` | designsystem.digital.gov | Accordion keyboard expansion | Yes |
| `real_bootstrap_modal_focus_escape` | getbootstrap.com | Modal focus and Escape | Yes |
| `real_nps_find_a_park_filter` | www.nps.gov | Search form and filters | Yes |
| `real_nhs_search_results_navigation` | www.nhs.uk | Search-result navigation | Yes |
| `real_cdc_search_results_navigation` | www.cdc.gov | Search-result navigation | Yes |
| `real_data_gov_dataset_filter` | catalog.data.gov | Dataset filters and results | Yes |
| `real_wordpress_plugin_search_navigation` | wordpress.org | Plugin-directory search | Yes |
| `real_python_docs_search_navigation` | docs.python.org | Documentation search | No |
| `real_usajobs_filter_panel` | www.usajobs.gov | Job-filter panel | No |
| `real_microsoft_learn_search_filter` | learn.microsoft.com | Documentation filter and navigation | Yes |
| `real_amazon_image_lightbox_keyboard` | www.amazon.com | Image thumbnails and lightbox | Yes |
| `real_weather_tab_accordion_expansion` | weather.com | Tab panel and accordion | Yes |

## Artifact Scope and Limitations

- The saved V5 package reproduces the reported binary metrics without browser
  interaction or model calls.
- Source tables retain relative provenance references to omitted raw artifacts;
  the raw artifacts themselves are not included.
- The benchmark is manually curated rather than a random sample of websites.
- Only 31 of the 765 primary rows are oracle `TRIGGER` rows, so trigger metrics
  have greater sampling variability than aggregate accuracy.
- The artifact does not execute screen readers or other assistive technologies.
- Live outcomes can change as websites, browser agents, and models evolve.

No software license is granted in this anonymous review artifact.
