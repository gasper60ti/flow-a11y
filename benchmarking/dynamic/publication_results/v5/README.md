# V5 repeated-run binary artifact

This directory is the compact, offline-reproducible package for the repeated-run
binary defect-decision analysis.

## Contents

- `source_inputs/` contains exactly 32 saved CSV files: the fixed V4 scoring
audit, the complete anonymized oracle, and three normalized tables for each of
ten saved runs.
- `derived/` contains the binary row-level report, mapping checks, 2x2 matrices,
TP/FP/TN/FN counts, precision, recall, F1, MCC, specificity, balanced accuracy,
ordinary accuracy, retained `NOT_TRIGGER` reasons, and four figures.
  `binary_derivation_manifest.csv` maps every generated table, report, and
  figure to its transitive saved-input dependencies.
- `SHA256SUMS` covers every saved input and derived release file.
- `ARTIFACT_MANIFEST.md` describes the input contract and output inventory.

## Decision mapping

| Original status | Binary decision | `not_trigger_reason` for tool output |
| --- | --- | --- |
| `FAIL` | `TRIGGER` | |
| `WARNING` | `NOT_TRIGGER` | `weak_signal` |
| `PASS` | `NOT_TRIGGER` | `assessed_pass` |
| `NOT_APPLICABLE` | `NOT_TRIGGER` | `not_applicable` |
| `INCOMPLETE` | `NOT_TRIGGER` | `insufficient_evidence` |

The primary denominator is 765 rows for every method/run. `NOT_TRIGGER` means
no automatic defect report from the observed trace; it does not verify WCAG
conformance or website-wide accessibility.

## Reproduce

From the `a11y-agent` directory:

```bash
python3 -m benchmarking.dynamic.publication_binary_analysis \
  --output-dir /tmp/flow-a11y-v5-analysis
```

or execute `../../publication_analysis.ipynb`. Neither path uses a browser or
model API.
