from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from dynamic.analyzers.dynamic_wcag_analyzer import DynamicWCAGAnalyzer
from dynamic.browser_use_agent import BrowserUseAccessibilityAgent
from dynamic.schemas.probe_result import ProbeResult
from dynamic.schemas.runtime_state import RuntimeState
from dynamic.schemas.runtime_trace import RuntimeTrace, RuntimeTraceMetadata
from dynamic.schemas.wcag_dynamic import DynamicCriterionResult, DynamicStatus, DynamicWCAGReport, RuntimeEvidenceBundle


class ValidJsonModelClient:
    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        match = re.search(r"criterion\s+(\d+\.\d+\.\d+)", prompt)
        criterion_id = match.group(1) if match else "unknown"
        return json.dumps(
            {
                "criterion_id": criterion_id,
                "status": "pass",
                "summary": "Deterministic test response.",
                "missing_evidence": [],
                "violations": [],
            },
            ensure_ascii=True,
        )


class InvalidJsonModelClient:
    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        return "this is not valid JSON"


class WrongCriterionModelClient:
    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        return json.dumps(
            {
                "criterion_id": "9.9.9",
                "status": "fail",
                "summary": "Wrong criterion payload.",
                "missing_evidence": [],
                "violations": [],
            },
            ensure_ascii=True,
        )


class ApiErrorModelClient:
    model_name = "gemini-test"

    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        return json.dumps({"error": "503 Server Error: Service Unavailable"}, ensure_ascii=True)


class CountingDynamicModelClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.model_name = "counting-dynamic"

    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        self.calls.append(prompt)
        match = re.search(r"criterion\s+(\d+\.\d+\.\d+)", prompt)
        criterion_id = match.group(1) if match else "unknown"
        return json.dumps(
            {
                "criterion_id": criterion_id,
                "status": "pass",
                "summary": "Gemini judge test response.",
                "missing_evidence": [],
                "violations": [],
            },
            ensure_ascii=True,
        )


class PayloadModelClient:
    model_name = "payload-test-model"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.call_records: list[dict] = []

    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        match = re.search(r"criterion\s+(\d+\.\d+\.\d+)", prompt)
        self.call_records.append({"criterion": match.group(1) if match else ""})
        return json.dumps(self.payload, ensure_ascii=True)


class EmptyFocusEvidencePreprocessor:
    def preprocess(self, trace: RuntimeTrace, criteria: list[str] | None = None) -> RuntimeEvidenceBundle:
        evidence = {"focus_sequence": []}
        return RuntimeEvidenceBundle(
            scenario=trace.scenario,
            steps_count=len(trace.steps),
            probe_evidence={"keyboard_focus_probe": evidence},
            flat_evidence=evidence,
        )


class WeakPointerEvidencePreprocessor:
    def preprocess(self, trace: RuntimeTrace, criteria: list[str] | None = None) -> RuntimeEvidenceBundle:
        evidence = {
            "pointer_handlers": [
                {
                    "step": 1,
                    "url": "https://example.com",
                    "source": "dom_token",
                    "quality": "static_candidate",
                    "decisive": False,
                }
            ]
        }
        return RuntimeEvidenceBundle(
            scenario=trace.scenario,
            steps_count=len(trace.steps),
            probe_evidence={"pointer_gesture_probe": evidence},
            flat_evidence=evidence,
        )


class DragPointerEvidencePreprocessor:
    def preprocess(self, trace: RuntimeTrace, criteria: list[str] | None = None) -> RuntimeEvidenceBundle:
        evidence = {
            "pointer_handlers": [],
            "observed_pointer_events": [{"step": 1, "type": "dragstart"}],
            "dragging_indicators": [
                {
                    "step": 1,
                    "event_type": "dragstart",
                    "source": "observed_pointer_event",
                    "quality": "observed",
                    "decisive": True,
                    "has_keyboard_alternative": False,
                }
            ],
        }
        return RuntimeEvidenceBundle(
            scenario=trace.scenario,
            steps_count=len(trace.steps),
            probe_evidence={"pointer_gesture_probe": evidence},
            flat_evidence=evidence,
        )


