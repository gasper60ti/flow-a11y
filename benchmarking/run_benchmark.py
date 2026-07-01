"""Dynamic-only benchmark dispatcher."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from benchmarking.dynamic.run_dynamic_benchmark import DEFAULT_RESULTS_DIR as DYNAMIC_RESULTS_DIR
from benchmarking.dynamic.run_dynamic_benchmark import run_dynamic_benchmark

# Static and combined benchmark imports are intentionally disabled on the
# dynamic-only branch so this dispatcher stays importable after removing the
# static source tree.


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["dynamic"], required=True)
    parser.add_argument("--scenario-set", choices=["fixture", "real"], default="fixture")
    parser.add_argument("--scenario-file", type=Path, default=None)
    parser.add_argument("--scenario-id", action="append", default=[])
    parser.add_argument("--criteria", action="append", default=[])
    parser.add_argument("--max-scenarios", type=int, default=None)
    parser.add_argument("--browser-max-steps", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--gemini-model", default=None)
    parser.add_argument("--dynamic-judge", choices=["rules", "gemini", "hybrid"], default="rules")
    parser.add_argument("--dynamic-criteria-scope", choices=["scenario", "all-prompts"], default="scenario")
    parser.add_argument(
        "--comparison-baseline",
        choices=["naive-browser-use-global-audit", "none"],
        default="naive-browser-use-global-audit",
    )
    parser.add_argument("--baseline-max-steps", type=int, default=None)
    parser.add_argument("--execution-mode", choices=["fixture-trace", "browser-use"], default="fixture-trace")
    parser.add_argument("--dynamic-output-dir", type=Path, default=DYNAMIC_RESULTS_DIR)
    return parser.parse_args(argv)


async def async_main(args: argparse.Namespace) -> int:
    summary = await run_dynamic_mode(args)
    print("Dynamic benchmark finished.")
    print(f"- Results: {summary.get('results_dir', '')}")
    if summary.get("tables_dir"):
        print(f"- Tables: {summary['tables_dir']}")
    if "scenarios" in summary:
        print(f"- Scenarios: {summary['scenarios']} / {summary.get('available_scenarios', summary['scenarios'])}")
    print(f"- Run statuses: {', '.join(summary.get('run_statuses', []))}")
    print(f"- Comparison baselines: {', '.join(summary.get('comparison_baselines', [])) or 'none'}")
    return 0


async def run_dynamic_mode(args: argparse.Namespace) -> dict[str, Any]:
    dynamic_args = SimpleNamespace(
        scenario_set=args.scenario_set,
        scenario_file=args.scenario_file,
        scenario_id=args.scenario_id,
        criteria=args.criteria,
        max_scenarios=args.max_scenarios,
        browser_max_steps=args.browser_max_steps,
        dry_run=args.dry_run,
        gemini_model=args.gemini_model,
        dynamic_judge=args.dynamic_judge,
        dynamic_criteria_scope=args.dynamic_criteria_scope,
        comparison_baseline=args.comparison_baseline,
        baseline_max_steps=args.baseline_max_steps,
        execution_mode=args.execution_mode,
        output_dir=args.dynamic_output_dir,
    )
    return await run_dynamic_benchmark(dynamic_args)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
