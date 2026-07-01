"""Shared support for integration-style accessibility tests."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_RESULTS_ROOT = REPO_ROOT / "tests-results"


def results_root_for(test_file: str) -> Path:
    root = TEST_RESULTS_ROOT / Path(test_file).stem
    root.mkdir(parents=True, exist_ok=True)
    return root