class WeakStatusEvidencePreprocessor:
    def preprocess(self, trace: RuntimeTrace, criteria: list[str] | None = None) -> RuntimeEvidenceBundle:
        evidence = {
            "status_message_mutations": [
                {
                    "kind": "static_keyword",
                    "text": "status",
                    "quality": "weak_keyword",
                    "source": "text_keyword",
                    "decisive": False,
                }
            ]
        }
        return RuntimeEvidenceBundle(
            scenario=trace.scenario,
            steps_count=len(trace.steps),
            probe_evidence={"status_message_probe": evidence},
            flat_evidence=evidence,
        )


class PlayingMediaWithoutCaptionsPreprocessor:
    def preprocess(self, trace: RuntimeTrace, criteria: list[str] | None = None) -> RuntimeEvidenceBundle:
        evidence = {
            "media_elements": [
                {
                    "step": 1,
                    "tag": "video",
                    "paused": False,
                    "muted": False,
                    "audible": True,
                    "current_time": 4.0,
                    "current_time_delta": 1.0,
                    "visible": True,
                }
            ],
            "media_events": [{"step": 1, "type": "timeupdate"}],
            "caption_tracks": [],
            "transcript_indicators": [],
        }
        return RuntimeEvidenceBundle(
            scenario=trace.scenario,
            steps_count=len(trace.steps),
            probe_evidence={"media_probe": evidence},
            flat_evidence=evidence,
        )


class DirectContextualHelpFailurePreprocessor:
    def preprocess(self, trace: RuntimeTrace, criteria: list[str] | None = None) -> RuntimeEvidenceBundle:
        evidence = {
            "form_snapshots": [{"step": 2}],
            "help_for_forms": [],
            "submit_events": [
                {
                    "step": 2,
                    "type": "submit",
                    "target": {"tag": "form", "xpath": "/html/body/form[1]"},
                    "source": "runtime_event",
                    "quality": "observed",
                    "decisive": True,
                }
            ],
            "contextual_help_checks": [
                {
                    "step": 2,
                    "target": {"tag": "input", "xpath": "/html/body/form[1]/input[1]"},
                    "invalid_after_action": True,
                    "error_feedback_present": True,
                    "help_present": False,
                    "evaluation_complete": True,
                    "source": "runtime_form_snapshot",
                    "quality": "computed",
                    "decisive": True,
                }
            ],
            "contextual_help_failures": [
                {
                    "step": 2,
                    "target": {"tag": "input", "xpath": "/html/body/form[1]/input[1]"},
                    "help_requested": True,
                    "help_present": False,
                    "source": "runtime_help_interaction",
                    "quality": "observed",
                    "decisive": True,
                }
            ],
        }
        return RuntimeEvidenceBundle(
            scenario=trace.scenario,
            steps_count=len(trace.steps),
            probe_evidence={"forms_errors_auth_probe": evidence},
            flat_evidence=evidence,
        )


def _trace(tmp_path: Path) -> RuntimeTrace:
    screenshot = tmp_path / "step_1.png"
    screenshot.write_bytes(b"image")
    now = datetime.now(timezone.utc)

    state = RuntimeState(
        timestamp=now,
        url="https://example.com",
        dom_html='<div class="tooltip">hover</div><div onpointerdown="x"></div>',
        focused_element={
            "xpath": "/html/body/button[1]",
            "tag": "button",
            "attributes": {"style": "outline: 1px solid #000"},
        },
        keyboard_state={"events": [{"key": "Tab"}]},
        screenshot_path=str(screenshot),
        console_logs=[],
        dom_mutations=[{"kind": "status", "text": "Saved"}],
        media_state={"media": [{"tag": "video", "muted": False}]},
        timing_info={"deadline_seconds": 30},
        evaluator_result=ProbeResult(
            evaluator="gemini",
            model="gemini-test",
            captured_at=now,
            response_text=json.dumps(
                {
                    "observations": ["runtime observation"],
                    "issues": [
                        {
                            "title": "Runtime issue",
                            "summary": "Exploratory runtime issue",
                            "severity": "low",
                        }
                    ],
                },
                ensure_ascii=True,
            ),
            request_payload={"prompt": "test"},
            request_path=str(tmp_path / "req.json"),
            response_path=str(tmp_path / "resp.json"),
        ),
    )

    return RuntimeTrace(
        scenario="exports",
        metadata=RuntimeTraceMetadata(start_time=now, end_time=now, duration_seconds=0.0),
        steps=[state],
    )


