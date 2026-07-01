from __future__ import annotations

import re
from collections import defaultdict
from urllib.parse import urlparse

from dynamic.probes import DynamicProbeRegistry
from dynamic.probes.common import flatten_probe_evidence
from dynamic.preprocessors.wcag_mapper import DYNAMIC_WCAG_REGISTRY
from dynamic.schemas.runtime_state import RuntimeState
from dynamic.schemas.runtime_trace import RuntimeTrace
from dynamic.schemas.wcag_dynamic import RuntimeEvidenceBundle


class RuntimeEvidencePreprocessor:
    def __init__(self, probe_registry: DynamicProbeRegistry | None = None) -> None:
        self.probe_registry = probe_registry or DynamicProbeRegistry()

    def preprocess(self, trace: RuntimeTrace, criteria: list[str] | None = None) -> RuntimeEvidenceBundle:
        analysis_steps = self._analysis_steps(trace)
        probe_evidence = self._collect_probe_evidence(analysis_steps, criteria)
        flat = flatten_probe_evidence(probe_evidence)
        return RuntimeEvidenceBundle(
            scenario=trace.scenario,
            steps_count=len(analysis_steps),
            probe_evidence=probe_evidence,
            flat_evidence=flat,
        )

    def _collect_probe_evidence(self, states: list[RuntimeState], criteria: list[str] | None) -> dict[str, dict]:
        if criteria is None:
            return self.probe_registry.collect_all(states)

        probe_names_by_criterion = defaultdict(str)
        for criterion_id in criteria:
            if not DYNAMIC_WCAG_REGISTRY.is_supported(criterion_id):
                continue
            criterion = DYNAMIC_WCAG_REGISTRY.get(criterion_id)
            probe_names_by_criterion[criterion.id] = criterion.probe

        probe_names = list(probe_names_by_criterion.values())
        if not probe_names:
            return {}
        return self.probe_registry.collect_for_probes(probe_names, states)

    def _analysis_steps(self, trace: RuntimeTrace) -> list[RuntimeState]:
        """Drop leading browser-profile pages captured before the task target."""

        states = list(trace.steps)
        target_host = _first_task_host(trace.scenario)
        if not states or not target_host:
            return states

        first_target_index = next(
            (
                index
                for index, state in enumerate(states)
                if _hosts_match(target_host, urlparse(str(state.url or "")).hostname or "")
            ),
            None,
        )
        if first_target_index is None:
            return states
        return states[first_target_index:]


def _first_task_host(task: str) -> str:
    match = re.search(r"https?://[^\s\"'<>]+", str(task or ""))
    if match is None:
        return ""
    return (urlparse(match.group(0).rstrip(".,);]")).hostname or "").lower()


def _hosts_match(expected: str, actual: str) -> bool:
    expected = expected.lower().removeprefix("www.")
    actual = actual.lower().removeprefix("www.")
    return expected == actual or expected.endswith("." + actual) or actual.endswith("." + expected)
