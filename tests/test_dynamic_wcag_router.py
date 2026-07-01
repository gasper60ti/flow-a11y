from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from dynamic.analyzers.dynamic_wcag_analyzer import DynamicWCAGAnalyzer
from dynamic.analyzers.wcag_router import DynamicWCAGRouter
from dynamic.preprocessors.wcag_mapper import DYNAMIC_WCAG_REGISTRY
from dynamic.schemas.runtime_state import RuntimeState
from dynamic.schemas.runtime_trace import RuntimeTrace, RuntimeTraceMetadata


def _trace() -> RuntimeTrace:
    now = datetime.now(timezone.utc)
    state = RuntimeState(timestamp=now, url="https://example.com")
    return RuntimeTrace(
        scenario="router",
        metadata=RuntimeTraceMetadata(start_time=now, end_time=now, duration_seconds=0.0),
        steps=[state],
    )


def test_router_maps_probe_groups() -> None:
    router = DynamicWCAGRouter()
    assert router.probe_for("2.1.2") == "keyboard_focus_probe"
    assert "2.1.2" in router.criteria_for_probe("keyboard_focus_probe")


def test_router_resolve_supported_and_unsupported() -> None:
    router = DynamicWCAGRouter()
    supported, unsupported = router.resolve(["2.1.2", "9.9.9"])
    assert supported == ["2.1.2"]
    assert unsupported == ["9.9.9"]


def test_analyzer_runs_all_criteria_when_none_requested() -> None:
    analyzer = DynamicWCAGAnalyzer()
    report, _, _ = asyncio.run(analyzer.analyze(_trace(), criteria=None))
    assert len(report.results) == len(DYNAMIC_WCAG_REGISTRY.supported_ids())


def test_analyzer_runs_requested_subset() -> None:
    analyzer = DynamicWCAGAnalyzer()
    requested = ["2.1.2", "2.4.7"]
    report, _, _ = asyncio.run(analyzer.analyze(_trace(), criteria=requested))
    assert [item.criterion_id for item in report.results] == requested


def test_analyzer_reports_unsupported_criteria() -> None:
    analyzer = DynamicWCAGAnalyzer()
    report, _, _ = asyncio.run(analyzer.analyze(_trace(), criteria=["2.1.2", "99.1.1"]))
    assert [item.criterion_id for item in report.results] == ["2.1.2"]
    assert report.unsupported_criteria == ["99.1.1"]