def test_dynamic_wcag_export_writes_per_criterion_artifacts(tmp_path: Path) -> None:
    trace = _trace(tmp_path)
    analyzer = DynamicWCAGAnalyzer(model_client=ValidJsonModelClient())

    report, _, artifacts = asyncio.run(analyzer.analyze(trace, criteria=["1.4.13", "2.1.2"]))
    formal_path = analyzer.export(tmp_path, report, artifacts)

    assert formal_path.exists()
    payload = json.loads(formal_path.read_text(encoding="utf-8"))
    assert isinstance(payload.get("results", []), list)

    for result in payload.get("results", []):
        criterion_id = result["criterion_id"]
        criterion_dir = tmp_path / "criteria" / criterion_id.replace(".", "_")
        assert criterion_dir.exists(), f"Missing criterion directory for {criterion_id}"
        assert (criterion_dir / "result.json").exists()
        assert (criterion_dir / "evidence.json").exists()
        assert (criterion_dir / "gate.json").exists()

        artifact = artifacts[criterion_id]
        assert artifact["gate_decision"] in {"proceed", "warning", "incomplete", "not_applicable"}
        assert artifact["gate_reason"]
        assert isinstance(artifact["evidence_quality"], dict)
        assert isinstance(artifact["criterion_evidence_packet"], dict)
        gate_payload = json.loads((criterion_dir / "gate.json").read_text(encoding="utf-8"))
        assert gate_payload["gate_decision"] == artifact["gate_decision"]
        assert gate_payload["gate_reason"] == artifact["gate_reason"]
        assert gate_payload["evidence_quality"] == artifact["evidence_quality"]
        assert gate_payload["criterion_evidence_packet"] == artifact["criterion_evidence_packet"]

        if result.get("requires_llm", False):
            assert (criterion_dir / "prompt.txt").exists()
            assert (criterion_dir / "llm_response.json").exists()


def test_invalid_gemini_json_is_handled_safely(tmp_path: Path) -> None:
    trace = _trace(tmp_path)
    analyzer = DynamicWCAGAnalyzer(model_client=InvalidJsonModelClient())

    report, _, _ = asyncio.run(analyzer.analyze(trace, criteria=["1.4.13"]))
    assert len(report.results) == 1
    result = report.results[0]
    assert result.status == DynamicStatus.incomplete
    assert result.llm_error == "invalid_json"


def test_llm_payload_must_map_to_same_criterion(tmp_path: Path) -> None:
    trace = _trace(tmp_path)
    analyzer = DynamicWCAGAnalyzer(model_client=WrongCriterionModelClient())

    report, _, _ = asyncio.run(analyzer.analyze(trace, criteria=["1.4.13"]))
    assert report.results[0].status == DynamicStatus.incomplete
    assert report.results[0].llm_error == "invalid_json"


def test_gemini_api_error_payload_preserves_error_reason(tmp_path: Path) -> None:
    trace = _trace(tmp_path)
    analyzer = DynamicWCAGAnalyzer(model_client=ApiErrorModelClient())

    report, _, _ = asyncio.run(analyzer.analyze(trace, criteria=["1.4.13"]))

    result = report.results[0]
    assert result.status == DynamicStatus.incomplete
    assert result.llm_error == "503 Server Error: Service Unavailable"
    assert result.missing_evidence == ["llm_evaluation"]


