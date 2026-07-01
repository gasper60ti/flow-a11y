from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from benchmarking import run_benchmark


def test_mode_dispatches_dynamic_only(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    async def fake_dynamic(args):  # noqa: ANN001
        calls.append(f"dynamic:{args.mode}")
        return {"status": "dynamic", "results_dir": str(tmp_path / "dynamic")}

    monkeypatch.setattr(run_benchmark, "run_dynamic_mode", fake_dynamic)

    asyncio.run(run_benchmark.async_main(run_benchmark.parse_args(["--mode", "dynamic", "--dry-run"])))

    assert calls == ["dynamic:dynamic"]


def test_static_and_both_modes_are_disabled() -> None:
    for mode in ("static", "both"):
        with pytest.raises(SystemExit):
            run_benchmark.parse_args(["--mode", mode, "--dry-run"])


def test_dynamic_mode_parser_runs_without_network(tmp_path: Path) -> None:
    args = run_benchmark.parse_args(
        [
            "--mode",
            "dynamic",
            "--scenario-set",
            "fixture",
            "--dry-run",
            "--max-scenarios",
            "1",
            "--dynamic-output-dir",
            str(tmp_path / "dynamic"),
        ]
    )

    exit_code = asyncio.run(run_benchmark.async_main(args))

    assert exit_code == 0
    assert (tmp_path / "dynamic" / "tables" / "dynamic_case_records.csv").exists()


def test_static_benchmark_source_is_removed() -> None:
    app_root = Path(__file__).resolve().parents[1]

    assert not (app_root / "static").exists()
    assert not (app_root / "benchmarking" / "static").exists()
    assert not (app_root / "benchmarking" / "run_static_benchmark.py").exists()
