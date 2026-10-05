"""Baseline 3.0: a matched-input ablation of criterion-specific evidence selection.

The primary analyzer and this ablation share one completed browser trace, the same
criterion ids, Gemini configuration, prompt templates, pre-judgment gate decisions,
response parsing, and evidence-reference verification.  FlowA11y receives the
criterion-specific evidence packet selected by its router.  Baseline 3.0 gives every
criterion one global packet containing the output of every runtime probe.

The design measures the value of selecting criterion-specific runtime evidence.  It
is an ablation, not an independent end-to-end accessibility tool.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from benchmarking.dynamic_baselines import MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE
from benchmarking.dynamic_datasets import DynamicBenchmarkScenario
from benchmarking.model_client import GEMINI_GENERATION_CONFIG
from dynamic.analyzers.dynamic_wcag_analyzer import DynamicWCAGAnalyzer, EvidenceGateDecision
from dynamic.preprocessors.runtime_evidence_preprocessor import RuntimeEvidencePreprocessor
from dynamic.preprocessors.wcag_mapper import DYNAMIC_WCAG_REGISTRY, get_criterion
from dynamic.runtime_extractors.utils import ensure_dir, write_json
from dynamic.schemas.runtime_trace import RuntimeTrace
from dynamic.schemas.wcag_dynamic import DynamicWCAGReport, RuntimeEvidenceBundle


EVIDENCE_STRATEGY_VERSION = "baseline-3.0/global-all-probes/v1"


class ModelClient(Protocol):
    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        ...

    def set_context(self, **kwargs: str) -> None:
        ...


@dataclass(frozen=True)
class MatchedGlobalEvidenceBaselineResult:
    baseline_id: str
    baseline_dir: Path
    report: DynamicWCAGReport
    formal_report_path: Path
    runtime_trace_path: Path
    artifacts: dict[str, dict[str, Any]]


class GlobalEvidencePreprocessor:
    """Expose every runtime-probe field to every requested criterion.

    The packet is assembled once from all registered probes, without using a
    criterion id to select its contents.  The outer mapping still uses each
    criterion's probe name because the production analyzer resolves evidence via
    that mapping; every mapped value is the same global packet.
    """

    def __init__(self, source: RuntimeEvidencePreprocessor | None = None) -> None:
        self._source = source or RuntimeEvidencePreprocessor()
        self.source_probe_names: tuple[str, ...] = ()

    def preprocess(self, trace: RuntimeTrace, criteria: list[str] | None = None) -> RuntimeEvidenceBundle:
        source_bundle = self._source.preprocess(trace, criteria=None)
        global_evidence = dict(source_bundle.flat_evidence)
        self.source_probe_names = tuple(sorted(source_bundle.probe_evidence))

        criterion_ids = criteria or DYNAMIC_WCAG_REGISTRY.supported_ids()
        probe_names = {
            get_criterion(criterion_id).probe
            for criterion_id in criterion_ids
            if DYNAMIC_WCAG_REGISTRY.is_supported(criterion_id)
        }
        return RuntimeEvidenceBundle(
            scenario=trace.scenario,
            steps_count=len(trace.steps),
            probe_evidence={probe_name: dict(global_evidence) for probe_name in sorted(probe_names)},
            flat_evidence=dict(global_evidence),
        )


class _FixedGateDynamicWCAGAnalyzer(DynamicWCAGAnalyzer):
    """Use the primary analyzer's deterministic pre-judgment gate decisions."""

    def __init__(self, *, shared_gates: Mapping[str, EvidenceGateDecision], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._shared_gates = dict(shared_gates)

    def _gate_evidence(
        self,
        criterion: Any,
        criterion_evidence: dict[str, Any],
        _evidence_packet: Any,
    ) -> EvidenceGateDecision:
        try:
            return self._shared_gates[criterion.id]
        except KeyError as exc:
            raise ValueError(f"Missing primary evidence-gate decision for criterion {criterion.id}") from exc


async def run_matched_global_evidence_ablation(
    *,
    scenario: DynamicBenchmarkScenario,
    dataset_id: str,
    runtime_trace: RuntimeTrace,
    runtime_trace_path: Path,
    output_dir: Path,
    requested_criteria: list[str],
    primary_artifacts: Mapping[str, Mapping[str, Any]],
    model_client: ModelClient | None,
    run_id: str = "run_1",
) -> MatchedGlobalEvidenceBaselineResult:
    """Run Baseline 3.0 over the exact trace already captured for FlowA11y."""

    baseline_dir = Path(output_dir)
    ensure_dir(baseline_dir)
    criteria = _supported_criteria(requested_criteria)
    shared_gates = _shared_gates_from_primary_artifacts(criteria, primary_artifacts)
    preprocessor = GlobalEvidencePreprocessor()
    analyzer = _FixedGateDynamicWCAGAnalyzer(
        shared_gates=shared_gates,
        preprocessor=preprocessor,
        model_client=model_client,
        judge_mode="gemini",
    )

    if model_client is not None:
        model_client.set_context(run_id=run_id, dataset=dataset_id, case_id=scenario.scenario_id)

    report, evidence_bundle, artifacts = await analyzer.analyze(runtime_trace, criteria=criteria)
    global_evidence = evidence_bundle.flat_evidence
    trace_sha256 = _canonical_sha256(runtime_trace.model_dump(mode="json"))
    evidence_sha256 = _canonical_sha256(global_evidence)
    template_hashes = {
        criterion_id: _text_sha256(analyzer.prompt_loader.load(criterion_id))
        for criterion_id in criteria
    }

    write_json(
        baseline_dir / "input_contract.json",
        {
            "baseline_id": MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id,
            "input_contract": {
                "scenario_id": scenario.scenario_id,
                "scenario_task_used_for_browser_execution": scenario.task_description,
                "scenario_task_injected_into_judge_prompt": False,
                "criterion_ids": criteria,
                "runtime_trace_path": str(runtime_trace_path),
                "runtime_trace_sha256": trace_sha256,
                "model": getattr(model_client, "model_name", None),
                "generation_config": GEMINI_GENERATION_CONFIG,
                "prompt_renderer": "DynamicWCAGAnalyzer.PromptLoader.render",
                "prompt_template_sha256": template_hashes,
                "response_parser": "DynamicWCAGAnalyzer._coerce_result_from_payload",
                "response_verifier": "DynamicWCAGAnalyzer._verify_llm_result",
                "shared_gate_source": "primary FlowA11y analyzer artifacts; gate decision only, no LLM output",
            },
            "evidence_policy": {
                "strategy": "global_all_probes_no_criterion_filtering",
                "version": EVIDENCE_STRATEGY_VERSION,
                "source_probe_names": list(preprocessor.source_probe_names),
                "global_evidence_path": "global_evidence.json",
                "global_evidence_sha256": evidence_sha256,
                "global_evidence_characters": len(_canonical_json(global_evidence)),
                "criterion_specific_content_selection": False,
            },
        },
    )
    write_json(baseline_dir / "global_evidence.json", global_evidence)
    write_json(
        baseline_dir / "shared_gate_decisions.json",
        {criterion_id: _gate_as_dict(gate) for criterion_id, gate in shared_gates.items()},
    )

    formal_report_path = _export_ablation_artifacts(
        baseline_dir=baseline_dir,
        analyzer=analyzer,
        report=report,
        artifacts=artifacts,
        evidence_sha256=evidence_sha256,
    )
    return MatchedGlobalEvidenceBaselineResult(
        baseline_id=MATCHED_GLOBAL_EVIDENCE_ABLATION_BASELINE.baseline_id,
        baseline_dir=baseline_dir,
        report=report,
        formal_report_path=formal_report_path,
        runtime_trace_path=Path(runtime_trace_path),
        artifacts=artifacts,
    )


def _supported_criteria(requested_criteria: list[str]) -> list[str]:
    result: list[str] = []
    for raw in requested_criteria:
        criterion_id = str(raw).strip()
        if criterion_id and criterion_id not in result and DYNAMIC_WCAG_REGISTRY.is_supported(criterion_id):
            result.append(criterion_id)
    return result


def _shared_gates_from_primary_artifacts(
    criteria: list[str],
    artifacts: Mapping[str, Mapping[str, Any]],
) -> dict[str, EvidenceGateDecision]:
    gates: dict[str, EvidenceGateDecision] = {}
    allowed = {"proceed", "warning", "incomplete", "not_applicable"}
    for criterion_id in criteria:
        artifact = artifacts.get(criterion_id)
        if not isinstance(artifact, Mapping):
            raise ValueError(f"FlowA11y did not export an evidence artifact for criterion {criterion_id}")
        decision = str(artifact.get("gate_decision") or "")
        if decision not in allowed:
            raise ValueError(f"FlowA11y exported an invalid evidence-gate decision for criterion {criterion_id}: {decision}")
        quality = artifact.get("evidence_quality")
        quality_dict = dict(quality) if isinstance(quality, Mapping) else {}
        missing = quality_dict.get("missing_or_unusable_evidence")
        gates[criterion_id] = EvidenceGateDecision(
            gate_decision=decision,  # type: ignore[arg-type]
            gate_reason=str(artifact.get("gate_reason") or ""),
            evidence_quality=quality_dict,
            missing_evidence=[str(item) for item in missing] if isinstance(missing, list) else [],
        )
    return gates


def _export_ablation_artifacts(
    *,
    baseline_dir: Path,
    analyzer: DynamicWCAGAnalyzer,
    report: DynamicWCAGReport,
    artifacts: Mapping[str, Mapping[str, Any]],
    evidence_sha256: str,
) -> Path:
    """Write compact, reproducible artifacts without duplicating global evidence per criterion."""

    criteria_root = baseline_dir / "criteria"
    ensure_dir(criteria_root)
    for result in report.results:
        criterion_dir = criteria_root / result.criterion_id.replace(".", "_")
        ensure_dir(criterion_dir)
        artifact = artifacts.get(result.criterion_id, {})
        prompt_text = str(artifact.get("prompt") or "")
        template_text = analyzer.prompt_loader.load(result.criterion_id)
        evidence = artifact.get("evidence") if isinstance(artifact.get("evidence"), Mapping) else {}

        write_json(criterion_dir / "result.json", result.model_dump(mode="json"))
        write_json(
            criterion_dir / "evidence.json",
            {
                "evidence_strategy": EVIDENCE_STRATEGY_VERSION,
                "shared_global_evidence_path": "../../global_evidence.json",
                "shared_global_evidence_sha256": evidence_sha256,
                "evidence_quality_summary": evidence.get("evidence_quality_summary", {}),
                "global_evidence_keys": sorted(key for key in evidence if key != "evidence_quality_summary"),
            },
        )
        write_json(
            criterion_dir / "gate.json",
            {
                "gate_decision": artifact.get("gate_decision", ""),
                "gate_reason": artifact.get("gate_reason", ""),
                "evidence_quality": artifact.get("evidence_quality", {}),
                "shared_from_primary_analyzer": True,
            },
        )
        (criterion_dir / "prompt_template.txt").write_text(template_text, encoding="utf-8")
        write_json(
            criterion_dir / "prompt_manifest.json",
            {
                "prompt_template_sha256": _text_sha256(template_text),
                "rendered_prompt_sha256": _text_sha256(prompt_text),
                "rendered_prompt_characters": len(prompt_text),
                "evidence_path": "../../global_evidence.json",
                "evidence_sha256": evidence_sha256,
            },
        )
        llm_response = artifact.get("llm_response")
        if llm_response is not None:
            write_json(criterion_dir / "llm_response.json", llm_response)

    formal_report_path = baseline_dir / "formal_dynamic_wcag_results.json"
    write_json(formal_report_path, report.model_dump(mode="json"))
    return formal_report_path


def _gate_as_dict(gate: EvidenceGateDecision) -> dict[str, Any]:
    return {
        "gate_decision": gate.gate_decision,
        "gate_reason": gate.gate_reason,
        "evidence_quality": gate.evidence_quality,
        "missing_evidence": gate.missing_evidence,
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _canonical_sha256(value: Any) -> str:
    return _text_sha256(_canonical_json(value))


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "GlobalEvidencePreprocessor",
    "MatchedGlobalEvidenceBaselineResult",
    "run_matched_global_evidence_ablation",
]