def test_gemini_judge_calls_model_for_rule_based_focus_order(tmp_path: Path) -> None:
    trace = _trace(tmp_path)
    model = CountingDynamicModelClient()
    analyzer = DynamicWCAGAnalyzer(model_client=model, judge_mode="gemini")

    report, _, artifacts = asyncio.run(analyzer.analyze(trace, criteria=["2.4.3"]))

    assert len(model.calls) == 1
    assert report.results[0].criterion_id == "2.4.3"
    assert report.results[0].requires_llm is True
    assert "prompt" in artifacts["2.4.3"]
    assert "llm_response" in artifacts["2.4.3"]


def test_llm_fail_with_unresolved_evidence_ref_is_incomplete(tmp_path: Path) -> None:
    model = PayloadModelClient(
        {
            "criterion_id": "2.4.3",
            "status": "fail",
            "summary": "Focus order fails.",
            "missing_evidence": [],
            "violations": [
                {
                    "title": "Made-up evidence",
                    "summary": "The model cited evidence that does not exist.",
                    "evidence_refs": ["made_up[0]"],
                }
            ],
        }
    )
    analyzer = DynamicWCAGAnalyzer(model_client=model, judge_mode="gemini")

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(tmp_path), criteria=["2.4.3"]))

    result = report.results[0]
    verification = artifacts["2.4.3"]["llm_response"]["llm_verification"]
    assert result.status == DynamicStatus.incomplete
    assert result.llm_error == "invalid_evidence_refs"
    assert verification["valid"] is False
    assert verification["invalid_evidence_refs"] == ["made_up[0]"]
    assert model.call_records[0]["invalid_llm_result"] is True


def test_llm_fail_without_violations_is_incomplete(tmp_path: Path) -> None:
    model = PayloadModelClient(
        {
            "criterion_id": "2.4.3",
            "status": "fail",
            "summary": "Focus order fails.",
            "missing_evidence": [],
            "violations": [],
        }
    )
    analyzer = DynamicWCAGAnalyzer(model_client=model, judge_mode="gemini")

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(tmp_path), criteria=["2.4.3"]))

    result = report.results[0]
    verification = artifacts["2.4.3"]["llm_response"]["llm_verification"]
    assert result.status == DynamicStatus.incomplete
    assert result.llm_error == "invalid_evidence_refs"
    assert "missing_violations" in verification["issues"]


def test_llm_fail_with_empty_evidence_refs_is_incomplete(tmp_path: Path) -> None:
    model = PayloadModelClient(
        {
            "criterion_id": "2.4.3",
            "status": "fail",
            "summary": "Focus order fails.",
            "missing_evidence": [],
            "violations": [
                {
                    "title": "Missing refs",
                    "summary": "The violation does not cite evidence.",
                    "evidence_refs": [],
                }
            ],
        }
    )
    analyzer = DynamicWCAGAnalyzer(model_client=model, judge_mode="gemini")

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(tmp_path), criteria=["2.4.3"]))

    result = report.results[0]
    verification = artifacts["2.4.3"]["llm_response"]["llm_verification"]
    assert result.status == DynamicStatus.incomplete
    assert result.llm_error == "invalid_evidence_refs"
    assert "violations[0].missing_evidence_refs" in verification["issues"]


def test_llm_warning_with_valid_evidence_ref_remains_warning(tmp_path: Path) -> None:
    model = PayloadModelClient(
        {
            "criterion_id": "2.4.3",
            "status": "warning",
            "summary": "Focus order needs review.",
            "missing_evidence": [],
            "violations": [
                {
                    "title": "Focus sequence needs review",
                    "summary": "The warning cites a real focused element.",
                    "evidence_refs": ["focus_sequence[0].xpath"],
                }
            ],
        }
    )
    analyzer = DynamicWCAGAnalyzer(model_client=model, judge_mode="gemini")

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(tmp_path), criteria=["2.4.3"]))

    result = report.results[0]
    verification = artifacts["2.4.3"]["llm_response"]["llm_verification"]
    assert result.status == DynamicStatus.warning
    assert result.llm_error is None
    assert verification["valid"] is True
    assert model.call_records[0]["verifier_status"] == "valid"


