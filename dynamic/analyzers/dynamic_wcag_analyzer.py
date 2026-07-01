from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from dynamic.analyzers.decision_status_router import CalibratedStatusRouter, status_for_pre_llm_packet
from dynamic.analyzers.evidence_refs import canonicalize_evidence_refs, resolve_evidence_ref
from dynamic.analyzers.fail_confidence_validator import FailConfidenceValidator
from dynamic.analyzers.wcag_router import DynamicWCAGRouter
from dynamic.preprocessors import (
    CriterionEvidencePacketBuilder,
    DynamicEvidenceValidator,
    EvidenceQualityAssessor,
    EvidenceQualitySummary,
    RuntimeEvidencePreprocessor,
)
from dynamic.preprocessors.wcag_mapper import get_criterion, prompt_filename
from dynamic.runtime_extractors.utils import ensure_dir, write_json
from dynamic.schemas.runtime_state import RuntimeState
from dynamic.schemas.runtime_trace import RuntimeTrace, RuntimeTraceMetadata
from dynamic.schemas.wcag_dynamic import (
    CriterionEvidencePacket,
    DynamicCriterion,
    DynamicCriterionResult,
    DynamicStatus,
    DynamicViolation,
    DynamicWCAGReport,
    RuntimeEvidenceBundle,
)


class DynamicModelClient(Protocol):
    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        ...


@dataclass(frozen=True)
class EvidenceGateDecision:
    gate_decision: Literal["proceed", "warning", "incomplete", "not_applicable"]
    gate_reason: str
    evidence_quality: dict[str, Any]
    missing_evidence: list[str]


@dataclass(frozen=True)
class LLMOutputVerification:
    valid: bool
    error_code: str = ""
    issues: list[str] | None = None
    invalid_evidence_refs: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "error_code": self.error_code,
            "issues": list(self.issues or []),
            "invalid_evidence_refs": list(self.invalid_evidence_refs or []),
            "invalid_evidence_ref_count": len(self.invalid_evidence_refs or []),
        }


class PromptLoader:
    def __init__(self, prompts_dir: Path) -> None:
        self.prompts_dir = prompts_dir
        self._cache: dict[str, str] = {}

    def load(self, criterion_id: str) -> str:
        if criterion_id not in self._cache:
            path = self.prompts_dir / prompt_filename(criterion_id)
            self._cache[criterion_id] = path.read_text(encoding="utf-8")
        return self._cache[criterion_id]

    def render(self, criterion: DynamicCriterion, evidence_payload: dict[str, Any]) -> str:
        template = self.load(criterion.id)
        template = self._with_quality_instructions(template, evidence_payload)
        evidence_text = json.dumps(evidence_payload, sort_keys=True, ensure_ascii=True)
        return template.replace("{{CRITERION_ID}}", criterion.id).replace("{{CRITERION_NAME}}", criterion.name).replace(
            "{{EVIDENCE}}", evidence_text
        )

    def _with_quality_instructions(self, template: str, evidence_payload: dict[str, Any]) -> str:
        reference_roots = ", ".join(sorted(str(key) for key in evidence_payload))
        instructions = (
            "Soft evidence quality guidance:\n"
            "- criterion_evidence_packet is deterministic pre-analysis metadata produced before model review.\n"
            "- If criterion_evidence_packet.applicability is not_applicable, return not_applicable, not fail.\n"
            "- If criterion_evidence_packet.applicability is insufficient_evidence, return incomplete or warning, not fail.\n"
            "- Return fail only when criterion_evidence_packet.applicability is applicable and evidence_strength is direct or behavioral_direct.\n"
            "- Return warning for plausible but indirect, contradicted, listener-only, generic mutation, or DOM-only signals.\n"
            "- evidence_quality_summary is advisory context, not an oracle label.\n"
            "- Prefer pass or fail when direct, computed, observed, or moderate evidence supports the judgment.\n"
            "- Use warning when evidence is plausible but weak or mostly static.\n"
            "- Use incomplete only when evidence is structurally missing, unusable, or does not support a judgment.\n"
            "- Use not_applicable only when applicability is not_applicable_candidate or feature absence is directly supported.\n"
            "- Do not convert weak but resolvable evidence to incomplete solely because it is weak.\n"
            "- Fail and warning outputs must cite concrete evidence_refs that resolve inside the Evidence JSON.\n"
            f"- Canonical evidence reference roots for this criterion are: {reference_roots}.\n"
            "- Packet facts must use the full prefix criterion_evidence_packet.compact_runtime_facts; do not cite compact_runtime_facts or focus_entries as top-level roots.\n"
            "- To support an absence claim, cite the exact empty top-level collection, for example caption_tracks or transcript_indicators; do not invent nested properties such as media_elements[0].caption_tracks.\n"
            "- Do not treat recommended_status_floor or recommended_status_ceiling as ground truth.\n\n"
        )
        marker = "Evidence JSON:"
        if marker in template and instructions not in template:
            return template.replace(marker, instructions + marker, 1)
        return template


