# Derived binary defect-decision report

This report derives binary decisions from saved five-status artifacts only. It does not rerun browser-use, Gemini, or any evaluation, and the original five-status artifacts remain unchanged.

The fixed evaluation frame contains 765 rows across 17 scenarios and 45 criteria per scenario. Every method/run is aligned to that same frame.

`TRIGGER` maps from `FAIL`. `NOT_TRIGGER` maps from `WARNING`, `PASS`, `NOT_APPLICABLE`, and `INCOMPLETE`. `NOT_TRIGGER` means no automatic defect report from the observed trace; it does not establish accessibility compliance.

| Cohort | Method | Runs | Precision | Recall | F1 | MCC | Balanced accuracy |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Original naive comparison | FlowA11y | 4 | 26.0% ± 2.6% | 69.4% ± 8.5% | 37.7% ± 3.7% | 38.7% ± 4.6% | 80.5% ± 4.1% |
| Original naive comparison | Naive baseline | 4 | 3.6% ± 7.1% | 0.8% ± 1.6% | 1.3% ± 2.6% | 0.2% ± 3.3% | 50.1% ± 0.8% |
| Baseline 2.0 matched trace | FlowA11y | 3 | 27.2% ± 0.4% | 64.5% ± 5.6% | 38.2% ± 1.0% | 38.2% ± 1.9% | 78.6% ± 2.5% |
| Baseline 2.0 matched trace | Baseline 2.0 | 3 | 19.7% ± 4.2% | 41.9% ± 8.5% | 26.8% ± 5.6% | 24.4% ± 6.4% | 67.4% ± 4.5% |
| Baseline 3.0 global-evidence ablation | FlowA11y | 3 | 25.7% ± 1.4% | 62.4% ± 3.7% | 36.4% ± 1.9% | 36.2% ± 2.3% | 77.4% ± 1.9% |
| Baseline 3.0 global-evidence ablation | Baseline 3.0 | 3 | 22.9% ± 3.2% | 60.2% ± 6.7% | 33.2% ± 4.3% | 33.0% ± 5.0% | 75.8% ± 3.6% |