def test_llm_packet_alias_is_normalized_to_canonical_evidence_ref(tmp_path: Path) -> None:
    model = PayloadModelClient(
        {
            "criterion_id": "2.4.3",
            "status": "warning",
            "summary": "Focus order needs review.",
            "missing_evidence": [],
            "violations": [
                {
                    "title": "Focus entry needs review",
                    "summary": "The model cited a compact packet field using its short alias.",
                    "evidence_refs": ["compact_runtime_facts.focus_entries[0]"],
                }
            ],
        }
    )
    analyzer = DynamicWCAGAnalyzer(model_client=model, judge_mode="gemini")

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(tmp_path), criteria=["2.4.3"]))

    result = report.results[0]
    llm_response = artifacts["2.4.3"]["llm_response"]
    assert result.status == DynamicStatus.warning
    assert result.llm_error is None
    assert result.violations[0].evidence_refs == [
        "criterion_evidence_packet.compact_runtime_facts.focus_entries[0]"
    ]
    assert llm_response["llm_verification"]["valid"] is True
    assert llm_response["evidence_ref_normalizations"] == [
        {
            "original": "compact_runtime_facts.focus_entries[0]",
            "canonical": "criterion_evidence_packet.compact_runtime_facts.focus_entries[0]",
        }
    ]


def test_llm_focus_body_alias_is_normalized_to_packet_field(tmp_path: Path) -> None:
    model = PayloadModelClient(
        {
            "criterion_id": "2.4.3",
            "status": "warning",
            "summary": "Focus order needs review.",
            "missing_evidence": [],
            "violations": [
                {
                    "title": "Focus entry needs review",
                    "summary": "The model cited the packet-only body/html alias.",
                    "evidence_refs": ["focus_visible_trace[0].is_body_html"],
                }
            ],
        }
    )
    analyzer = DynamicWCAGAnalyzer(model_client=model, judge_mode="gemini")

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(tmp_path), criteria=["2.4.3"]))

    result = report.results[0]
    assert result.llm_error is None
    assert result.violations[0].evidence_refs == [
        "criterion_evidence_packet.compact_runtime_facts.focus_entries[0].is_body_html"
    ]
    assert artifacts["2.4.3"]["llm_response"]["llm_verification"]["valid"] is True


def test_llm_drops_isolated_out_of_range_ref_when_valid_ref_remains(tmp_path: Path) -> None:
    model = PayloadModelClient(
        {
            "criterion_id": "2.4.3",
            "status": "warning",
            "summary": "Focus order needs review.",
            "missing_evidence": [],
            "violations": [
                {
                    "title": "Focus entry needs review",
                    "summary": "One valid citation remains after sanitization.",
                    "evidence_refs": ["focus_sequence[0].xpath", "focus_sequence[99]"],
                }
            ],
        }
    )
    analyzer = DynamicWCAGAnalyzer(model_client=model, judge_mode="gemini")

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(tmp_path), criteria=["2.4.3"]))

    result = report.results[0]
    assert result.llm_error is None
    assert result.violations[0].evidence_refs == ["focus_sequence[0].xpath"]
    assert artifacts["2.4.3"]["llm_response"]["llm_verification"]["valid"] is True
    assert {
        "original": "focus_sequence[99]",
        "canonical": "",
        "action": "dropped_unresolved",
    } in artifacts["2.4.3"]["llm_response"]["evidence_ref_normalizations"]


