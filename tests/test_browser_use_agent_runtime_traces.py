from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import dynamic.browser_use_agent as browser_agent_module
from dynamic.browser_use_agent import (
    BrowserUseAccessibilityAgent,
    load_env_from_repo,
    task_with_runtime_evidence_requirements,
)
from dynamic.schemas.runtime_trace import RuntimeTrace
from tests.test_support import results_root_for

OUTPUT_ROOT = results_root_for(__file__)


SCENARIOS: list[tuple[str, str]] = [
    (
        "google_search",
        "Go to google.com, search for 'accessibility testing', and wait 2 seconds.",
    ),
    (
        "youtube_keyboard",
        "Go to youtube.com, focus the search box, type 'accessibility', and stop.",
    ),
    (
        "ebay_navigation",
        "Go to ebay.com, search for 'wireless keyboard', and open the first result.",
    ),
]


def run_async(coro):
    return asyncio.run(coro)


def log(message: str) -> None:
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_trace_outputs(trace: RuntimeTrace, output_dir: Path) -> None:
    assert trace.steps, "Expected runtime steps to be recorded"
    assert output_dir.exists(), "Expected output directory to exist"

    has_screenshot = False
    has_dom = False
    has_a11y_tree = False
    has_successful_eval = False

    for step in trace.steps:
        if step.screenshot_path and Path(step.screenshot_path).exists():
            has_screenshot = True
        if step.dom_html:
            has_dom = True
        if step.accessibility_tree:
            has_a11y_tree = True
        if step.evaluator_result and step.evaluator_result.request_payload and not step.evaluator_result.error:
            has_successful_eval = True

        assert isinstance(step.console_logs, list)
        assert isinstance(step.dom_mutations, list)

    assert has_screenshot, "Expected at least one screenshot to be written"
    assert has_dom, "Expected DOM snapshots"
    assert has_a11y_tree, "Expected accessibility trees"
    assert has_successful_eval, "Expected at least one successful Gemini runtime evaluation"


class FakeHistory:
    def model_dump(self, mode: str = "json") -> dict:
        return {"history": [{"step": 1}]}

    def final_result(self) -> str:
        return "done"


class FakeBrowserSession:
    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self.start_calls = 0
        self.close_calls = 0
        self.navigated_to: list[str] = []

    async def start(self) -> None:
        self.start_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    async def navigate_to(self, url: str) -> None:
        self.navigated_to.append(url)

    async def _cdp_add_init_script(self, script: str) -> str:
        return "fake-init-script"

    async def must_get_current_page(self):
        return FakePage()

    async def get_current_page_url(self) -> str:
        return "https://example.test"

    async def get_current_page_title(self) -> str:
        return "Example"


class FakePage:
    async def evaluate(self, script: str):
        return {"installed": True}

    async def screenshot(self, format: str = "png"):
        return ""


class FakeAgent:
    def __init__(self, task, llm, browser_session, output_model_schema=None, **kwargs):
        self.task = task
        self.llm = llm
        self.browser_session = browser_session
        self.output_model_schema = output_model_schema
        self.state = SimpleNamespace(last_model_output=None, last_result=[])

    async def run(self, max_steps: int = 1, on_step_end=None, **kwargs):
        self.state.last_result = [{"is_done": False}]
        if on_step_end is not None:
            await on_step_end(self)
        return FakeHistory()


class StaticExtractor:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def extract(self, *args):
        output_dir = args[-2]
        step_number = args[-1]
        if "screenshot_path" in self.payload:
            path = output_dir / f"screenshot_step_{step_number}.png"
            path.write_bytes(b"png")
            return {"screenshot_path": str(path)}
        return dict(self.payload)


class EarlyTabKeyboardExtractor:
    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, *args):
        self.calls += 1
        events = [{"type": "keydown", "key": "Tab"}] if self.calls == 1 else []
        return {"keyboard_state": {"events": events, "event_count": len(events)}}


