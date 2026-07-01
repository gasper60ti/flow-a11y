# Compact v4 Result Artifact

This directory contains the normalized result tables used for the paper's
reported metrics. It is intentionally limited to table-level reproduction.
The 2.6 GB raw browser traces, screenshots, DOM snapshots, prompts, and model
responses are not included.

## Contents

- `benchmark_summary.json`: sanitized run configuration and aggregate metadata.
- `tables/`: 31 normalized CSV tables emitted by the v4 benchmark runner.
- `SHA256SUMS`: SHA-256 digests for the summary and every table.

The `formal_report_path` and `runtime_trace_path` columns are retained as
provenance from the original run. Their referenced files are not present in
this compact artifact.

## Publication Denominators

- Scenarios attempted: 19
- Scenario-criterion rows attempted: 855
- Scenarios completed and scored: 17
- Primary scored rows: 765
- Supported dynamic WCAG criteria: 45

## Integrity Check

From this directory:

```bash
shasum -a 256 -c SHA256SUMS
```
