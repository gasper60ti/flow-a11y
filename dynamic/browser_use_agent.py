from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

import requests


def _add_local_browser_use_source() -> Path | None:
    repo_root = Path(__file__).resolve().parents[2]
    local_source = repo_root / "browser-use"
    if (local_source / "browser_use").exists() and str(local_source) not in sys.path:
        sys.path.insert(0, str(local_source))
    return local_source if (local_source / "browser_use").exists() else None


_LOCAL_BROWSER_USE_SOURCE = _add_local_browser_use_source()

try:
    from browser_use import Agent, BrowserSession, ChatGoogle
    from browser_use.llm.base import BaseChatModel
except (ImportError, ModuleNotFoundError) as exc:
    Agent = None
    BrowserSession = None
    ChatGoogle = None
    _BROWSER_USE_IMPORT_ERROR: Exception | None = exc

    class BaseChatModel:  # type: ignore[no-redef]
        pass

else:
    _BROWSER_USE_IMPORT_ERROR = None

from dynamic.analyzers.dynamic_wcag_analyzer import DynamicWCAGAnalyzer
from dynamic.analyzers.gemini_client import GeminiCriterionModelClient
from dynamic.runtime_extractors.accessibility_runtime import AccessibilityRuntimeExtractor
from dynamic.runtime_extractors.console_runtime import ConsoleRuntimeExtractor
from dynamic.runtime_extractors.dom_runtime import DomRuntimeExtractor
from dynamic.runtime_extractors.focus_runtime import FocusRuntimeExtractor
from dynamic.runtime_extractors.instrumentation_runtime import (
    RUNTIME_INSTRUMENTATION_INIT_SOURCE,
    RuntimeInstrumentationExtractor,
)
from dynamic.runtime_extractors.keyboard_runtime import KeyboardRuntimeExtractor
from dynamic.runtime_extractors.media_runtime import MediaRuntimeExtractor
from dynamic.runtime_extractors.mutation_runtime import MutationRuntimeExtractor
from dynamic.runtime_extractors.screenshot_runtime import ScreenshotRuntimeExtractor
from dynamic.runtime_extractors.timing_runtime import TimingRuntimeExtractor
from dynamic.runtime_extractors.utils import ensure_dir, utc_now, write_json
from dynamic.schemas.probe_result import ProbeResult
from dynamic.schemas.runtime_state import RuntimeState
from dynamic.schemas.runtime_trace import RuntimeTrace, RuntimeTraceMetadata
from dynamic.schemas.wcag_dynamic import DynamicWCAGReport

DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parents[1] / "tests" / "experiments" / "runtime"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and os.environ.get(key) is None:
            os.environ[key] = value


def load_env_from_repo() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    load_env_file(repo_root / ".env")


def normalize_model_name(model_name: str) -> str:
    return model_name.replace("models/", "")


def sanitize_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "scenario"


def first_http_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s\"'<>]+", str(text or ""))
    return match.group(0).rstrip(".,);]") if match else None


def task_with_runtime_evidence_requirements(task: str) -> str:
    """Require real interactions when a scenario explicitly asks for them."""

    task = str(task or "").strip()
    lowered = task.lower()
    requirements: list[str] = [
        "Target elements through the browser's visible interactive-element index or accessible role/name. "
        "Never invent jQuery-only CSS selectors such as :contains(...); if one locator strategy fails, "
        "inspect the current page and switch strategy."
    ]
    if any(token in lowered for token in ("keyboard", " tab", "shift+tab", "escape")):
        requirements.append(
            "For every requested keyboard step, use actual keyboard input (for example Tab, Shift+Tab, "
            "arrow keys, Enter, Space, or Escape); do not substitute pointer clicks."
        )
    if any(token in lowered for token in ("submit", "required field", "form with", "error or status feedback")):
        requirements.append(
            "Perform the requested real form input or submit action and wait for its resulting validation "
            "or status feedback before declaring the task complete."
        )
    return task + "\n\nRuntime execution requirements:\n- " + "\n- ".join(requirements)


