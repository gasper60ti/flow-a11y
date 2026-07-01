# Dynamic Benchmarking

The artifact contains only the dynamic WCAG benchmark. Static and combined
benchmark modes are not part of this repository.

## Reproducible Offline Analysis

The compact v4 tables are stored in:

```text
benchmarking/dynamic/publication_results/v4/
```

Execute the publication notebook:

```bash
python -m jupyter nbconvert \
  --to notebook \
  --execute benchmarking/dynamic/publication_analysis.ipynb \
  --output /tmp/flow-a11y-publication-analysis.ipynb \
  --ExecutePreprocessor.timeout=180
```

This path requires neither browser interaction nor a Gemini API key.

## Deterministic Fixture Check

```bash
python -m benchmarking.run_benchmark \
  --mode dynamic \
  --scenario-set fixture \
  --dry-run \
  --max-scenarios 2 \
  --dynamic-output-dir /tmp/flow-a11y-fixture
```

## Live Real-Scenario Run

Live execution requires `GEMINI_API_KEY`, Chromium, network access, and the
public websites in the scenario fixture:

```bash
python -m playwright install chromium

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

Live outputs can differ from the saved run because browser-agent actions,
websites, network conditions, and model responses are nondeterministic.

## Fixtures

- `real_scenarios.json`: exact scenario definitions used by the saved v4 run.
- `real_scenarios_reviewed_latest.json`: later reviewed wording retained for
  provenance but not used to compute the reported v4 metrics.
- `real_oracle_labels.csv`: complete 855-row publication oracle.
- `synthetic_scenarios.json`: deterministic authored fixtures used in tests.
