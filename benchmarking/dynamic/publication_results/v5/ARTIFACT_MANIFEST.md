# V5 artifact manifest

## Scope

The release preserves the saved evidence required to reproduce the binary
analysis of FlowA11y, the naive generic audit, Baseline 2.0, and Baseline 3.0.
It does not contain raw browser traces, screenshots, model responses, or live
website artifacts.

## Saved inputs

The compact package has 32 source CSV files:

- one V4 scoring audit defining the common 765-row frame;
- one complete anonymized 855-row oracle fixture used to validate that frame;
- three normalized tables for each of four original-naive runs, three Baseline
  2.0 runs, and three Baseline 3.0 runs.

`derived/binary_source_manifest.csv` records every use of those files, their
relative package paths, and SHA-256 digests. It has 42 rows because a saved
status table is recorded separately for the primary and comparator method that
consume it; there are 32 unique source files. `derived/binary_derivation_manifest.csv`
then maps every generated table, report, and figure to those saved inputs.

Some normalized source-table columns retain relative provenance references to
raw artifacts. The raw artifacts themselves are not included in this release.

## Reproduction contract

`publication_binary_analysis.py` asserts that every method/run has exactly 765
unique V4 keys, that all statuses map exactly once, that every confusion matrix
sums to 765, and that the saved native sparse labels agree with their source
status records where they are available.

All outputs are derived from saved tables. No browser-use execution, Gemini
request, or modification of the original five-status source artifacts occurs.