def _parse_json_payload(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                payload = json.loads(text[start : end + 1])
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                pass
    return {}


class GeminiRuntimeEvaluator:
    def __init__(
        self,
        api_key: str,
        model_name: str,
        cache_dir: Path | None = None,
        max_retries: int = 3,
        base_delay_s: float = 1.0,
        max_delay_s: float = 20.0,
    ) -> None:
        self.api_key = api_key
        self.model_name = normalize_model_name(model_name)
        self.cache_dir = cache_dir
        self.max_retries = max_retries
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self.errors: list[dict[str, str]] = []

        if self.cache_dir:
            ensure_dir(self.cache_dir)

    async def evaluate(
        self,
        state: RuntimeState,
        output_dir: Path,
        step_number: int,
    ) -> ProbeResult:
        prompt = self._build_prompt(state)
        payload = self._build_payload(prompt)

        request_path = output_dir / f"gemini_request_step_{step_number}.json"
        write_json(request_path, payload)

        response_text, error = await asyncio.to_thread(self._call_api, payload)
        parsed_payload = _parse_json_payload(response_text)

        response_path = output_dir / f"gemini_response_step_{step_number}.json"
        write_json(
            response_path,
            {
                "evaluator": "gemini",
                "model": self.model_name,
                "error": error,
                "raw_response_text": response_text,
                "parsed_response": parsed_payload,
            },
        )

        return ProbeResult(
            evaluator="gemini",
            model=self.model_name,
            captured_at=utc_now(),
            request_payload=payload,
            response_text=response_text,
            error=error,
            request_path=str(request_path),
            response_path=str(response_path),
        )

    def _build_prompt(self, state: RuntimeState) -> str:
        payload = state.model_dump(mode="json")
        dom_html = payload.get("dom_html")
        if isinstance(dom_html, str) and len(dom_html) > 12000:
            payload["dom_html"] = dom_html[:12000] + "\n<!-- truncated -->"
            payload["dom_html_truncated"] = True

        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True)
        return (
            "You are an accessibility analyst reviewing runtime evidence from a browser session. "
            "Summarize potential issues or observations in JSON with keys: observations (list of strings), "
            "issues (list of objects with title, summary, severity).\n\n"
            f"Runtime evidence:\n{serialized}"
        )

    def _build_payload(self, prompt: str) -> dict[str, Any]:
        return {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.0, "topP": 1.0, "maxOutputTokens": 2048},
        }

    def _cache_key(self, payload: dict[str, Any]) -> str:
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256((self.model_name + serialized).encode("utf-8")).hexdigest()

    def _call_api(self, payload: dict[str, Any]) -> tuple[str, str | None]:
        if self.cache_dir:
            cache_key = self._cache_key(payload)
            cache_path = self.cache_dir / f"{cache_key}.json"
            if cache_path.exists():
                return cache_path.read_text(encoding="utf-8"), None

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"
        last_exc: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(url, params={"key": self.api_key}, json=payload, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                text = self._extract_text(data)
                if self.cache_dir:
                    cache_key = self._cache_key(payload)
                    cache_path = self.cache_dir / f"{cache_key}.json"
                    cache_path.write_text(text, encoding="utf-8")
                return text, None
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries and self._is_retryable(exc):
                    wait_s = min(self.base_delay_s * (2**attempt), self.max_delay_s)
                    time.sleep(wait_s)
                    continue
                error_text = self._redact(str(exc))
                self.errors.append({"error": error_text})
                return json.dumps({"error": error_text}, ensure_ascii=True), error_text

        error_text = self._redact(str(last_exc)) if last_exc else "Unknown error"
        self.errors.append({"error": error_text})
        return json.dumps({"error": error_text}, ensure_ascii=True), error_text

    def _extract_text(self, data: dict[str, Any]) -> str:
        candidates = data.get("candidates", [])
        if not candidates:
            return json.dumps(data, ensure_ascii=True)
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts)
        return text or json.dumps(data, ensure_ascii=True)

    def _redact(self, text: str) -> str:
        if not text:
            return text
        redacted = text.replace(self.api_key, "[REDACTED]")
        redacted = re.sub(r"key=([^&\s]+)", "key=[REDACTED]", redacted)
        return redacted

    def _is_retryable(self, exc: Exception) -> bool:
        if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
            return True
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            return exc.response.status_code in {429, 500, 502, 503, 504}
        return False


