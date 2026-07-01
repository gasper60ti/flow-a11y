from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from dynamic.schemas.wcag_dynamic import CriterionEvidencePacket, DynamicCriterion, DynamicCriterionResult, DynamicStatus


DecisionLevel = Literal[
    "confirmed_fail",
    "probable_violation",
    "weak_signal",
    "insufficient_evidence",
    "not_applicable",
]


@dataclass(frozen=True)
class StatusRoutingDecision:
    status: DynamicStatus
    decision_level: DecisionLevel | None
    reason: str


class CalibratedStatusRouter:
    """Map packet-calibrated internal decision levels to existing report statuses."""

    def route(
        self,
        criterion: DynamicCriterion,
        result: DynamicCriterionResult,
        packet: CriterionEvidencePacket,
    ) -> DynamicCriterionResult:
        decision = self.decision_for(criterion, result, packet)
        if decision.status == result.status and result.decision_level == decision.decision_level and result.evidence_packet == packet:
            return result

        summary = result.summary
        violations = result.violations
        missing_evidence = list(result.missing_evidence)
        if decision.status != result.status and decision.decision_level in {"not_applicable", "insufficient_evidence"}:
            summary = decision.reason
            violations = []
            if decision.decision_level == "insufficient_evidence":
                missing_evidence = _dedupe([*missing_evidence, *packet.missing_evidence])

        return result.model_copy(
            update={
                "status": decision.status,
                "summary": summary,
                "violations": violations,
                "missing_evidence": missing_evidence,
                "decision_level": decision.decision_level,
                "evidence_packet": packet,
            }
        )

    def decision_for(
        self,
        criterion: DynamicCriterion,
        result: DynamicCriterionResult,
        packet: CriterionEvidencePacket,
    ) -> StatusRoutingDecision:
        if packet.applicability == "not_applicable":
            return StatusRoutingDecision(
                status=DynamicStatus.not_applicable,
                decision_level="not_applicable",
                reason=packet.applicability_reason,
            )

        if packet.applicability == "insufficient_evidence":
            if _has_actionable_warning_signal(packet):
                return StatusRoutingDecision(
                    status=DynamicStatus.warning,
                    decision_level="weak_signal",
                    reason=packet.applicability_reason,
                )
            return StatusRoutingDecision(
                status=DynamicStatus.incomplete,
                decision_level="insufficient_evidence",
                reason=packet.applicability_reason,
            )

        if result.status == DynamicStatus.fail:
            if self._is_confirmed_fail(criterion, result, packet):
                return StatusRoutingDecision(
                    status=DynamicStatus.fail,
                    decision_level="confirmed_fail",
                    reason="Fail is supported by direct criterion-specific runtime evidence.",
                )
            if packet.evidence_strength in {"direct", "behavioral_direct"}:
                return StatusRoutingDecision(
                    status=DynamicStatus.warning,
                    decision_level="probable_violation",
                    reason="Runtime evidence is direct, but final confirmation is limited by validation or contradictions.",
                )
            return StatusRoutingDecision(
                status=DynamicStatus.warning,
                decision_level="weak_signal",
                reason="Fail was reduced to a visible warning because direct confirmation is missing.",
            )

        if result.status == DynamicStatus.warning:
            if (
                result.fail_validation is not None
                and result.fail_validation.original_status == DynamicStatus.fail.value
                and self._is_recoverable_validator_warning(criterion, packet)
            ):
                return StatusRoutingDecision(
                    status=DynamicStatus.fail,
                    decision_level="confirmed_fail",
                    reason="Validator-downgraded fail was recovered by direct criterion-specific runtime evidence.",
                )
            if (
                packet.evidence_strength in {"indirect", "insufficient"}
                and not _has_actionable_warning_signal(packet)
            ):
                return StatusRoutingDecision(
                    status=DynamicStatus.incomplete,
                    decision_level="insufficient_evidence",
                    reason="The warning lacks an actionable criterion-specific runtime signal.",
                )
            level: DecisionLevel = "probable_violation" if packet.evidence_strength in {"direct", "behavioral_direct"} else "weak_signal"
            return StatusRoutingDecision(status=DynamicStatus.warning, decision_level=level, reason=result.summary)

        if result.status == DynamicStatus.pass_status and self._should_surface_pass_as_warning(criterion, packet):
            return StatusRoutingDecision(
                status=DynamicStatus.warning,
                decision_level="probable_violation",
                reason="Deterministic criterion packet contains behavioral-direct runtime risk despite a pass judgment.",
            )

        if result.status == DynamicStatus.incomplete:
            return StatusRoutingDecision(
                status=DynamicStatus.incomplete,
                decision_level="insufficient_evidence",
                reason=result.summary,
            )

        if result.status == DynamicStatus.not_applicable:
            return StatusRoutingDecision(
                status=DynamicStatus.not_applicable,
                decision_level="not_applicable",
                reason=result.summary,
            )

        return StatusRoutingDecision(status=result.status, decision_level=None, reason=result.summary)

    def _is_confirmed_fail(
        self,
        criterion: DynamicCriterion,
        result: DynamicCriterionResult,
        packet: CriterionEvidencePacket,
    ) -> bool:
        facts = packet.compact_runtime_facts
        if packet.evidence_strength == "contradicted":
            return False

        if not result.requires_llm and result.fail_validation is None:
            if packet.criterion_family == "other":
                return packet.evidence_strength == "direct" or bool(packet.positive_evidence)
            if criterion.id == "2.4.3":
                return packet.evidence_strength in {"direct", "behavioral_direct"}

        if result.fail_validation is not None and result.fail_validation.validated_status == DynamicStatus.fail.value:
            return packet.evidence_strength in {"direct", "behavioral_direct"}

        if packet.evidence_strength == "behavioral_direct":
            return True

        if packet.criterion_family == "focus_keyboard":
            if criterion.id in {"2.1.1", "2.1.3"}:
                return bool(facts.get("repeated_keyboard_blocking")) or (
                    int(facts.get("missing_focus_indicator_without_screenshot_count") or 0) > 0
                    and int(facts.get("keyboard_event_count") or 0) > 0
                )
            if criterion.id == "2.1.2":
                return bool(facts.get("repeated_keyboard_blocking")) and bool(facts.get("terminal_repeated_focus"))
            if criterion.id == "2.4.3":
                return bool(facts.get("repeated_keyboard_blocking")) or bool(facts.get("terminal_repeated_focus"))
            if criterion.id in {"2.4.7", "2.4.13"}:
                return int(facts.get("direct_missing_focus_indicator_count") or 0) > 0

        if packet.criterion_family == "media":
            return int(facts.get("playing_relevant_media_count") or 0) > 0 and bool(packet.missing_evidence)

        if packet.criterion_family == "pointer":
            return (
                int(facts.get("observed_gesture_requirement_count") or 0) > 0
                or (
                    criterion.id == "2.5.2"
                    and bool(facts.get("observed_pointer_cancellation_failure"))
                )
            )

        if packet.criterion_family == "forms":
            if criterion.id in {"3.3.1", "3.3.3"}:
                return bool(facts.get("tied_error_feedback"))
            if criterion.id == "3.3.5":
                return int(facts.get("missing_contextual_help_count") or 0) > 0
            if criterion.id in {"3.3.4", "3.3.6"}:
                return int(facts.get("error_prevention_failure_count") or 0) > 0
            if criterion.id == "3.3.7":
                return int(facts.get("observed_redundant_entry_failure_count") or 0) > 0
            if criterion.id in {"3.3.8", "3.3.9"}:
                return int(facts.get("auth_barrier_failure_count") or 0) > 0
            return False

        return packet.evidence_strength == "direct"

    def _is_recoverable_validator_warning(
        self,
        criterion: DynamicCriterion,
        packet: CriterionEvidencePacket,
    ) -> bool:
        facts = packet.compact_runtime_facts
        if packet.criterion_family != "focus_keyboard":
            return False
        if packet.evidence_strength == "contradicted":
            return False
        if criterion.id == "2.1.1":
            return (
                bool(facts.get("terminal_repeated_focus"))
                and int(facts.get("keyboard_navigation_event_count") or 0) > 0
                and int(facts.get("body_html_focus_count") or 0) >= 2
            )
        if criterion.id == "2.1.3":
            return (
                int(facts.get("body_html_focus_count") or 0) >= 6
                and int(facts.get("visible_focus_indicator_count") or 0) <= 1
                and int(facts.get("false_focus_indicator_count") or 0) >= 5
                and int(facts.get("focused_interactive_count") or 0) <= 2
                and int(facts.get("keyboard_event_count") or 0) > 0
            )
        if criterion.id in {"2.4.7", "2.4.13"}:
            missing_count = int(facts.get("strong_computed_missing_focus_indicator_count") or 0)
            contradiction_count = int(facts.get("focus_indicator_contradiction_count") or 0)
            return (
                missing_count >= 2
                and int(facts.get("focused_interactive_count") or 0) >= 2
                and int(facts.get("keyboard_event_count") or 0) > 0
                and contradiction_count < missing_count
            )
        return False

    def _should_surface_pass_as_warning(
        self,
        criterion: DynamicCriterion,
        packet: CriterionEvidencePacket,
    ) -> bool:
        if packet.criterion_family != "focus_keyboard":
            return False
        if packet.evidence_strength != "behavioral_direct":
            return False
        facts = packet.compact_runtime_facts
        if criterion.id in {"2.1.1", "2.1.2", "2.1.3", "2.4.3"}:
            return bool(facts.get("repeated_keyboard_blocking")) or bool(facts.get("terminal_repeated_focus"))
        return False