def test_llm_can_cite_explicit_empty_support_collection(tmp_path: Path) -> None:
    model = PayloadModelClient(
        {
            "criterion_id": "1.2.2",
            "status": "warning",
            "summary": "Playing media has no captured caption track.",
            "missing_evidence": [],
            "violations": [
                {
                    "title": "Caption support absent",
                    "summary": "The explicit empty caption collection is evidence of absence.",
                    "evidence_refs": ["media_elements[0]", "caption_tracks"],
                }
            ],
        }
    )
    analyzer = DynamicWCAGAnalyzer(
        preprocessor=PlayingMediaWithoutCaptionsPreprocessor(),
        model_client=model,
        judge_mode="gemini",
    )

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(tmp_path), criteria=["1.2.2"]))

    result = report.results[0]
    verification = artifacts["1.2.2"]["llm_response"]["llm_verification"]
    assert result.status == DynamicStatus.warning
    assert result.llm_error is None
    assert verification["valid"] is True


def test_help_fail_is_confirmed_only_with_action_tied_missing_help(tmp_path: Path) -> None:
    model = PayloadModelClient(
        {
            "criterion_id": "3.3.5",
            "status": "fail",
            "summary": "Contextual help is missing after validation.",
            "missing_evidence": [],
            "violations": [
                {
                    "title": "Missing contextual help",
                    "summary": "The invalid submitted control has no associated help.",
                    "evidence_refs": ["submit_events[0]", "contextual_help_failures[0]"],
                }
            ],
        }
    )
    analyzer = DynamicWCAGAnalyzer(
        preprocessor=DirectContextualHelpFailurePreprocessor(),
        model_client=model,
        judge_mode="gemini",
    )

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(tmp_path), criteria=["3.3.5"]))

    result = report.results[0]
    assert result.status == DynamicStatus.fail
    assert result.decision_level == "confirmed_fail"
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "form_contextual_help_missing_after_action"
    assert artifacts["3.3.5"]["llm_response"]["llm_verification"]["valid"] is True


def test_gemini_not_called_for_not_applicable_pointer_listener_only_packet(tmp_path: Path) -> None:
    model = PayloadModelClient(
        {
            "criterion_id": "2.5.1",
            "status": "fail",
            "summary": "Pointer gesture evidence fails.",
            "missing_evidence": [],
            "violations": [
                {
                    "title": "Pointer gesture candidate",
                    "summary": "The model cites weak but real pointer evidence.",
                    "evidence_refs": ["pointer_handlers[0]"],
                }
            ],
        }
    )
    analyzer = DynamicWCAGAnalyzer(
        preprocessor=WeakPointerEvidencePreprocessor(),
        model_client=model,
        judge_mode="gemini",
    )

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(tmp_path), criteria=["2.5.1"]))

    result = report.results[0]
    artifact = artifacts["2.5.1"]
    assert result.status == DynamicStatus.not_applicable
    assert result.llm_error is None
    assert result.decision_level == "not_applicable"
    assert result.evidence_packet is not None
    assert result.evidence_packet.applicability == "not_applicable"
    assert artifact["soft_evidence_quality"]["evidence_strength"] == "weak"
    assert "llm_response" not in artifact
    assert len(model.call_records) == 0


def test_gemini_receives_compact_criterion_evidence_packet(tmp_path: Path) -> None:
    model = PayloadModelClient(
        {
            "criterion_id": "2.5.1",
            "status": "pass",
            "summary": "Pointer gesture has an alternative.",
            "missing_evidence": [],
            "violations": [],
        }
    )
    analyzer = DynamicWCAGAnalyzer(
        preprocessor=DragPointerEvidencePreprocessor(),
        model_client=model,
        judge_mode="gemini",
    )

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(tmp_path), criteria=["2.5.1"]))

    assert report.results[0].status == DynamicStatus.pass_status
    assert len(model.call_records) == 1
    prompt = artifacts["2.5.1"]["prompt"]
    assert "criterion_evidence_packet" in prompt
    assert "observed_pointer_gesture_requirement" in prompt
    assert artifacts["2.5.1"]["criterion_evidence_packet"]["applicability"] == "applicable"