def test_run_scenario_captures_initial_state_before_first_action(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(browser_agent_module, "Agent", FakeAgent)
    monkeypatch.setattr(browser_agent_module, "BrowserSession", FakeBrowserSession)

    agent = BrowserUseAccessibilityAgent(
        output_root=tmp_path,
        llm=object(),
        enable_evaluator=False,
        enable_formal_wcag=False,
    )
    agent.dom_extractor = StaticExtractor({"dom_html": "<button>Start</button>"})
    agent.screenshot_extractor = StaticExtractor({"screenshot_path": ""})
    agent.focus_extractor = StaticExtractor(
        {
            "focused_element": {
                "tag": "button",
                "xpath": "/html/body/button[1]",
                "computed_style": {
                    "outline": "2px solid black",
                    "outlineWidth": "2px",
                    "outlineStyle": "solid",
                    "outlineColor": "rgb(0, 0, 0)",
                    "boxShadow": "none",
                    "border": "0px none rgb(0, 0, 0)",
                    "backgroundColor": "rgb(255, 255, 255)",
                    "color": "rgb(0, 0, 0)",
                },
                "bounding_client_rect": {"x": 10, "y": 20, "width": 80, "height": 30},
                "viewport": {"width": 1280, "height": 720, "deviceScaleFactor": 1},
                "active_element_text": "Start",
                "active_element_name": "Start",
                "visible_focus_candidate": True,
            }
        }
    )
    agent.keyboard_extractor = EarlyTabKeyboardExtractor()
    agent.console_extractor = StaticExtractor({"console_logs": []})
    agent.mutation_extractor = StaticExtractor({"dom_mutations": []})
    agent.timing_extractor = StaticExtractor({"timing_info": {}})
    agent.media_extractor = StaticExtractor({"media_state": {"media": []}})
    agent.accessibility_extractor = StaticExtractor({"accessibility_tree": {"role": {"value": "document"}}})
    agent.instrumentation_extractor = StaticExtractor({"runtime_instrumentation": {"pointer_events": []}})

    trace = run_async(agent.run_scenario("Press Tab then click Start.", scenario_id="early_tab", max_steps=1))

    assert len(trace.steps) == 2
    assert trace.steps[0].interaction_metadata["capture_phase"] == "initial"
    assert trace.steps[1].interaction_metadata["capture_phase"] == "post_action"
    assert trace.steps[0].keyboard_state["events"][0]["key"] == "Tab"
    assert trace.steps[1].keyboard_state["events"] == []
    assert (tmp_path / "early_tab" / "runtime_state_step_1.json").exists()


def test_run_scenario_navigates_to_task_target_before_initial_capture(monkeypatch, tmp_path: Path) -> None:
    session = FakeBrowserSession()
    monkeypatch.setattr(browser_agent_module, "Agent", FakeAgent)

    agent = BrowserUseAccessibilityAgent(
        output_root=tmp_path,
        llm=object(),
        browser_session=session,
        enable_evaluator=False,
        enable_formal_wcag=False,
    )
    agent.dom_extractor = StaticExtractor({"dom_html": "<main>Target</main>"})
    agent.screenshot_extractor = StaticExtractor({"screenshot_path": ""})
    agent.focus_extractor = StaticExtractor({"focused_element": {}})
    agent.keyboard_extractor = StaticExtractor({"keyboard_state": {"events": []}})
    agent.console_extractor = StaticExtractor({"console_logs": []})
    agent.mutation_extractor = StaticExtractor({"dom_mutations": []})
    agent.timing_extractor = StaticExtractor({"timing_info": {}})
    agent.media_extractor = StaticExtractor({"media_state": {"media": []}})
    agent.accessibility_extractor = StaticExtractor({"accessibility_tree": {"role": {"value": "document"}}})
    agent.instrumentation_extractor = StaticExtractor({"runtime_instrumentation": {}})

    trace = run_async(
        agent.run_scenario(
            "Open https://wordpress.org and inspect the page.",
            scenario_id="target_navigation",
            max_steps=1,
        )
    )

    assert session.navigated_to == ["https://wordpress.org"]
    assert trace.steps[0].interaction_metadata["capture_phase"] == "initial_target"


def test_runtime_task_requires_real_keyboard_and_form_actions() -> None:
    task = task_with_runtime_evidence_requirements(
        "Open https://example.com, submit the form, then use keyboard navigation and Escape."
    )

    assert "use actual keyboard input" in task
    assert "Perform the requested real form input or submit action" in task
    assert "do not substitute pointer clicks" in task
    assert "Never invent jQuery-only CSS selectors such as :contains(...)" in task


def test_browser_agent_builds_distinct_fallback_model(monkeypatch, tmp_path: Path) -> None:
    created: list[dict] = []

    class FakeChatGoogle:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(browser_agent_module, "ChatGoogle", FakeChatGoogle)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    agent = BrowserUseAccessibilityAgent(
        output_root=tmp_path,
        model_name="gemini-2.5-flash",
        fallback_model_name="gemini-2.5-flash-lite",
        llm_max_retries=3,
        fallback_llm_max_retries=8,
    )

    assert agent._build_llm() is not None
    assert agent._build_fallback_llm() is not None
    assert [(item["model"], item["max_retries"]) for item in created] == [
        ("gemini-2.5-flash", 3),
        ("gemini-2.5-flash-lite", 8),
    ]


def test_browser_use_agent_runtime_traces() -> None:
    load_env_from_repo()
    api_key = os.environ.get("GEMINI_API_KEY")
    assert api_key, "Set GEMINI_API_KEY in a11y-agent/.env or environment."

    log("Runtime accessibility benchmark: starting")

    scenarios_summary: dict[str, dict] = {}

    for scenario_id, scenario in SCENARIOS:
        log(f"Scenario: starting {scenario_id}")
        agent = BrowserUseAccessibilityAgent(output_root=OUTPUT_ROOT)
        trace = run_async(
            agent.run_scenario(
                scenario,
                scenario_id=scenario_id,
                max_steps=3,
                reset_output_dir=True,
                step_logger=log,
            )
        )

        trace_path = OUTPUT_ROOT / scenario_id / "runtime_trace.json"
        findings_path = OUTPUT_ROOT / scenario_id / "findings.json"
        formal_path = OUTPUT_ROOT / scenario_id / "formal_dynamic_wcag_results.json"
        exploratory_path = OUTPUT_ROOT / scenario_id / "exploratory_dynamic_observations.json"
        criteria_dir = OUTPUT_ROOT / scenario_id / "criteria"

        assert trace_path.exists(), "Expected runtime trace to be written"
        assert findings_path.exists(), "Expected dynamic findings report to be written"
        assert formal_path.exists(), "Expected formal dynamic WCAG results to be written"
        assert exploratory_path.exists(), "Expected exploratory dynamic observations to be written"
        assert criteria_dir.exists(), "Expected per-criterion dynamic evidence directory"

        validated = RuntimeTrace.model_validate(load_json(trace_path))
        findings_report = load_json(findings_path)
        formal_report = load_json(formal_path)
        assert trace.metadata.browser_use_history
        assert trace.metadata.duration_seconds is not None
        assert_trace_outputs(validated, OUTPUT_ROOT / scenario_id)
        assert isinstance(findings_report.get("findings", []), list)
        assert isinstance(formal_report.get("results", []), list)

        for result in formal_report.get("results", []):
            criterion_id = result["criterion_id"]
            criterion_output_dir = criteria_dir / criterion_id.replace(".", "_")

            assert criterion_output_dir.exists(), f"Expected evidence directory for {criterion_id}"
            assert (criterion_output_dir / "result.json").exists(), f"Missing result.json for {criterion_id}"
            assert (criterion_output_dir / "evidence.json").exists(), f"Missing evidence.json for {criterion_id}"

            if result.get("requires_llm", False):
                assert (criterion_output_dir / "prompt.txt").exists(), f"Missing prompt.txt for {criterion_id}"
                assert (criterion_output_dir / "llm_response.json").exists(), f"Missing llm_response.json for {criterion_id}"

            assert result["status"] in {
                "pass",
                "fail",
                "warning",
                "not_applicable",
                "incomplete",
            }

        scenarios_summary[scenario_id] = {
            "trace_path": str(trace_path),
            "findings_path": str(findings_path),
            "formal_dynamic_wcag_results_path": str(formal_path),
            "exploratory_dynamic_observations_path": str(exploratory_path),
            "duration_seconds": trace.metadata.duration_seconds,
            "findings_count": len(findings_report.get("findings", [])),
            "formal_results_count": len(formal_report.get("results", [])),
        }
        log(f"Scenario: finished {scenario_id} -> {trace_path}")

    summary_path = OUTPUT_ROOT / "findings_summary.json"
    summary_path.write_text(
        json.dumps({"scenarios": scenarios_summary}, indent=2, sort_keys=True, ensure_ascii=True),
        encoding="utf-8",
    )
    assert summary_path.exists(), "Expected aggregate dynamic findings summary to be written"
    log("Runtime accessibility benchmark: finished")
