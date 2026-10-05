# Dynamic Benchmarking

This repository contains the dynamic WCAG benchmark and its reviewer-facing
binary publication analysis.

## Reproduce the saved analysis offline

The compact source inputs and generated outputs are under
`benchmarking/dynamic/publication_results/v5/`. Execute the notebook or script
without a browser or Gemini key:

```bash
python3 -m benchmarking.dynamic.publication_binary_analysis \
  --output-dir /tmp/flow-a11y-v5-analysis
```

```bash
FLOWA11Y_PUBLICATION_OUTPUT_DIR=/tmp/flow-a11y-v5-analysis \
  python3 -m jupyter nbconvert --to notebook --execute \
  benchmarking/dynamic/publication_analysis.ipynb \
  --output /tmp/flow-a11y-publication-analysis.ipynb \
  --ExecutePreprocessor.timeout=180
```

The analysis uses a fixed 765-row frame. `FAIL` maps to `TRIGGER`; `WARNING`,
`PASS`, `NOT_APPLICABLE`, and `INCOMPLETE` map to `NOT_TRIGGER`.
`NOT_TRIGGER` means no automatic defect report from the observed trace, not
verified accessibility compliance.

## Deterministic fixture check

```bash
python3 -m benchmarking.run_benchmark \
  --mode dynamic \
  --scenario-set fixture \
  --dry-run \
  --max-scenarios 2 \
  --dynamic-output-dir /tmp/flow-a11y-fixture
```

## Live real-scenario comparisons

All live commands require `GEMINI_API_KEY`, Chromium, `browser-use`, and
network access. Use the complete anonymized oracle fixture bundled with this
artifact.

### Naive generic audit

```bash
python3 -m benchmarking.dynamic.run_dynamic_benchmark \
  --scenario-set real \
  --scenario-file benchmarking/fixtures/dynamic/real_scenarios.json \
  --oracle-file benchmarking/fixtures/dynamic/real_oracle_labels.csv \
  --execution-mode browser-use \
  --browser-max-steps 12 \
  --baseline-max-steps 12 \
  --dynamic-judge gemini \
  --dynamic-criteria-scope all-prompts \
  --comparison-baseline naive-browser-use-global-audit \
  --gemini-model gemini-2.5-flash \
  --output-dir /tmp/flow-a11y-naive
```

This is a descriptive unequal-input reference: it launches its own generic
browser audit and does not receive FlowA11y's scenario task or criterion plan.

### Baseline 2.0: matched generic trace judge

```bash
python3 -m benchmarking.dynamic.run_dynamic_benchmark \
  --scenario-set real \
  --scenario-file benchmarking/fixtures/dynamic/real_scenarios.json \
  --oracle-file benchmarking/fixtures/dynamic/real_oracle_labels.csv \
  --execution-mode browser-use \
  --browser-max-steps 12 \
  --dynamic-judge gemini \
  --dynamic-criteria-scope all-prompts \
  --comparison-baseline baseline-2.0-matched-trace-generic-judge \
  --gemini-model gemini-2.5-flash \
  --output-dir /tmp/flow-a11y-baseline2
```

Baseline 2.0 reuses the trace just collected for FlowA11y. It receives the same
scenario task and criterion targets but judges a documented bounded generic
trace rendering without criterion-specific probes, evidence packets, gates, or
validation layers.

### Baseline 3.0: global-evidence ablation

```bash
python3 -m benchmarking.dynamic.run_dynamic_benchmark \
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

Baseline 3.0 shares the trace, criterion targets, Gemini configuration, prompt
templates, gate decisions, parser, and verifier. It gives every criterion the
same all-probes evidence packet instead of FlowA11y's criterion-specific packet.