def test_weak_indirect_status_evidence_is_gated_before_llm(tmp_path: Path) -> None:
    model = PayloadModelClient(
        {
            "criterion_id": "4.1.3",
            "status": "warning",
            "summary": "Status message evidence is weak but reviewable.",
            "missing_evidence": [],
            "violations": [
                {
                    "title": "Weak status candidate",
                    "summary": "The model cites weak but real status evidence.",
                    "evidence_refs": ["status_message_mutations[0]"],
                }
            ],
        }
    )
    analyzer = DynamicWCAGAnalyzer(
        preprocessor=WeakStatusEvidencePreprocessor(),
        model_client=model,
        judge_mode="gemini",
    )

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(tmp_path), criteria=["4.1.3"]))

    result = report.results[0]
    artifact = artifacts["4.1.3"]
    assert result.status == DynamicStatus.incomplete
    assert result.llm_error is None
    assert result.decision_level == "insufficient_evidence"
    assert artifact["soft_evidence_quality"]["evidence_strength"] == "weak"
    assert "llm_response" not in artifact
    assert len(model.call_records) == 0


def test_llm_pass_with_valid_ref_to_strong_evidence_remains_pass(tmp_path: Path) -> None:
    model = PayloadModelClient(
        {
            "criterion_id": "2.4.3",
            "status": "pass",
            "summary": "Focus order passes.",
            "missing_evidence": [],
            "violations": [],
        }
    )
    analyzer = DynamicWCAGAnalyzer(model_client=model, judge_mode="gemini")

    report, _, artifacts = asyncio.run(analyzer.analyze(_trace(tmp_path), criteria=["2.4.3"]))

    result = report.results[0]
    artifact = artifacts["2.4.3"]
    verification = artifact["llm_response"]["llm_verification"]
    assert result.status == DynamicStatus.pass_status
    assert result.llm_error is None
    assert artifact["soft_evidence_quality"]["evidence_strength"] in {"strong", "moderate"}
    assert verification["valid"] is True


def test_formal_results_and_exploratory_observations_are_separate(tmp_path: Path) -> None:
    trace = _trace(tmp_path)
    now = datetime.now(timezone.utc)
    formal_report = DynamicWCAGReport(
        scenario="exports",
        generated_at=now,
        criteria_requested=["2.1.2"],
        results=[
            DynamicCriterionResult(
                criterion_id="2.1.2",
                criterion_name="No Keyboard Trap",
                status=DynamicStatus.pass_status,
                summary="Focus moved normally.",
                generated_at=now,
            )
        ],
    )

    agent = BrowserUseAccessibilityAgent(enable_formal_wcag=False)
    exploratory = agent._build_exploratory_dynamic_report(trace)
    findings_report = agent._build_dynamic_findings_report(trace, exploratory, formal_report)

    assert isinstance(findings_report.get("findings", []), list)
    assert isinstance(findings_report.get("formal_dynamic_wcag_results", []), list)
    assert isinstance(findings_report.get("exploratory_dynamic_observations", []), list)

    assert findings_report["formal_dynamic_wcag_results"][0]["criterion_id"] == "2.1.2"
    assert findings_report["exploratory_dynamic_observations"][0]["observations"] == ["runtime observation"]


def test_browser_use_agent_formal_analyzer_uses_gemini_judge_mode(tmp_path: Path) -> None:
    agent = BrowserUseAccessibilityAgent(api_key="test-key", enable_formal_wcag=True)

    analyzer = agent._build_formal_dynamic_analyzer(tmp_path)

    assert analyzer.judge_mode == "gemini"
    assert analyzer.model_client is not None


def test_browser_use_agent_can_skip_formal_wcag_analysis(tmp_path: Path) -> None:
    agent = BrowserUseAccessibilityAgent(enable_formal_wcag=False)
    trace = _trace(tmp_path)

    report = asyncio.run(agent._run_formal_dynamic_wcag_analysis(tmp_path, trace))

    assert report.results == []
    assert report.criteria_requested == []
    assert not (tmp_path / "formal_dynamic_wcag_results.json").exists()
