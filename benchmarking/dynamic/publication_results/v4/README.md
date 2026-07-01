# Saved Publication Tables

Run the bundled analysis from the `a11y-agent` directory:

```bash
python -m jupyter nbconvert \
  --to notebook \
  --execute benchmarking/dynamic/publication_analysis.ipynb \
  --output /tmp/flow-a11y-publication-analysis.ipynb \
  --ExecutePreprocessor.timeout=180
```

No browser, website access, or Gemini API key is required to reproduce the
reported tables and plots.

## Evaluation Scope

| Quantity | Value |
| --- | ---: |
| Public-web scenarios attempted | 19 |
| Dynamic WCAG criteria per scenario | 45 |
| Attempted scenario-criterion rows | 855 |
| Completed and scored scenarios | 17 |
| Primary scored rows | 765 |
| Rows excluded after task failure | 90 |

## Primary Results

| Metric | Result |
| --- | ---: |
| Exact five-way status accuracy (95% CI) | 349 / 765 (45.6%; 43.0-48.2%) |
| Fail precision (95% CI) | 12 / 29 (41.4%; 17.5-64.7%) |
| Fail recall (95% CI) | 12 / 31 (38.7%; 13.8-68.2%) |
| Fail-or-warning issue recall | 18 / 31 (58.1%) |
| Pass precision | 83 / 105 (79.0%) |
| Pass recall | 83 / 182 (45.6%) |
| Not-applicable accuracy | 252 / 517 (48.7%) |
| Incomplete rate | 304 / 765 (39.7%) |

## Baseline Comparison

| Metric | Flow-A11y | Naive browser-use audit |
| --- | ---: | ---: |
| Exact accuracy | 45.6% | 3.3% |
| Detected oracle failures | 12 / 31 | 0 / 31 |
| Fail precision | 41.4% | 0.0% |
| Fail recall | 38.7% | 0.0% |
| Incomplete rate | 39.7% | 94.5% |

## Criterion-Family Outcomes

| Probe family | Rows | Exact accuracy | Fail TP / Oracle fail |
| --- | ---: | ---: | ---: |
| Media | 170 | 100.0% | 0 / 0 |
| Keyboard and focus | 170 | 58.8% | 10 / 23 |
| Pointer and gesture | 85 | 55.3% | 0 / 0 |
| Status messages | 17 | 47.1% | 0 / 2 |
| Hover/focus-revealed content | 17 | 41.2% | 2 / 3 |
| Forms, errors, and authentication | 136 | 12.5% | 0 / 0 |
| Animation and flashing | 51 | 0.0% | 0 / 0 |
| Timing and interruption | 68 | 0.0% | 0 / 0 |
| Consistency and navigation | 51 | 0.0% | 0 / 3 |

The media rows are all `not_applicable`; their perfect exact accuracy should
not be interpreted as demonstrated media-analysis capability.

## Model-Call Validity

| Quantity | Value |
| --- | ---: |
| Formal Gemini calls | 203 |
| Valid verified responses | 201 |
| Invalid JSON responses | 2 |
| Unresolvable evidence references | 0 |
| Prompt tokens | 17,102,567 |
| Output tokens | 31,083 |
| Total tokens | 17,507,120 |

## Result Sources

- `tables/dynamic_quality_summary.csv`: primary metrics and confidence intervals.
- `tables/status_confusion.csv`: five-way status confusion counts.
- `tables/baseline_comparison.csv`: row-level primary and naive-baseline outcomes.
- `tables/criteria_support_matrix.csv`: criterion-to-probe mapping.
- `tables/model_calls.csv`: formal model-call usage and verification records.
- `tables/model_call_verification.csv`: valid and invalid response counts.
- `benchmark_summary.json`: denominators, run configuration, and aggregate metadata.

The historical pre-layer comparison in the paper uses a separate prior run
with 630 scored rows. It is not a controlled ablation against the 765-row
final run and is therefore not included among the primary v4 tables.