class BrowserUseAccessibilityAgent:
    def __init__(
        self,
        output_root: Path | None = None,
        llm: BaseChatModel | None = None,
        fallback_llm: BaseChatModel | None = None,
        browser_session: BrowserSession | None = None,
        evaluator: GeminiRuntimeEvaluator | None = None,
        formal_analyzer: DynamicWCAGAnalyzer | None = None,
        model_name: str | None = None,
        fallback_model_name: str | None = None,
        api_key: str | None = None,
        enable_evaluator: bool = True,
        enable_formal_wcag: bool = True,
        llm_max_retries: int | None = None,
        fallback_llm_max_retries: int = 8,
        llm_retry_base_s: float | None = None,
        llm_retry_max_s: float | None = None,
        agent_max_failures: int = 8,
        agent_llm_timeout_s: int = 120,
        agent_step_timeout_s: int = 240,
    ) -> None:
        self.output_root = output_root or DEFAULT_OUTPUT_ROOT
        self.llm = llm
        self.fallback_llm = fallback_llm
        self.browser_session = browser_session
        self.model_name = model_name
        self.fallback_model_name = fallback_model_name
        self.api_key = api_key
        self.enable_evaluator = enable_evaluator
        self.enable_formal_wcag = enable_formal_wcag
        self.llm_max_retries = llm_max_retries
        self.fallback_llm_max_retries = max(0, int(fallback_llm_max_retries))
        self.llm_retry_base_s = llm_retry_base_s
        self.llm_retry_max_s = llm_retry_max_s
        self.agent_max_failures = max(1, int(agent_max_failures))
        self.agent_llm_timeout_s = max(1, int(agent_llm_timeout_s))
        self.agent_step_timeout_s = max(1, int(agent_step_timeout_s))

        self.dom_extractor = DomRuntimeExtractor()
        self.screenshot_extractor = ScreenshotRuntimeExtractor()
        self.focus_extractor = FocusRuntimeExtractor()
        self.keyboard_extractor = KeyboardRuntimeExtractor()
        self.console_extractor = ConsoleRuntimeExtractor()
        self.mutation_extractor = MutationRuntimeExtractor()
        self.timing_extractor = TimingRuntimeExtractor()
        self.media_extractor = MediaRuntimeExtractor()
        self.accessibility_extractor = AccessibilityRuntimeExtractor()
        self.instrumentation_extractor = RuntimeInstrumentationExtractor()

        self.evaluator = evaluator
        self.formal_analyzer = formal_analyzer
        self._states: list[RuntimeState] = []
        self._instrumentation_script_id: str | None = None

    async def _default_step_logger(self, message: str) -> None:
        print(message, flush=True)

    async def _emit_step_log(self, step_logger: Callable[[str], Any], message: str) -> None:
        result = step_logger(message)
        if inspect.isawaitable(result):
            await result

    async def run_scenario(
        self,
        scenario: str,
        scenario_id: str | None = None,
        max_steps: int = 8,
        reset_output_dir: bool = True,
        step_logger: Callable[[str], Any] | None = None,
        output_model_schema: type[Any] | None = None,
    ) -> RuntimeTrace:
        output_dir = self.prepare_output_dir(scenario_id or sanitize_slug(scenario), reset=reset_output_dir)
        self._states = []

        if Agent is None or BrowserSession is None:
            source_hint = f" Local source checked: {_LOCAL_BROWSER_USE_SOURCE}" if _LOCAL_BROWSER_USE_SOURCE else ""
            raise ImportError(
                "browser_use is required to run live browser scenarios."
                " Install it with `python -m pip install -e ../browser-use` from a11y-agent."
                f"{source_hint}"
            ) from _BROWSER_USE_IMPORT_ERROR

        llm = self.llm or self._build_llm()
        fallback_llm = self.fallback_llm
        if fallback_llm is None and self.llm is None:
            fallback_llm = self._build_fallback_llm()
        browser_session = self.browser_session or BrowserSession(headless=True)
        owns_session = self.browser_session is None
        step_logger = step_logger or self._default_step_logger

        start_time = utc_now()
        history = None

        await self._emit_step_log(step_logger, f"[runtime] Starting scenario {scenario_id or sanitize_slug(scenario)}")

        try:
            agent = Agent(
                task=task_with_runtime_evidence_requirements(scenario),
                llm=llm,
                fallback_llm=fallback_llm,
                browser_session=browser_session,
                output_model_schema=output_model_schema,
                max_failures=self.agent_max_failures,
                llm_timeout=self.agent_llm_timeout_s,
                step_timeout=self.agent_step_timeout_s,
            )
            await self._install_pre_run_instrumentation(browser_session)
            target_url = first_http_url(scenario)
            if target_url:
                await self._emit_step_log(
                    step_logger,
                    f"[runtime] Navigating to task target before initial capture: {target_url}",
                )
                await browser_session.navigate_to(target_url)
                page = await browser_session.must_get_current_page()
                await self._install_runtime_instrumentation_on_page(page)
            await self._emit_step_log(
                step_logger,
                f"[runtime] Capturing initial state for {scenario_id or sanitize_slug(scenario)}",
            )
            initial_state = await self.capture_runtime_state(
                agent,
                output_dir,
                len(self._states) + 1,
                capture_phase="initial_target" if target_url else "initial",
            )
            self._states.append(initial_state)

            async def on_step_end(agent_instance: Agent) -> None:
                step_number = len(self._states) + 1
                await self._emit_step_log(
                    step_logger,
                    f"[runtime] Capturing step {step_number} for {scenario_id or sanitize_slug(scenario)}",
                )
                state = await self.capture_runtime_state(agent_instance, output_dir, step_number)
                self._states.append(state)
                action_name = None
                if state.current_action:
                    action_name = state.current_action.get("action") or state.current_action.get("next_goal")
                await self._emit_step_log(
                    step_logger,
                    "[runtime] Step "
                    f"{step_number} saved url={state.url} title={state.title or ''} "
                    f"action={action_name or 'n/a'} dom={bool(state.dom_html)} ax={bool(state.accessibility_tree)} "
                    f"screenshot={bool(state.screenshot_path)}",
                )

            history = await agent.run(max_steps=max_steps, on_step_end=on_step_end)
            self._save_browser_use_history_artifacts(output_dir, history, output_model_schema)
        finally:
            if owns_session:
                await browser_session.close()

        end_time = utc_now()
        duration = (end_time - start_time).total_seconds()

        metadata = RuntimeTraceMetadata(
            start_time=start_time,
            end_time=end_time,
            duration_seconds=duration,
            browser_use_history=history.model_dump().get("history", []) if history else [],
            artifacts_dir=str(output_dir),
        )
        trace = RuntimeTrace(scenario=scenario, metadata=metadata, steps=list(self._states))
        await self.save_runtime_artifacts(output_dir, trace)
        await self._emit_step_log(
            step_logger,
            f"[runtime] Finished scenario {scenario_id or sanitize_slug(scenario)} -> {output_dir}",
        )
        return trace

    async def capture_runtime_state(
        self,
        agent_instance: Agent,
        output_dir: Path,
        step_number: int,
        capture_phase: str = "post_action",
    ) -> RuntimeState:
        timestamp = utc_now()
        page = await agent_instance.browser_session.must_get_current_page()
        instrumentation_install = await self._install_runtime_instrumentation_on_page(page)
        url = await agent_instance.browser_session.get_current_page_url()
        title = await agent_instance.browser_session.get_current_page_title()

        current_action = None
        if agent_instance.state.last_model_output is not None:
            current_action = agent_instance.state.last_model_output.model_dump(exclude_none=True, mode="json")

        extraction_errors: dict[str, str] = {}

        async def safe_extract(name: str, coro):
            try:
                return await coro
            except Exception as exc:
                extraction_errors[name] = str(exc)
                return {}

        dom_data = await safe_extract("dom", self.dom_extractor.extract(page, output_dir, step_number))
        screenshot_data = await safe_extract(
            "screenshot", self.screenshot_extractor.extract(page, output_dir, step_number)
        )
        focus_data = await safe_extract("focus", self.focus_extractor.extract(page, output_dir, step_number))
        keyboard_data = await safe_extract(
            "keyboard", self.keyboard_extractor.extract(page, output_dir, step_number)
        )
        console_data = await safe_extract(
            "console", self.console_extractor.extract(page, output_dir, step_number)
        )
        mutation_data = await safe_extract(
            "mutations", self.mutation_extractor.extract(page, output_dir, step_number)
        )
        timing_data = await safe_extract(
            "timing", self.timing_extractor.extract(page, output_dir, step_number)
        )
        media_data = await safe_extract("media", self.media_extractor.extract(page, output_dir, step_number))
        runtime_instrumentation_data = await safe_extract(
            "runtime_instrumentation",
            self.instrumentation_extractor.extract(page, output_dir, step_number),
        )
        accessibility_data = await safe_extract(
            "accessibility",
            self.accessibility_extractor.extract(
                agent_instance.browser_session,
                page,
                output_dir,
                step_number,
            ),
        )

        interaction_metadata = {
            "action_results": self._serialize_action_results(agent_instance.state.last_result),
            "capture_phase": capture_phase,
            "instrumentation_install": instrumentation_install,
            "runtime_instrumentation": runtime_instrumentation_data.get("runtime_instrumentation", {}),
        }
        if extraction_errors:
            interaction_metadata["extraction_errors"] = extraction_errors

        state = RuntimeState(
            timestamp=timestamp,
            url=url,
            title=title,
            current_action=current_action,
            dom_html=dom_data.get("dom_html"),
            accessibility_tree=accessibility_data.get("accessibility_tree"),
            focused_element=focus_data.get("focused_element"),
            keyboard_state=keyboard_data.get("keyboard_state"),
            console_logs=console_data.get("console_logs") or [],
            screenshot_path=screenshot_data.get("screenshot_path"),
            dom_mutations=mutation_data.get("dom_mutations") or [],
            timing_info=timing_data.get("timing_info"),
            media_state=media_data.get("media_state"),
            interaction_metadata=interaction_metadata,
        )

        if self.enable_evaluator:
            state.evaluator_result = await self.evaluate_dynamic_criteria(state, output_dir, step_number)

        write_json(output_dir / f"runtime_state_step_{step_number}.json", state.model_dump(mode="json"))
        return state

    async def _install_pre_run_instrumentation(self, browser_session: BrowserSession) -> dict[str, Any]:
        await browser_session.start()
        init_status: dict[str, Any] = {}
        if self._instrumentation_script_id is None and hasattr(browser_session, "_cdp_add_init_script"):
            try:
                self._instrumentation_script_id = await browser_session._cdp_add_init_script(
                    RUNTIME_INSTRUMENTATION_INIT_SOURCE
                )
                init_status["init_script"] = "installed"
                init_status["init_script_id"] = self._instrumentation_script_id
            except Exception as exc:  # noqa: BLE001
                init_status["init_script"] = "failed"
                init_status["init_script_error"] = str(exc)
        try:
            page = await browser_session.must_get_current_page()
            init_status["current_page"] = await self._install_runtime_instrumentation_on_page(page)
        except Exception as exc:  # noqa: BLE001
            init_status["current_page"] = {"installed": False, "error": str(exc)}
        return init_status

    async def _install_runtime_instrumentation_on_page(self, page: Any) -> dict[str, Any]:
        try:
            return await self.instrumentation_extractor.install(page)
        except Exception as exc:  # noqa: BLE001
            return {"installed": False, "error": str(exc)}

    async def evaluate_dynamic_criteria(
        self,
        state: RuntimeState,
        output_dir: Path,
        step_number: int,
    ) -> ProbeResult:
        evaluator = self.evaluator or self._build_evaluator(output_dir)
        return await evaluator.evaluate(state, output_dir, step_number)

    def export_trace(self, trace: RuntimeTrace, output_dir: Path) -> Path:
        trace_path = output_dir / "runtime_trace.json"
        write_json(trace_path, trace.model_dump(mode="json"))
        return trace_path

    async def save_runtime_artifacts(self, output_dir: Path, trace: RuntimeTrace) -> None:
        self.export_trace(trace, output_dir)
        exploratory_report = self._build_exploratory_dynamic_report(trace)
        write_json(output_dir / "exploratory_dynamic_observations.json", exploratory_report)

        formal_report = await self._run_formal_dynamic_wcag_analysis(output_dir, trace)
        dynamic_report = self._build_dynamic_findings_report(trace, exploratory_report, formal_report)
        write_json(output_dir / "findings.json", dynamic_report)

    async def _run_formal_dynamic_wcag_analysis(self, output_dir: Path, trace: RuntimeTrace) -> DynamicWCAGReport:
        if not self.enable_formal_wcag:
            return DynamicWCAGReport(
                scenario=trace.scenario,
                generated_at=utc_now(),
                criteria_requested=[],
                results=[],
                unsupported_criteria=[],
            )
        analyzer = self.formal_analyzer or self._build_formal_dynamic_analyzer(output_dir)
        report, _, artifacts = await analyzer.analyze(trace, criteria=None)
        analyzer.export(output_dir, report, artifacts)
        return report

    def _build_exploratory_dynamic_report(self, trace: RuntimeTrace) -> dict[str, Any]:
        steps: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []

        for index, step in enumerate(trace.steps, start=1):
            probe = step.evaluator_result
            parsed_response: dict[str, Any] = {}
            if probe and probe.response_text:
                parsed_response = _parse_json_payload(probe.response_text)

            observations = parsed_response.get("observations", [])
            if not isinstance(observations, list):
                observations = []

            issues = parsed_response.get("issues", [])
            if not isinstance(issues, list):
                issues = []

            step_summary = {
                "step": index,
                "url": step.url,
                "title": step.title,
                "action": (step.current_action or {}).get("action") if step.current_action else None,
                "observations": observations,
                "issues_count": len(issues),
                "error": probe.error if probe else None,
                "request_path": probe.request_path if probe else None,
                "response_path": probe.response_path if probe else None,
            }
            steps.append(step_summary)

            for issue_index, issue in enumerate(issues, start=1):
                if not isinstance(issue, dict):
                    continue
                title = str(issue.get("title", "Accessibility runtime issue")).strip() or "Accessibility runtime issue"
                summary = str(issue.get("summary", "")).strip() or "Potential accessibility issue detected."
                severity = str(issue.get("severity", "medium")).strip().lower() or "medium"
                finding_id_source = f"{trace.scenario}:{index}:{issue_index}:{title}:{summary}".encode("utf-8")
                finding_id = hashlib.sha256(finding_id_source).hexdigest()[:12]
                findings.append(
                    {
                        "id": finding_id,
                        "title": title,
                        "description": summary,
                        "severity": severity,
                        "step": index,
                        "url": step.url,
                        "evidence_paths": [path for path in [step.screenshot_path] if path],
                        "tags": ["wcag", "dynamic", "runtime", "gemini"],
                    }
                )

        return {
            "scenario": trace.scenario,
            "model": trace.steps[0].evaluator_result.model if trace.steps and trace.steps[0].evaluator_result else None,
            "summary": {
                "total_steps": len(trace.steps),
                "total_findings": len(findings),
                "steps_with_errors": sum(1 for step in steps if step.get("error")),
                "duration_seconds": trace.metadata.duration_seconds,
            },
            "steps": steps,
            "findings": findings,
        }

    def _build_dynamic_findings_report(
        self,
        trace: RuntimeTrace,
        exploratory_report: dict[str, Any],
        formal_report: DynamicWCAGReport,
    ) -> dict[str, Any]:
        steps = exploratory_report.get("steps", [])
        findings = exploratory_report.get("findings", [])
        summary = exploratory_report.get("summary", {})
        formal_results = [result.model_dump(mode="json") for result in formal_report.results]

        return {
            "scenario": trace.scenario,
            "model": exploratory_report.get("model"),
            "summary": summary,
            "steps": steps,
            "findings": findings,
            "formal_dynamic_wcag_results": formal_results,
            "exploratory_dynamic_observations": steps,
        }

    def prepare_output_dir(self, scenario_id: str, reset: bool = True) -> Path:
        output_dir = self.output_root / scenario_id
        if reset and output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _build_llm(self) -> BaseChatModel:
        if ChatGoogle is None:
            source_hint = f" Local source checked: {_LOCAL_BROWSER_USE_SOURCE}" if _LOCAL_BROWSER_USE_SOURCE else ""
            raise ImportError(
                "browser_use is required to build the browser agent LLM."
                " Install it with `python -m pip install -e ../browser-use` from a11y-agent."
                f"{source_hint}"
            ) from _BROWSER_USE_IMPORT_ERROR

        load_env_from_repo()
        api_key = self.api_key or os.environ.get("GEMINI_API_KEY")
        model_name = self.model_name or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        max_retries = (
            int(self.llm_max_retries)
            if self.llm_max_retries is not None
            else int(os.environ.get("BROWSER_USE_GEMINI_MAX_RETRIES", os.environ.get("GEMINI_MAX_RETRIES", "3")))
        )
        base_delay_s = (
            float(self.llm_retry_base_s)
            if self.llm_retry_base_s is not None
            else float(os.environ.get("BROWSER_USE_GEMINI_RETRY_BASE", "2.0"))
        )
        max_delay_s = (
            float(self.llm_retry_max_s)
            if self.llm_retry_max_s is not None
            else float(os.environ.get("BROWSER_USE_GEMINI_RETRY_MAX", "90.0"))
        )

        if not api_key:
            raise ValueError("GEMINI_API_KEY is required to run the browser agent")

        return ChatGoogle(
            model=normalize_model_name(model_name),
            api_key=api_key,
            max_retries=max_retries,
            retry_base_delay=base_delay_s,
            retry_max_delay=max_delay_s,
        )

    def _build_fallback_llm(self) -> BaseChatModel | None:
        if ChatGoogle is None:
            return None
        load_env_from_repo()
        api_key = self.api_key or os.environ.get("GEMINI_API_KEY")
        fallback_name = self.fallback_model_name
        if fallback_name is None:
            fallback_name = os.environ.get("BROWSER_USE_GEMINI_FALLBACK_MODEL", "gemini-2.5-flash-lite")
        fallback_name = str(fallback_name or "").strip()
        if fallback_name.lower() in {"", "none", "off", "disabled"}:
            return None
        primary_name = self.model_name or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        if normalize_model_name(fallback_name) == normalize_model_name(primary_name):
            return None
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required to build the browser agent fallback LLM")
        return ChatGoogle(
            model=normalize_model_name(fallback_name),
            api_key=api_key,
            max_retries=self.fallback_llm_max_retries,
            retry_base_delay=float(self.llm_retry_base_s if self.llm_retry_base_s is not None else 2.0),
            retry_max_delay=float(self.llm_retry_max_s if self.llm_retry_max_s is not None else 90.0),
        )

    def _build_evaluator(self, output_dir: Path) -> GeminiRuntimeEvaluator:
        load_env_from_repo()
        api_key = self.api_key or os.environ.get("GEMINI_API_KEY")
        model_name = self.model_name or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        max_retries = int(os.environ.get("GEMINI_MAX_RETRIES", "3"))
        base_delay_s = float(os.environ.get("GEMINI_RETRY_BASE", "1.0"))
        max_delay_s = float(os.environ.get("GEMINI_RETRY_MAX", "20.0"))

        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for evaluation")

        cache_dir = output_dir / "_cache"
        return GeminiRuntimeEvaluator(
            api_key=api_key,
            model_name=model_name,
            cache_dir=cache_dir,
            max_retries=max_retries,
            base_delay_s=base_delay_s,
            max_delay_s=max_delay_s,
        )

    def _build_formal_dynamic_analyzer(self, output_dir: Path) -> DynamicWCAGAnalyzer:
        load_env_from_repo()
        api_key = self.api_key or os.environ.get("GEMINI_API_KEY")
        model_name = self.model_name or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        max_retries = int(os.environ.get("GEMINI_MAX_RETRIES", "3"))
        base_delay_s = float(os.environ.get("GEMINI_RETRY_BASE", "1.0"))
        max_delay_s = float(os.environ.get("GEMINI_RETRY_MAX", "20.0"))

        model_client = None
        if api_key:
            model_client = GeminiCriterionModelClient(
                api_key=api_key,
                model_name=model_name,
                cache_dir=output_dir / "_cache" / "formal_dynamic_wcag",
                max_retries=max_retries,
                base_delay_s=base_delay_s,
                max_delay_s=max_delay_s,
            )
        return DynamicWCAGAnalyzer(
            model_client=model_client,
            judge_mode="gemini" if model_client is not None else "rules",
        )

    def _save_browser_use_history_artifacts(
        self,
        output_dir: Path,
        history: Any | None,
        output_model_schema: type[Any] | None,
    ) -> None:
        if history is None:
            return

        if hasattr(history, "model_dump"):
            write_json(output_dir / "browser_use_history.json", history.model_dump(mode="json"))

        final_result = None
        if hasattr(history, "final_result"):
            try:
                final_result = history.final_result()
            except Exception:  # noqa: BLE001
                final_result = None
        if final_result is not None:
            (output_dir / "browser_use_final_result.txt").write_text(str(final_result), encoding="utf-8")

        structured_output = None
        try:
            if output_model_schema is not None and hasattr(history, "get_structured_output"):
                structured_output = history.get_structured_output(output_model_schema)
            elif hasattr(history, "structured_output"):
                structured_output = history.structured_output
        except Exception as exc:  # noqa: BLE001
            write_json(
                output_dir / "browser_use_structured_output_error.json",
                {"error": str(exc), "schema": getattr(output_model_schema, "__name__", None)},
            )
            return

        if structured_output is None:
            return
        if hasattr(structured_output, "model_dump"):
            payload = structured_output.model_dump(mode="json")
        else:
            payload = structured_output
        write_json(output_dir / "browser_use_structured_output.json", payload)

    def _serialize_action_results(self, results: list[Any] | None) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for result in results or []:
            if hasattr(result, "model_dump"):
                serialized.append(result.model_dump(exclude_none=True, mode="json"))
            elif isinstance(result, dict):
                serialized.append(result)
            else:
                serialized.append({"value": str(result)})
        return serialized