class DynamicWCAGAnalyzer:
    def __init__(
        self,
        prompts_dir: Path | None = None,
        preprocessor: RuntimeEvidencePreprocessor | None = None,
        validator: DynamicEvidenceValidator | None = None,
        router: DynamicWCAGRouter | None = None,
        model_client: DynamicModelClient | None = None,
        judge_mode: Literal["rules", "gemini", "hybrid"] = "rules",
    ) -> None:
        if judge_mode not in {"rules", "gemini", "hybrid"}:
            raise ValueError(f"Unsupported dynamic WCAG judge mode: {judge_mode}")
        root = Path(__file__).resolve().parent.parent
        self.prompts_dir = prompts_dir or (root / "prompts")
        self.prompt_loader = PromptLoader(self.prompts_dir)
        self.preprocessor = preprocessor or RuntimeEvidencePreprocessor()
        self.validator = validator or DynamicEvidenceValidator()
        self.packet_builder = CriterionEvidencePacketBuilder()
        self.fail_confidence_validator = FailConfidenceValidator()
        self.status_router = CalibratedStatusRouter()
        self.quality_assessor = EvidenceQualityAssessor()
        self.router = router or DynamicWCAGRouter()
        self.model_client = model_client
        self.judge_mode = judge_mode

    async def analyze(
        self,
        runtime_state: RuntimeTrace | RuntimeState | list[RuntimeState],
        criteria: list[str] | None = None,
    ) -> tuple[DynamicWCAGReport, RuntimeEvidenceBundle, dict[str, dict[str, Any]]]:
        trace = self._normalize_trace(runtime_state)
        requested_criteria = self.router.resolve(None)[0] if criteria is None else [str(criterion_id) for criterion_id in criteria]
        criteria_to_run, unsupported_criteria = self.router.resolve(criteria)
        evidence_bundle = self.preprocessor.preprocess(trace, criteria_to_run if criteria is not None else None)

        results: list[DynamicCriterionResult] = []
        artifacts: dict[str, dict[str, Any]] = {}

        for criterion_id in criteria_to_run:
            criterion = get_criterion(criterion_id)
            probe_name = self.router.probe_for(criterion_id)
            raw_criterion_evidence = evidence_bundle.probe_evidence.get(probe_name, {})
            quality_summary = self.quality_assessor.assess(criterion, raw_criterion_evidence)
            criterion_evidence = self._attach_quality_summary(raw_criterion_evidence, quality_summary)
            evidence_packet = self.packet_builder.build(criterion, criterion_evidence)
            criterion_evidence = self._attach_evidence_packet(criterion_evidence, evidence_packet)
            gate = self._gate_evidence(criterion, criterion_evidence, evidence_packet)
            artifact = self._artifact_for(criterion, criterion_evidence, gate, quality_summary, evidence_packet)

            if gate.gate_decision in {"warning", "incomplete", "not_applicable"}:
                status, decision_level = status_for_pre_llm_packet(evidence_packet)
                if gate.gate_decision == "incomplete":
                    status = DynamicStatus.incomplete
                    decision_level = "insufficient_evidence"
                if gate.gate_decision == "not_applicable":
                    status = DynamicStatus.not_applicable
                    decision_level = "not_applicable"
                result = DynamicCriterionResult(
                    criterion_id=criterion.id,
                    criterion_name=criterion.name,
                    status=status,
                    summary=gate.gate_reason,
                    missing_evidence=gate.missing_evidence if status == DynamicStatus.incomplete else [],
                    requires_llm=False,
                    decision_level=decision_level,
                    evidence_packet=evidence_packet,
                    generated_at=datetime.now(timezone.utc),
                )
                results.append(result)
                artifacts[criterion.id] = artifact
                continue

            if self._uses_gemini_judge(criterion):
                result, prompt_text, llm_payload = await self._evaluate_with_llm(
                    criterion,
                    criterion_evidence,
                    gate,
                    evidence_packet,
                )
                results.append(result)
                artifact.update({"prompt": prompt_text, "llm_response": llm_payload})
                artifacts[criterion.id] = artifact
                continue

            if self.judge_mode == "gemini" and self.model_client is None:
                result = DynamicCriterionResult(
                    criterion_id=criterion.id,
                    criterion_name=criterion.name,
                    status=DynamicStatus.incomplete,
                    summary="Gemini dynamic WCAG judge requested but no model client is configured.",
                    missing_evidence=["llm_evaluation"],
                    requires_llm=True,
                    llm_error="missing_model_client",
                    decision_level="insufficient_evidence",
                    evidence_packet=evidence_packet,
                    generated_at=datetime.now(timezone.utc),
                )
                results.append(result)
                artifacts[criterion.id] = artifact
                continue

            if criterion.requires_llm and self.model_client is None and self._has_deterministic_rule(criterion):
                result = self._evaluate_rule_based(criterion, criterion_evidence, evidence_packet)
                results.append(result)
                artifacts[criterion.id] = artifact
                continue

            if criterion.requires_llm and self.model_client is not None:
                result, prompt_text, llm_payload = await self._evaluate_with_llm(
                    criterion,
                    criterion_evidence,
                    gate,
                    evidence_packet,
                )
                results.append(result)
                artifact.update({"prompt": prompt_text, "llm_response": llm_payload})
                artifacts[criterion.id] = artifact
                continue

            if criterion.requires_llm and self.model_client is None:
                result = DynamicCriterionResult(
                    criterion_id=criterion.id,
                    criterion_name=criterion.name,
                    status=DynamicStatus.incomplete,
                    summary="LLM evaluation required but no model client is configured.",
                    missing_evidence=["llm_evaluation"],
                    requires_llm=False,
                    decision_level="insufficient_evidence",
                    evidence_packet=evidence_packet,
                    generated_at=datetime.now(timezone.utc),
                )
                results.append(result)
                artifacts[criterion.id] = artifact
                continue

            result = self._evaluate_rule_based(criterion, criterion_evidence, evidence_packet)
            results.append(result)
            artifacts[criterion.id] = artifact

        report = DynamicWCAGReport(
            scenario=trace.scenario,
            generated_at=datetime.now(timezone.utc),
            criteria_requested=requested_criteria,
            results=results,
            unsupported_criteria=unsupported_criteria,
        )
        return report, evidence_bundle, artifacts

    def _uses_gemini_judge(self, criterion: DynamicCriterion) -> bool:
        if self.model_client is None:
            return False
        if self.judge_mode == "gemini":
            return True
        if self.judge_mode == "hybrid":
            return (self.prompts_dir / prompt_filename(criterion.id)).exists()
        return False

    def _gate_evidence(
        self,
        criterion: DynamicCriterion,
        criterion_evidence: dict[str, Any],
        evidence_packet: CriterionEvidencePacket,
    ) -> EvidenceGateDecision:
        missing = self.validator.missing_evidence(criterion, criterion_evidence)
        if (
            criterion.id in {"2.4.7", "2.4.13"}
            and "screenshot_paths" in missing
            and int(
                evidence_packet.compact_runtime_facts.get(
                    "strong_computed_missing_focus_indicator_count",
                    0,
                )
                or 0
            )
            > 0
        ):
            missing = [key for key in missing if key != "screenshot_paths"]
        if missing:
            return EvidenceGateDecision(
                gate_decision="incomplete",
                gate_reason="Missing or structurally unusable required runtime evidence: " + ", ".join(missing),
                evidence_quality=self._evidence_quality(
                    criterion,
                    criterion_evidence,
                    missing_evidence=missing,
                    applicable=None,
                    sufficient=False,
                ),
                missing_evidence=missing,
            )

        if evidence_packet.applicability == "not_applicable":
            return EvidenceGateDecision(
                gate_decision="not_applicable",
                gate_reason=evidence_packet.applicability_reason,
                evidence_quality=self._evidence_quality(
                    criterion,
                    criterion_evidence,
                    missing_evidence=[],
                    applicable=False,
                    sufficient=False,
                ),
                missing_evidence=[],
            )

        if evidence_packet.applicability == "insufficient_evidence":
            status, _ = status_for_pre_llm_packet(evidence_packet)
            return EvidenceGateDecision(
                gate_decision="warning" if status == DynamicStatus.warning else "incomplete",
                gate_reason=evidence_packet.applicability_reason,
                evidence_quality=self._evidence_quality(
                    criterion,
                    criterion_evidence,
                    missing_evidence=list(evidence_packet.missing_evidence),
                    applicable=True,
                    sufficient=False,
                ),
                missing_evidence=list(evidence_packet.missing_evidence),
            )

        if self._is_not_applicable(criterion, criterion_evidence):
            return EvidenceGateDecision(
                gate_decision="not_applicable",
                gate_reason="Criterion not applicable based on observed runtime features.",
                evidence_quality=self._evidence_quality(
                    criterion,
                    criterion_evidence,
                    missing_evidence=[],
                    applicable=False,
                    sufficient=False,
                ),
                missing_evidence=[],
            )

        return EvidenceGateDecision(
            gate_decision="proceed",
            gate_reason="Required runtime evidence is present and the criterion appears applicable.",
            evidence_quality=self._evidence_quality(
                criterion,
                criterion_evidence,
                missing_evidence=[],
                applicable=True,
                sufficient=True,
            ),
            missing_evidence=[],
        )

    def _evidence_quality(
        self,
        criterion: DynamicCriterion,
        criterion_evidence: dict[str, Any],
        *,
        missing_evidence: list[str],
        applicable: bool | None,
        sufficient: bool,
    ) -> dict[str, Any]:
        present = sorted(key for key in criterion.required_evidence if key in criterion_evidence)
        non_empty_required = sorted(
            key for key in criterion.required_evidence if self._has_non_empty_evidence_value(criterion_evidence.get(key))
        )
        empty_required = sorted(key for key in criterion.required_evidence if key in criterion_evidence and key not in non_empty_required)
        sufficient_for_judgment = sufficient and not missing_evidence and not empty_required
        return {
            "required_evidence": list(criterion.required_evidence),
            "present_required_evidence": present,
            "non_empty_required_evidence": non_empty_required,
            "empty_required_evidence": empty_required,
            "missing_or_unusable_evidence": list(missing_evidence),
            "probe": criterion.probe,
            "probe_evidence_keys": sorted(criterion_evidence.keys()),
            "applicable": applicable,
            "sufficient_for_judgment": sufficient_for_judgment,
        }

    def _has_non_empty_evidence_value(self, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, dict, tuple, set)):
            return bool(value)
        return True

    def _artifact_for(
        self,
        criterion: DynamicCriterion,
        criterion_evidence: dict[str, Any],
        gate: EvidenceGateDecision,
        quality_summary: EvidenceQualitySummary,
        evidence_packet: CriterionEvidencePacket,
    ) -> dict[str, Any]:
        return {
            "criterion": criterion.model_dump(mode="json"),
            "evidence": criterion_evidence,
            "gate_decision": gate.gate_decision,
            "gate_reason": gate.gate_reason,
            "evidence_quality": gate.evidence_quality,
            "soft_evidence_quality": quality_summary.model_dump(mode="json"),
            "criterion_evidence_packet": evidence_packet.model_dump(mode="json"),
        }

    def _attach_quality_summary(
        self,
        criterion_evidence: dict[str, Any],
        quality_summary: EvidenceQualitySummary,
    ) -> dict[str, Any]:
        payload = dict(criterion_evidence)
        payload["evidence_quality_summary"] = quality_summary.model_dump(mode="json")
        return payload

    def _attach_evidence_packet(
        self,
        criterion_evidence: dict[str, Any],
        evidence_packet: CriterionEvidencePacket,
    ) -> dict[str, Any]:
        payload = dict(criterion_evidence)
        payload["criterion_evidence_packet"] = evidence_packet.model_dump(mode="json")
        return payload

    def export(
        self,
        output_dir: Path,
        report: DynamicWCAGReport,
        artifacts: dict[str, dict[str, Any]],
    ) -> Path:
        criteria_root = output_dir / "criteria"
        ensure_dir(criteria_root)

        for result in report.results:
            criterion_dir = criteria_root / result.criterion_id.replace(".", "_")
            ensure_dir(criterion_dir)
            write_json(criterion_dir / "result.json", result.model_dump(mode="json"))

            artifact = artifacts.get(result.criterion_id, {})
            write_json(criterion_dir / "evidence.json", artifact.get("evidence", {}))
            write_json(
                criterion_dir / "gate.json",
                {
                    "gate_decision": artifact.get("gate_decision", ""),
                    "gate_reason": artifact.get("gate_reason", ""),
                    "evidence_quality": artifact.get("evidence_quality", {}),
                    "criterion_evidence_packet": artifact.get("criterion_evidence_packet", {}),
                },
            )

            prompt_text = artifact.get("prompt")
            llm_response = artifact.get("llm_response")
            if prompt_text is not None:
                (criterion_dir / "prompt.txt").write_text(str(prompt_text), encoding="utf-8")
            if llm_response is not None:
                write_json(criterion_dir / "llm_response.json", llm_response)

            screenshot_paths = artifact.get("evidence", {}).get("screenshot_paths", [])
            if isinstance(screenshot_paths, list) and screenshot_paths:
                screenshots_dir = criterion_dir / "screenshots"
                ensure_dir(screenshots_dir)
                for path in screenshot_paths:
                    source = Path(path)
                    if source.exists():
                        shutil.copy2(source, screenshots_dir / source.name)

        formal_path = output_dir / "formal_dynamic_wcag_results.json"
        write_json(formal_path, report.model_dump(mode="json"))
        return formal_path

    def _normalize_trace(self, runtime_state: RuntimeTrace | RuntimeState | list[RuntimeState]) -> RuntimeTrace:
        if isinstance(runtime_state, RuntimeTrace):
            return runtime_state
        if isinstance(runtime_state, RuntimeState):
            return RuntimeTrace(
                scenario="runtime_state",
                metadata=RuntimeTraceMetadata(start_time=runtime_state.timestamp, end_time=runtime_state.timestamp),
                steps=[runtime_state],
            )
        if isinstance(runtime_state, list):
            if not runtime_state:
                now = datetime.now(timezone.utc)
                return RuntimeTrace(scenario="runtime_states", metadata=RuntimeTraceMetadata(start_time=now), steps=[])
            return RuntimeTrace(
                scenario="runtime_states",
                metadata=RuntimeTraceMetadata(start_time=runtime_state[0].timestamp, end_time=runtime_state[-1].timestamp),
                steps=runtime_state,
            )
        raise TypeError("runtime_state must be RuntimeTrace, RuntimeState, or list[RuntimeState]")

    async def _evaluate_with_llm(
        self,
        criterion: DynamicCriterion,
        criterion_evidence: dict[str, Any],
        gate: EvidenceGateDecision,
        evidence_packet: CriterionEvidencePacket,
    ) -> tuple[DynamicCriterionResult, str, dict[str, Any]]:
        prompt = self.prompt_loader.render(criterion, criterion_evidence)
        response_text = await self.model_client.complete(prompt)
        payload = self._parse_json_payload(response_text)
        llm_payload: dict[str, Any] = {"raw_response_text": response_text, "parsed_response": payload}

        api_error = self._payload_error(payload)
        if api_error and not payload.get("criterion_id"):
            result = DynamicCriterionResult(
                criterion_id=criterion.id,
                criterion_name=criterion.name,
                status=DynamicStatus.incomplete,
                summary="Model API error prevented a dynamic WCAG judgment.",
                missing_evidence=["llm_evaluation"],
                requires_llm=True,
                llm_error=api_error,
                model=getattr(self.model_client, "model_name", None),
                decision_level="insufficient_evidence",
                evidence_packet=evidence_packet,
                generated_at=datetime.now(timezone.utc),
            )
            verification = LLMOutputVerification(valid=False, error_code=api_error, issues=["api_error"])
            llm_payload["llm_verification"] = verification.as_dict()
            self._annotate_latest_model_call(criterion, result, verification)
            return result, prompt, llm_payload

        parsed = self._coerce_result_from_payload(criterion, payload)
        if parsed is None:
            result = DynamicCriterionResult(
                criterion_id=criterion.id,
                criterion_name=criterion.name,
                status=DynamicStatus.incomplete,
                summary="Invalid JSON from model or schema mismatch.",
                requires_llm=True,
                llm_error="invalid_json",
                model=getattr(self.model_client, "model_name", None),
                decision_level="insufficient_evidence",
                evidence_packet=evidence_packet,
                generated_at=datetime.now(timezone.utc),
            )
            verification = LLMOutputVerification(valid=False, error_code="invalid_json", issues=["schema_or_criterion_mismatch"])
            llm_payload["llm_verification"] = verification.as_dict()
            self._annotate_latest_model_call(criterion, result, verification)
            return result, prompt, llm_payload

        parsed.model = parsed.model or getattr(self.model_client, "model_name", None)
        parsed, evidence_ref_normalizations = self._normalize_result_evidence_refs(parsed, criterion_evidence)
        if evidence_ref_normalizations:
            llm_payload["evidence_ref_normalizations"] = evidence_ref_normalizations
        verification = self._verify_llm_result(criterion, criterion_evidence, parsed, gate)
        result = parsed if verification.valid else self._downgrade_invalid_llm_result(criterion, parsed, verification)
        if verification.valid:
            result = self.fail_confidence_validator.validate(criterion, result, criterion_evidence)
            result = self.status_router.route(criterion, result, evidence_packet)
            if result.fail_validation is not None:
                llm_payload["fail_validation"] = result.fail_validation.model_dump(mode="json")
            llm_payload["status_routing"] = {
                "decision_level": result.decision_level,
                "validated_status": result.status.value,
            }
        else:
            result = result.model_copy(update={"decision_level": "insufficient_evidence", "evidence_packet": evidence_packet})
        llm_payload["criterion_evidence_packet"] = evidence_packet.model_dump(mode="json")
        llm_payload["llm_verification"] = verification.as_dict()
        self._annotate_latest_model_call(criterion, result, verification)
        return result, prompt, llm_payload

    def _verify_llm_result(
        self,
        criterion: DynamicCriterion,
        criterion_evidence: dict[str, Any],
        result: DynamicCriterionResult,
        gate: EvidenceGateDecision,
    ) -> LLMOutputVerification:
        issues: list[str] = []
        invalid_refs: list[str] = []

        if result.criterion_id != criterion.id:
            issues.append("criterion_id_mismatch")
        if result.status not in set(DynamicStatus):
            issues.append("invalid_status")

        if gate.gate_decision == "incomplete" and result.status != DynamicStatus.incomplete:
            issues.append("contradicts_pre_gate_incomplete")
        if gate.gate_decision == "not_applicable" and result.status != DynamicStatus.not_applicable:
            issues.append("contradicts_pre_gate_not_applicable")

        if result.status in {DynamicStatus.fail, DynamicStatus.warning}:
            if not result.violations:
                issues.append("missing_violations")
            for violation_index, violation in enumerate(result.violations):
                if not violation.evidence_refs:
                    issues.append(f"violations[{violation_index}].missing_evidence_refs")
                    continue
                for evidence_ref in violation.evidence_refs:
                    resolution = resolve_evidence_ref(criterion_evidence, evidence_ref, allow_empty=True)
                    if not resolution.resolved:
                        invalid_refs.append(evidence_ref)
                        issues.append(f"{evidence_ref}:{resolution.reason}")

        if invalid_refs or any(issue.startswith("violations[") or issue == "missing_violations" for issue in issues):
            return LLMOutputVerification(
                valid=False,
                error_code="invalid_evidence_refs",
                issues=issues,
                invalid_evidence_refs=invalid_refs,
            )
        if issues:
            return LLMOutputVerification(valid=False, error_code="pre_gate_contradiction", issues=issues)
        return LLMOutputVerification(valid=True)

    def _normalize_result_evidence_refs(
        self,
        result: DynamicCriterionResult,
        criterion_evidence: dict[str, Any],
    ) -> tuple[DynamicCriterionResult, list[dict[str, str]]]:
        normalizations: list[dict[str, str]] = []
        normalized_violations = []
        for violation in result.violations:
            normalized_refs, ref_audit = canonicalize_evidence_refs(
                criterion_evidence,
                list(violation.evidence_refs),
            )
            normalizations.extend(ref_audit)
            normalized_violations.append(violation.model_copy(update={"evidence_refs": normalized_refs}))
        if not normalizations:
            return result, []
        return result.model_copy(update={"violations": normalized_violations}), normalizations

    def _downgrade_invalid_llm_result(
        self,
        criterion: DynamicCriterion,
        result: DynamicCriterionResult,
        verification: LLMOutputVerification,
    ) -> DynamicCriterionResult:
        return DynamicCriterionResult(
            criterion_id=criterion.id,
            criterion_name=criterion.name,
            status=DynamicStatus.incomplete,
            summary="Model judgment failed mechanical evidence-contract verification.",
            missing_evidence=list(verification.invalid_evidence_refs or []),
            violations=[],
            requires_llm=True,
            llm_error=verification.error_code or "invalid_llm_result",
            model=result.model,
            generated_at=datetime.now(timezone.utc),
        )

    def _annotate_latest_model_call(
        self,
        criterion: DynamicCriterion,
        result: DynamicCriterionResult,
        verification: LLMOutputVerification,
    ) -> None:
        records = getattr(self.model_client, "call_records", None)
        if not isinstance(records, list):
            return
        for record in reversed(records):
            if not isinstance(record, dict):
                continue
            record_criterion = str(record.get("criterion") or "")
            if record_criterion and record_criterion != criterion.id:
                continue
            if record.get("verifier_status"):
                continue
            record.update(
                {
                    "verifier_status": "valid" if verification.valid else "invalid",
                    "verifier_error": verification.error_code,
                    "invalid_llm_result": not verification.valid,
                    "invalid_evidence_ref_count": len(verification.invalid_evidence_refs or []),
                    "invalid_evidence_refs": list(verification.invalid_evidence_refs or []),
                    "verifier_issues": list(verification.issues or []),
                    "final_status": result.status.value,
                    "decision_level": result.decision_level or "",
                    "final_llm_error": result.llm_error or "",
                    "fail_validator": result.fail_validation.validator if result.fail_validation else "",
                    "fail_validation_reason": result.fail_validation.reason if result.fail_validation else "",
                    "fail_validation_evidence_strength": (
                        result.fail_validation.evidence_strength if result.fail_validation else ""
                    ),
                    "fail_validation_rule": result.fail_validation.rule if result.fail_validation else "",
                }
            )
            return

    def _payload_error(self, payload: dict[str, Any]) -> str:
        error = payload.get("error")
        if error is None:
            return ""
        return str(error).strip()

    def _coerce_result_from_payload(
        self,
        criterion: DynamicCriterion,
        payload: dict[str, Any],
    ) -> DynamicCriterionResult | None:
        criterion_id = str(payload.get("criterion_id", "")).strip()
        if criterion_id != criterion.id:
            return None

        status = str(payload.get("status", "")).strip().lower()
        if status not in {"pass", "fail", "warning", "not_applicable", "incomplete"}:
            return None

        summary = str(payload.get("summary", "")).strip()
        if not summary:
            return None

        violations_payload = payload.get("violations", [])
        violations: list[DynamicViolation] = []
        if isinstance(violations_payload, list):
            for item in violations_payload:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title", "")).strip()
                item_summary = str(item.get("summary", "")).strip()
                if not title or not item_summary:
                    continue
                evidence_refs = item.get("evidence_refs", [])
                if not isinstance(evidence_refs, list):
                    evidence_refs = []
                violations.append(
                    DynamicViolation(
                        title=title,
                        summary=item_summary,
                        severity=str(item.get("severity", "medium")),
                        evidence_refs=[str(ref) for ref in evidence_refs],
                    )
                )

        missing_evidence_payload = payload.get("missing_evidence", [])
        missing_evidence = [str(value) for value in missing_evidence_payload] if isinstance(missing_evidence_payload, list) else []

        return DynamicCriterionResult(
            criterion_id=criterion.id,
            criterion_name=criterion.name,
            status=DynamicStatus(status),
            summary=summary,
            missing_evidence=missing_evidence,
            violations=violations,
            requires_llm=True,
            llm_error=self._payload_error(payload) or None,
            generated_at=datetime.now(timezone.utc),
        )

    def _evaluate_rule_based(
        self,
        criterion: DynamicCriterion,
        evidence: dict[str, Any],
        evidence_packet: CriterionEvidencePacket,
    ) -> DynamicCriterionResult:
        status, summary, violations = self._rule_status(criterion, evidence)
        result = DynamicCriterionResult(
            criterion_id=criterion.id,
            criterion_name=criterion.name,
            status=status,
            summary=summary,
            violations=violations,
            requires_llm=False,
            generated_at=datetime.now(timezone.utc),
        )
        return self.status_router.route(criterion, result, evidence_packet)

    def _rule_status(
        self,
        criterion: DynamicCriterion,
        evidence: dict[str, Any],
    ) -> tuple[DynamicStatus, str, list[DynamicViolation]]:
        criterion_id = criterion.id

        focus_sequence = evidence.get("focus_sequence", []) if isinstance(evidence.get("focus_sequence"), list) else []
        keyboard_events = evidence.get("keyboard_events", []) if isinstance(evidence.get("keyboard_events"), list) else []
        focus_visible_trace = (
            evidence.get("focus_visible_trace", []) if isinstance(evidence.get("focus_visible_trace"), list) else []
        )

        unique_focus = sorted(
            {
                str(item.get("xpath"))
                for item in focus_sequence
                if isinstance(item, dict) and item.get("xpath") not in {None, "", "None"}
            }
        )
        tab_events = [event for event in keyboard_events if str(event.get("key", "")).lower() == "tab"]

        if criterion_id in {"2.1.1", "2.1.3"}:
            if tab_events and len(unique_focus) >= 2:
                return DynamicStatus.pass_status, "Keyboard navigation evidence shows focus can move between controls.", []
            if keyboard_events and len(unique_focus) <= 1:
                return (
                    DynamicStatus.fail,
                    "Keyboard events were observed but focus progression is limited, suggesting keyboard inaccessibility.",
                    [
                        DynamicViolation(
                            title="Limited focus progression",
                            summary="Tab/keyboard usage did not produce enough distinct focus targets.",
                            evidence_refs=["focus_sequence", "keyboard_events"],
                        )
                    ],
                )
            return DynamicStatus.warning, "Keyboard support could not be conclusively verified from captured steps.", []

        if criterion_id == "2.1.2":
            if tab_events and len(unique_focus) <= 1 and len(focus_sequence) >= 2:
                return (
                    DynamicStatus.fail,
                    "Potential keyboard trap detected: focus did not advance to new targets.",
                    [
                        DynamicViolation(
                            title="Potential keyboard trap",
                            summary="Repeated focus target during Tab traversal.",
                            evidence_refs=["focus_sequence"],
                        )
                    ],
                )
            if len(unique_focus) >= 2:
                return DynamicStatus.pass_status, "Focus moved across multiple targets; no keyboard trap signal observed.", []
            return DynamicStatus.warning, "Keyboard trap analysis is inconclusive with current trace length.", []

        if criterion_id == "2.1.4":
            shortcuts = evidence.get("character_key_shortcuts", [])
            if isinstance(shortcuts, list) and shortcuts:
                return (
                    DynamicStatus.warning,
                    "Character-key shortcuts were observed; verify a disable/remap or active-on-focus-only mechanism.",
                    [],
                )
            return DynamicStatus.not_applicable, "No character-key shortcuts were observed.", []

        if criterion_id == "1.4.13":
            revealed = evidence.get("hover_focus_revealed_content", [])
            if isinstance(revealed, list):
                non_dismissible = [
                    item for item in revealed if isinstance(item, dict) and item.get("has_dismiss_control") is False
                ]
                if non_dismissible:
                    return (
                        DynamicStatus.fail,
                        "Hover/focus-revealed content was observed without a dismiss control.",
                        [
                            DynamicViolation(
                                title="Content on hover or focus is not dismissible",
                                summary="Revealed hover/focus content lacks evidence of a close or dismiss mechanism.",
                                evidence_refs=["hover_focus_revealed_content"],
                            )
                        ],
                    )
                if revealed:
                    return DynamicStatus.pass_status, "Hover/focus-revealed content includes dismiss controls.", []
            return DynamicStatus.not_applicable, "No hover/focus-revealed content was observed.", []

        if criterion_id == "2.2.6":
            timing_snapshots = evidence.get("timing_snapshots", [])
            timeout_warnings = evidence.get("timeout_warnings", [])
            if isinstance(timing_snapshots, list) and timing_snapshots:
                missing_control = any(
                    isinstance(item, dict)
                    and isinstance(item.get("timing_info"), dict)
                    and item["timing_info"].get("user_extend_control") is False
                    for item in timing_snapshots
                )
                has_extend_control = any(
                    isinstance(item, dict)
                    and isinstance(item.get("timing_info"), dict)
                    and item["timing_info"].get("user_extend_control") is True
                    for item in timing_snapshots
                )
                if missing_control and not timeout_warnings:
                    return (
                        DynamicStatus.fail,
                        "A timeout was observed without evidence of a warning or extension control.",
                        [
                            DynamicViolation(
                                title="Timeout without warning or extension",
                                summary="Runtime timing evidence shows a deadline and no user extension control.",
                                evidence_refs=["timing_snapshots"],
                            )
                        ],
                    )
                if timeout_warnings and has_extend_control:
                    return DynamicStatus.pass_status, "Timeout warning and user extension control were observed.", []
                return DynamicStatus.warning, "Timeout evidence requires review of warning and extension behavior.", []
            return DynamicStatus.not_applicable, "No timeout evidence was observed.", []

        if criterion_id == "2.4.3":
            modal_escape = self._focus_escaped_active_dialog(focus_sequence)
            if modal_escape:
                return (
                    DynamicStatus.fail,
                    "Focus moved from an active dialog to background page content.",
                    [
                        DynamicViolation(
                            title="Modal focus escape",
                            summary="Focus sequence includes a dialog control followed by background-page focus.",
                            evidence_refs=["focus_sequence"],
                        )
                    ],
                )

            out_of_order = self._looks_like_out_of_order_form_focus(focus_sequence)
            if out_of_order:
                return (
                    DynamicStatus.fail,
                    "Focus order does not follow the form control sequence.",
                    [
                        DynamicViolation(
                            title="Illogical focus order",
                            summary="Focus moved to a form action before a later form input in the same sequence.",
                            evidence_refs=["focus_sequence"],
                        )
                    ],
                )

            if len(unique_focus) >= 2:
                return DynamicStatus.pass_status, "Focusable order was captured across multiple elements.", []
            return DynamicStatus.warning, "Insufficient focus transitions to establish reliable focus order.", []

        if criterion_id in {"2.4.7", "2.4.13"}:
            has_false = any(isinstance(item, dict) and item.get("has_visible_indicator") is False for item in focus_visible_trace)
            has_true = any(isinstance(item, dict) and item.get("has_visible_indicator") is True for item in focus_visible_trace)
            if has_false:
                return (
                    DynamicStatus.fail,
                    "At least one focused element appears to lack a visible focus indicator.",
                    [
                        DynamicViolation(
                            title="Potential invisible focus",
                            summary="Focus trace includes entries without visible focus styling hints.",
                            evidence_refs=["focus_visible_trace"],
                        )
                    ],
                )
            if has_true:
                return DynamicStatus.pass_status, "Focus styling indicators were observed on focused elements.", []
            return DynamicStatus.warning, "Focus visibility could not be validated with available styling evidence.", []

        if criterion_id in {"2.4.11", "2.4.12"}:
            obscured = evidence.get("focus_obscured_trace", [])
            if isinstance(obscured, list) and obscured:
                return DynamicStatus.warning, "Focus obscuration evidence requires manual visual confirmation.", []
            return DynamicStatus.warning, "No direct obscuration measurements were captured for focused elements.", []

        if criterion_id == "3.2.1":
            changes = evidence.get("focus_change_events", [])
            if isinstance(changes, list) and len(changes) >= 2:
                return DynamicStatus.warning, "Focus transitions observed; verify no unexpected context changes on focus.", []
            return DynamicStatus.warning, "Insufficient focus-change evidence to verify On Focus behavior.", []

        if criterion_id in {"3.3.1", "3.3.3"}:
            errors = evidence.get("error_messages", [])
            if isinstance(errors, list) and errors:
                has_semantics = bool(evidence.get("has_error_semantics"))
                if criterion_id == "3.3.1" and not has_semantics:
                    return (
                        DynamicStatus.fail,
                        "Form error content was observed without programmatic error semantics.",
                        [
                            DynamicViolation(
                                title="Form error not programmatically identified",
                                summary="Error text is present but no aria-invalid, aria-describedby, alert, or live-region semantics were observed.",
                                evidence_refs=["form_snapshots", "error_messages"],
                            )
                        ],
                    )
                return DynamicStatus.warning, "Error-related content observed; verify text clarity and suggestions.", []
            return DynamicStatus.not_applicable, "No form error state observed in this runtime trace.", []

        if criterion_id == "4.1.3":
            status_messages = evidence.get("status_message_mutations", [])
            if isinstance(status_messages, list) and status_messages:
                if not evidence.get("live_region_presence"):
                    return (
                        DynamicStatus.fail,
                        "A status message was inserted without live-region semantics.",
                        [
                            DynamicViolation(
                                title="Status message is not programmatically announced",
                                summary="Runtime mutation evidence shows a status update, but no role=status, role=alert, aria-live, log, or progress semantics were observed.",
                                evidence_refs=["status_message_mutations"],
                            )
                        ],
                    )
                return DynamicStatus.pass_status, "Status message activity is associated with live-region semantics.", []
            return DynamicStatus.not_applicable, "No runtime status messages were observed.", []

        if criterion_id in {"2.3.1", "2.3.2"}:
            flashes = evidence.get("flash_events", [])
            if isinstance(flashes, list) and flashes:
                if all(isinstance(item, dict) and item.get("below_threshold") is True for item in flashes):
                    return DynamicStatus.pass_status, "Observed flashing signals are marked below threshold.", []
                return DynamicStatus.fail, "Potential flashing content signals were detected in runtime evidence.", []
            return DynamicStatus.not_applicable, "No flashing signals were observed.", []

        if criterion_id == "2.5.2":
            pointer_handlers = evidence.get("pointer_handlers", [])
            cancellation = evidence.get("pointer_cancellation_indicators", [])
            if isinstance(pointer_handlers, list) and pointer_handlers:
                if isinstance(cancellation, list) and cancellation:
                    return DynamicStatus.pass_status, "Pointer cancellation or pointer-up evidence was observed.", []
                return (
                    DynamicStatus.fail,
                    "Pointer handlers were observed without cancellation or pointer-up evidence.",
                    [
                        DynamicViolation(
                            title="Pointer cancellation evidence missing",
                            summary="A custom pointer interaction was captured without pointer cancellation or pointer-up safeguards.",
                            evidence_refs=["pointer_handlers"],
                        )
                    ],
                )
            return DynamicStatus.not_applicable, "No pointer handlers were observed.", []

        if criterion_id == "2.5.7":
            dragging = evidence.get("dragging_indicators", [])
            if isinstance(dragging, list) and dragging:
                missing_alternative = [
                    item
                    for item in dragging
                    if isinstance(item, dict) and item.get("has_keyboard_alternative") is not True
                ]
                if missing_alternative:
                    return (
                        DynamicStatus.fail,
                        "Dragging movement was observed without a keyboard or non-drag alternative.",
                        [
                            DynamicViolation(
                                title="Drag-only movement",
                                summary="The runtime evidence includes draggable controls without alternative movement controls.",
                                evidence_refs=["dragging_indicators"],
                            )
                        ],
                    )
                return DynamicStatus.pass_status, "Dragging movement has keyboard alternative evidence.", []
            return DynamicStatus.not_applicable, "No dragging movement indicators were observed.", []

        if criterion.probe == "media_probe":
            caption_tracks = evidence.get("caption_tracks", [])
            transcript_indicators = evidence.get("transcript_indicators", [])
            if criterion_id in {"1.2.2", "1.2.4"}:
                if isinstance(caption_tracks, list) and caption_tracks:
                    return DynamicStatus.pass_status, "Caption track indicators were detected.", []
                return DynamicStatus.fail, "Media detected without clear caption track indicators.", []
            if criterion_id in {"1.2.3", "1.2.5", "1.2.6", "1.2.7", "1.2.8"}:
                if isinstance(transcript_indicators, list) and transcript_indicators:
                    return DynamicStatus.pass_status, "Transcript/audio-description indicators were detected.", []
                return DynamicStatus.warning, "No explicit media alternatives were detected from runtime evidence.", []
            return DynamicStatus.warning, "Media criterion requires deeper content-level review.", []

        return DynamicStatus.warning, "Criterion requires additional targeted interaction evidence.", []

    def _has_deterministic_rule(self, criterion: DynamicCriterion) -> bool:
        return criterion.id in {"1.2.2", "1.4.13", "2.2.6"}

    def _focus_escaped_active_dialog(self, focus_sequence: list[dict[str, Any]]) -> bool:
        saw_dialog_focus = False
        for item in focus_sequence:
            if not isinstance(item, dict):
                continue
            xpath = str(item.get("xpath") or "").lower()
            role = str(item.get("role") or "").lower()
            in_dialog = "dialog" in xpath or role == "dialog"
            if in_dialog:
                saw_dialog_focus = True
                continue
            if saw_dialog_focus and xpath and "dialog" not in xpath:
                return True
        return False

    def _looks_like_out_of_order_form_focus(self, focus_sequence: list[dict[str, Any]]) -> bool:
        form_controls: list[tuple[str, int]] = []
        for item in focus_sequence:
            if not isinstance(item, dict):
                continue
            xpath = str(item.get("xpath") or "").lower()
            if "/form/" not in xpath:
                continue
            if "/button" in xpath:
                form_controls.append(("button", 0))
                continue
            input_index = self._xpath_index(xpath, "input")
            if input_index is not None:
                form_controls.append(("input", input_index))

        if len(form_controls) < 3:
            return False

        seen_button = False
        highest_input_before_button = 0
        for kind, index in form_controls:
            if kind == "button":
                seen_button = True
                continue
            if not seen_button:
                highest_input_before_button = max(highest_input_before_button, index)
                continue
            if index > highest_input_before_button:
                return True
        return False

    def _xpath_index(self, xpath: str, element_name: str) -> int | None:
        token = f"/{element_name}["
        if token not in xpath:
            return None
        try:
            return int(xpath.rsplit(token, 1)[1].split("]", 1)[0])
        except (IndexError, ValueError):
            return None

    def _is_not_applicable(self, criterion: DynamicCriterion, evidence: dict[str, Any]) -> bool:
        if criterion.probe == "media_probe":
            media = evidence.get("media_elements")
            return isinstance(media, list) and len(media) == 0

        if criterion.probe == "timing_interruption_probe":
            has_timeout = isinstance(evidence.get("timeout_warnings"), list) and bool(evidence.get("timeout_warnings"))
            has_interruptions = isinstance(evidence.get("interruption_signals"), list) and bool(
                evidence.get("interruption_signals")
            )
            has_reauth = isinstance(evidence.get("reauthentication_events"), list) and bool(
                evidence.get("reauthentication_events")
            )
            has_timing_limits = isinstance(evidence.get("timing_snapshots"), list) and bool(evidence.get("timing_snapshots"))
            return not any([has_timeout, has_interruptions, has_reauth, has_timing_limits])

        if criterion.probe == "animation_flashing_probe":
            if criterion.id == "2.3.3":
                interactions = evidence.get("interaction_animation_events")
                return isinstance(interactions, list) and len(interactions) == 0
            flashes = evidence.get("flash_events")
            return isinstance(flashes, list) and len(flashes) == 0

        if criterion.probe == "forms_errors_auth_probe":
            if criterion.id in {"3.3.8", "3.3.9"}:
                auth = evidence.get("auth_snapshots")
                return isinstance(auth, list) and len(auth) == 0
            forms = evidence.get("form_snapshots")
            return isinstance(forms, list) and len(forms) == 0

        if criterion.probe == "consistency_navigation_probe":
            url_sequence = evidence.get("url_sequence")
            return isinstance(url_sequence, list) and len(url_sequence) < 2

        if criterion.probe == "pointer_gesture_probe":
            pointer_features = [
                evidence.get("pointer_handlers"),
                evidence.get("dragging_indicators"),
                evidence.get("pointer_cancellation_indicators"),
                evidence.get("motion_actuation_indicators"),
                evidence.get("concurrent_input_indicators"),
            ]
            return all(isinstance(items, list) and len(items) == 0 for items in pointer_features)

        if criterion.probe == "hover_focus_content_probe":
            hover = evidence.get("hover_focus_revealed_content")
            return isinstance(hover, list) and len(hover) == 0

        if criterion.probe == "status_message_probe":
            messages = evidence.get("status_message_mutations")
            return isinstance(messages, list) and len(messages) == 0

        return False

    def _parse_json_payload(self, text: str) -> dict[str, Any]:
        try:
            payload = json.loads(text)
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    payload = json.loads(text[start : end + 1])
                    return payload if isinstance(payload, dict) else {}
                except json.JSONDecodeError:
                    return {}
        return {}


__all__ = ["DynamicModelClient", "DynamicWCAGAnalyzer", "PromptLoader"]
