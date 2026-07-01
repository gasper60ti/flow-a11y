"""Compatibility entry point for the dynamic benchmark runner."""

from __future__ import annotations

from benchmarking.dynamic.run_dynamic_benchmark import async_main, main, parse_args, run_dynamic_benchmark

__all__ = ["async_main", "main", "parse_args", "run_dynamic_benchmark"]


if __name__ == "__main__":
    raise SystemExit(main())
