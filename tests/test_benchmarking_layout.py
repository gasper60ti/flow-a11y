from __future__ import annotations

import json
from pathlib import Path


def test_benchmarking_dynamic_only_layout_is_documented() -> None:
    root = Path("benchmarking")
    readme = (root / "README.md").read_text(encoding="utf-8")

    assert (root / "dynamic").exists()
    assert f"{root}/dynamic/" in readme
    assert "benchmarking/dynamic/publication_results/v5/" in readme
    assert "--mode dynamic" in readme
    assert "--mode static" not in readme
    assert "--mode both" not in readme
    assert "python -m benchmarking.run_static_benchmark" not in readme


def test_dynamic_notebooks_exist_and_are_valid_json() -> None:
    notebooks = [Path("benchmarking/dynamic/publication_analysis.ipynb")]

    for notebook in notebooks:
        assert notebook.exists()
        payload = json.loads(notebook.read_text(encoding="utf-8"))
        assert payload["nbformat"] == 4

    assert not Path("benchmarking/dynamic/benchmark_comparison.ipynb").exists()
    assert not Path("benchmarking/dynamic/ablation.ipynb").exists()


def test_static_and_combined_benchmark_paths_are_removed() -> None:
    root = Path("benchmarking")

    assert not (root / "static").exists()
    assert not (root / "both").exists()
    assert not (root / "run_static_benchmark.py").exists()
