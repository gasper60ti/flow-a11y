# FlowA11y: Flow-Aware Accessibility Testing

FlowA11y evaluates accessibility barriers that emerge while a user completes an
interaction scenario. It executes a browser task, records runtime states,
constructs criterion-specific evidence, and produces auditable WCAG decisions.

This research artifact contains the dynamic implementation, the anonymized
19-scenario / 855-label fixture, deterministic tests, Baseline 2.0 and
Baseline 3.0, and a compact offline package that reproduces the paper's
repeated-run binary analysis.

## Offline reproduction

The reviewer-facing analysis requires no browser, network access, or Gemini key.
It uses the saved normalized tables under
`benchmarking/dynamic/publication_results/v5/`.

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r benchmarking/requirements-benchmark.txt
make verify
```

To execute only the publication notebook:

```bash
FLOWA11Y_PUBLICATION_OUTPUT_DIR=/tmp/flow-a11y-v5-analysis \
  python -m jupyter nbconvert --to notebook --execute \
  benchmarking/dynamic/publication_analysis.ipynb \
  --output /tmp/flow-a11y-publication-analysis.ipynb \
  --ExecutePreprocessor.timeout=180
```

The notebook writes regenerated CSVs and figures to a temporary directory by
default. Set `FLOWA11Y_PUBLICATION_INPUT_DIR` only when using a compatible copy
of the bundled `source_inputs` directory.

## Binary defect decisions

The reported primary frame has 765 rows: 17 successfully completed scenarios
by 45 criteria. The analysis maps `FAIL` to `TRIGGER` and maps `WARNING`,
`PASS`, `NOT_APPLICABLE`, and `INCOMPLETE` to `NOT_TRIGGER`.

`NOT_TRIGGER` means that the observed trace did not produce an automatic defect
report for that criterion. It does not establish accessibility compliance.
The original five-status source tables remain available in the compact package.

## Artifact layout

| Path | Contents |
| --- | --- |
| `benchmarking/dynamic/publication_results/v5/source_inputs/` | 32 saved input CSVs: the fixed V4 frame, full anonymized oracle, and ten repeated-run table sets. |
| `benchmarking/dynamic/publication_results/v5/derived/` | Recomputed binary rows, matrices, metrics, reason breakdowns, report, and figures. |
| `benchmarking/dynamic/publication_analysis.ipynb` | Binary-only offline analysis notebook. |
| `benchmarking/dynamic/publication_binary_analysis.py` | Script used by the notebook and verification workflow. |
| `benchmarking/dynamic/matched_trace_baseline.py` | Baseline 2.0 implementation. |
| `benchmarking/dynamic/matched_global_evidence_baseline.py` | Baseline 3.0 implementation. |

The raw browser traces, screenshots, model responses, caches, and live-site
outputs are deliberately excluded. They are not needed to reproduce the saved
binary results.

## Baselines

- **Naive baseline:** a separate generic browser-use audit. It is an
  unequal-input descriptive reference.
- **Baseline 2.0:** receives FlowA11y's completed scenario, criterion targets,
  and trace, but judges a bounded generic trace view without the structured
  evidence layers.
- **Baseline 3.0:** shares trace, targets, Gemini configuration, prompt
  templates, gate decisions, parser, and verifier; it replaces
  criterion-specific evidence selection with one global all-probes packet.

The exact source hashes, validation rules, and derived files are documented in
[`benchmarking/dynamic/publication_results/v5/README.md`](benchmarking/dynamic/publication_results/v5/README.md).

## Live execution

Live reruns require a Gemini API key, a Chrome-compatible browser,
`browser-use`, network access, and the public websites listed in the scenario
fixture. Browser actions, websites, network conditions, and model responses
can change, so live reruns are not expected to recreate the saved traces.

Use the commands in [`benchmarking/README.md`](benchmarking/README.md) to run
the naive comparison, Baseline 2.0, or Baseline 3.0.
