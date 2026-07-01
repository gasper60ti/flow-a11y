from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from dynamic.analyzers.dynamic_wcag_analyzer import DynamicWCAGAnalyzer
from dynamic.preprocessors.evidence_validator import DynamicEvidenceValidator
from dynamic.preprocessors.runtime_evidence_preprocessor import RuntimeEvidencePreprocessor
from dynamic.preprocessors.wcag_mapper import get_criterion
from dynamic.schemas.runtime_state import RuntimeState
from dynamic.schemas.runtime_trace import RuntimeTrace, RuntimeTraceMetadata
from dynamic.schemas.wcag_dynamic import RuntimeEvidenceBundle


class MissingKeyboardEvidencePreprocessor:
    def preprocess(self, trace: RuntimeTrace, criteria: list[str] | None = None) -> RuntimeEvidenceBundle:
        return RuntimeEvidenceBundle(
            scenario=trace.scenario,
            steps_count=len(trace.steps),
            probe_evidence={"keyboard_focus_probe": {"focus_sequence": []}},
            flat_evidence={"focus_sequence": []},
        )


class EmptyStatusMessagePreprocessor:
    def preprocess(self, trace: RuntimeTrace, criteria: list[str] | None = None) -> RuntimeEvidenceBundle:
        return RuntimeEvidenceBundle(
            scenario=trace.scenario,
            steps_count=len(trace.steps),
            probe_evidence={"status_message_probe": {"status_message_mutations": []}},
            flat_evidence={"status_message_mutations": []},
        )


class MissingProbePreprocessor:
    def preprocess(self, trace: RuntimeTrace, criteria: list[str] | None = None) -> RuntimeEvidenceBundle:
        return RuntimeEvidenceBundle(
            scenario=trace.scenario,
            steps_count=len(trace.steps),
            probe_evidence={},
            flat_evidence={},
        )


class EmptyMediaEvidencePreprocessor:
    def preprocess(self, trace: RuntimeTrace, criteria: list[str] | None = None) -> RuntimeEvidenceBundle:
        return RuntimeEvidenceBundle(
            scenario=trace.scenario,
            steps_count=len(trace.steps),
            probe_evidence={
                "media_probe": {
                    "media_elements": [],
                    "caption_tracks": [],
                    "transcript_indicators": [],
                    "background_audio_indicators": [],
                }
            },
            flat_evidence={
                "media_elements": [],
                "caption_tracks": [],
                "transcript_indicators": [],
                "background_audio_indicators": [],
            },
        )


class MissingScreenshotEvidencePreprocessor:
    def preprocess(self, trace: RuntimeTrace, criteria: list[str] | None = None) -> RuntimeEvidenceBundle:
        return RuntimeEvidenceBundle(
            scenario=trace.scenario,
            steps_count=len(trace.steps),
            probe_evidence={
                "keyboard_focus_probe": {
                    "focus_visible_trace": [{"step": 1, "xpath": "/html/body/button[1]", "has_visible_indicator": True}],
                    "focus_sequence": [{"step": 1, "xpath": "/html/body/button[1]"}],
                    "keyboard_events": [{"key": "Tab", "step": 1}],
                }
            },
            flat_evidence={
                "focus_visible_trace": [{"step": 1, "xpath": "/html/body/button[1]", "has_visible_indicator": True}],
                "focus_sequence": [{"step": 1, "xpath": "/html/body/button[1]"}],
                "keyboard_events": [{"key": "Tab", "step": 1}],
            },
        )


class StrongComputedFocusWithoutScreenshotPreprocessor:
    def preprocess(self, trace: RuntimeTrace, criteria: list[str] | None = None) -> RuntimeEvidenceBundle:
        rect = {"x": 10, "y": 10, "width": 80, "height": 32}
        evidence = {
            "focus_visible_trace": [
                {
                    "step": 1,
                    "xpath": "/html/body/button[1]",
                    "has_visible_indicator": False,
                    "bounding_client_rect": rect,
                    "source": "computed_style",
                    "quality": "computed",
                    "decisive": True,
                }
            ],
            "focus_sequence": [
                {
                    "step": 1,
                    "xpath": "/html/body/button[1]",
                    "tag": "button",
                    "bounding_client_rect": rect,
                }
            ],
            "keyboard_events": [{"key": "Tab", "type": "keydown", "step": 1}],
            "screenshot_paths": [],
        }
        return RuntimeEvidenceBundle(
            scenario=trace.scenario,
            steps_count=len(trace.steps),
            probe_evidence={"keyboard_focus_probe": evidence},
            flat_evidence=evidence,
        )


class SufficientFocusVisibleEvidencePreprocessor:
    def __init__(self, screenshot_path: Path) -> None:
        self.screenshot_path = screenshot_path

    def preprocess(self, trace: RuntimeTrace, criteria: list[str] | None = None) -> RuntimeEvidenceBundle:
        evidence = {
            "focus_visible_trace": [{"step": 1, "xpath": "/html/body/button[1]", "has_visible_indicator": True}],
            "focus_sequence": [{"step": 1, "xpath": "/html/body/button[1]"}],
            "keyboard_events": [{"key": "Tab", "step": 1}],
            "screenshot_paths": [str(self.screenshot_path)],
        }
        return RuntimeEvidenceBundle(
            scenario=trace.scenario,
            steps_count=len(trace.steps),
            probe_evidence={"keyboard_focus_probe": evidence},
            flat_evidence=evidence,
        )


class RaisingModelClient:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        self.calls += 1
        raise AssertionError("model should not be called")


class RecordingModelClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.model_name = "recording-test-model"

    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        self.calls.append(prompt)
        return json.dumps(
            {
                "criterion_id": "2.4.7",
                "status": "pass",
                "summary": "Visible focus evidence is sufficient.",
                "missing_evidence": [],
                "violations": [],
            },
            ensure_ascii=True,
        )


def _trace() -> RuntimeTrace:
    now = datetime.now(timezone.utc)
    state = RuntimeState(timestamp=now, url="https://example.com")
    return RuntimeTrace(
        scenario="validator",
        metadata=RuntimeTraceMetadata(start_time=now, end_time=now, duration_seconds=0.0),
        steps=[state],
    )


def test_validator_reports_missing_keys() -> None:
    validator = DynamicEvidenceValidator()
    criterion = get_criterion("2.4.7")
    missing = validator.missing_evidence(criterion, {"focus_visible_trace": []})
    assert missing == ["screenshot_paths"]


def test_missing_evidence_returns_incomplete() -> None:
    analyzer = DynamicWCAGAnalyzer(preprocessor=MissingKeyboardEvidencePreprocessor())
    report, _, _ = asyncio.run(analyzer.analyze(_trace(), criteria=["2.1.2"]))

    assert len(report.results) == 1
    result = report.results[0]
    assert result.criterion_id == "2.1.2"
    assert result.status.value == "incomplete"
    assert "keyboard_events" in result.missing_evidence


def test_missing_probe_output_returns_incomplete() -> None:
    analyzer = DynamicWCAGAnalyzer(preprocessor=MissingProbePreprocessor())
    report, _, _ = asyncio.run(analyzer.analyze(_trace(), criteria=["4.1.3"]))
    assert report.results[0].status.value == "incomplete"


def test_no_relevant_feature_returns_not_applicable() -> None:
    analyzer = DynamicWCAGAnalyzer(preprocessor=EmptyStatusMessagePreprocessor())
    report, _, _ = asyncio.run(analyzer.analyze(_trace(), criteria=["4.1.3"]))

    assert len(report.results) == 1
    result = report.results[0]
    assert result.criterion_id == "4.1.3"
    assert result.status.value == "not_applicable"


def test_media_not_applicable_gate_skips_model_call() -> None:
    model = RaisingModelClient()
    analyzer = DynamicWCAGAnalyzer(
        preprocessor=EmptyMediaEvidencePreprocessor(),
        model_client=model,
        judge_mode="gemini",
    )

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(), criteria=["1.2.2"]))

    result = report.results[0]
    assert result.status.value == "not_applicable"
    assert model.calls == 0
    assert artifacts["1.2.2"]["gate_decision"] == "not_applicable"
    assert artifacts["1.2.2"]["evidence_quality"]["applicable"] is False
    assert "prompt" not in artifacts["1.2.2"]


def test_focus_visible_missing_screenshot_gate_skips_model_call() -> None:
    model = RaisingModelClient()
    analyzer = DynamicWCAGAnalyzer(
        preprocessor=MissingScreenshotEvidencePreprocessor(),
        model_client=model,
        judge_mode="gemini",
    )

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(), criteria=["2.4.7"]))

    result = report.results[0]
    assert result.status.value == "incomplete"
    assert result.missing_evidence == ["screenshot_paths"]
    assert model.calls == 0
    assert artifacts["2.4.7"]["gate_decision"] == "incomplete"
    assert artifacts["2.4.7"]["evidence_quality"]["missing_or_unusable_evidence"] == ["screenshot_paths"]
    assert "prompt" not in artifacts["2.4.7"]


def test_focus_visible_sufficient_evidence_calls_model_in_gemini_mode(tmp_path: Path) -> None:
    screenshot = tmp_path / "focus.png"
    screenshot.write_bytes(b"png")
    model = RecordingModelClient()
    analyzer = DynamicWCAGAnalyzer(
        preprocessor=SufficientFocusVisibleEvidencePreprocessor(screenshot),
        model_client=model,
        judge_mode="gemini",
    )

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(), criteria=["2.4.7"]))

    result = report.results[0]
    assert result.status.value == "pass"
    assert result.requires_llm is True
    assert len(model.calls) == 1
    assert artifacts["2.4.7"]["gate_decision"] == "proceed"
    assert artifacts["2.4.7"]["evidence_quality"]["sufficient_for_judgment"] is True
    assert "prompt" in artifacts["2.4.7"]


def test_strong_computed_focus_without_screenshot_calls_model() -> None:
    model = RecordingModelClient()
    analyzer = DynamicWCAGAnalyzer(
        preprocessor=StrongComputedFocusWithoutScreenshotPreprocessor(),
        model_client=model,
        judge_mode="gemini",
    )

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(), criteria=["2.4.7"]))

    assert report.results[0].status.value == "pass"
    assert len(model.calls) == 1
    assert artifacts["2.4.7"]["gate_decision"] == "proceed"


def test_preprocessor_drops_only_leading_states_before_task_target() -> None:
    now = datetime.now(timezone.utc)
    trace = RuntimeTrace(
        scenario="Open https://wordpress.org and inspect the account flow.",
        metadata=RuntimeTraceMetadata(start_time=now, end_time=now, duration_seconds=0.0),
        steps=[
            RuntimeState(timestamp=now, url="https://uniluxembourg.sharepoint.com/login"),
            RuntimeState(timestamp=now, url="https://www.wordpress.org/"),
            RuntimeState(timestamp=now, url="https://login.wordpress.org/"),
        ],
    )

    steps = RuntimeEvidencePreprocessor()._analysis_steps(trace)

    assert [step.url for step in steps] == [
        "https://www.wordpress.org/",
        "https://login.wordpress.org/",
    ]