def status_for_pre_llm_packet(packet: CriterionEvidencePacket) -> tuple[DynamicStatus, DecisionLevel]:
    if packet.applicability == "not_applicable":
        return DynamicStatus.not_applicable, "not_applicable"
    if packet.applicability == "insufficient_evidence":
        if _has_actionable_warning_signal(packet):
            return DynamicStatus.warning, "weak_signal"
        return DynamicStatus.incomplete, "insufficient_evidence"
    return DynamicStatus.warning, "weak_signal"


def _has_actionable_warning_signal(packet: CriterionEvidencePacket) -> bool:
    facts = packet.compact_runtime_facts
    if packet.criterion_family == "focus_keyboard":
        navigation_count = int(facts.get("keyboard_navigation_event_count") or 0)
        repeated = bool(facts.get("repeated_keyboard_blocking")) or bool(
            facts.get("terminal_repeated_focus")
        )
        missing_focus = int(
            facts.get("strong_computed_missing_focus_indicator_count")
            or facts.get("missing_focus_indicator_without_screenshot_count")
            or 0
        )
        if packet.criterion_id in {"2.4.7", "2.4.13"}:
            return navigation_count > 0 and (
                missing_focus > 0
                or int(facts.get("focus_indicator_contradiction_count") or 0) > 0
                or repeated
            )
        if packet.criterion_id == "2.4.3":
            return repeated or (
                navigation_count > 0
                and int(facts.get("focused_interactive_count") or 0) > 0
            )
        if packet.criterion_id in {"2.1.1", "2.1.2", "2.1.3"}:
            return repeated or (navigation_count > 0 and missing_focus > 0)
        return navigation_count > 0 and int(facts.get("focused_interactive_count") or 0) > 0
    if packet.criterion_family == "forms":
        if packet.criterion_id in {"3.3.1", "3.3.3"}:
            return bool(facts.get("real_form_action")) and bool(facts.get("tied_error_feedback"))
        if packet.criterion_id == "3.3.5":
            return (
                int(facts.get("contextual_help_check_count") or 0) > 0
                or int(facts.get("missing_contextual_help_count") or 0) > 0
            )
        if packet.criterion_id in {"3.3.4", "3.3.6"}:
            return int(facts.get("error_prevention_failure_count") or 0) > 0
        if packet.criterion_id == "3.3.7":
            return (
                int(facts.get("redundant_entry_candidate_count") or 0) > 0
                or int(facts.get("observed_redundant_entry_failure_count") or 0) > 0
            )
        if packet.criterion_id in {"3.3.8", "3.3.9"}:
            return (
                int(facts.get("observed_auth_event_count") or 0) > 0
                or int(facts.get("auth_barrier_failure_count") or 0) > 0
            )
        return False
    if packet.criterion_family == "pointer":
        return (
            int(facts.get("observed_gesture_requirement_count") or 0) > 0
            or bool(facts.get("observed_pointer_cancellation_failure"))
        )
    if packet.criterion_family == "media":
        return int(facts.get("playing_relevant_media_count") or 0) > 0
    return packet.evidence_strength in {"direct", "behavioral_direct", "contradicted"}


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


__all__ = ["CalibratedStatusRouter", "DecisionLevel", "StatusRoutingDecision", "status_for_pre_llm_packet"]
