from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dynamic.schemas.wcag_dynamic import DynamicCriterion


ApplicabilityValue = Literal["applicable", "not_applicable_candidate", "uncertain"]
EvidenceStrengthValue = Literal["strong", "moderate", "weak", "absent"]


class EvidenceQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    probe: str
    applicability: ApplicabilityValue
    evidence_strength: EvidenceStrengthValue
    decisive_evidence_refs: list[str] = Field(default_factory=list)
    candidate_evidence_refs: list[str] = Field(default_factory=list)
    weak_signal_refs: list[str] = Field(default_factory=list)
    recommended_status_floor: str | None = None
    recommended_status_ceiling: str | None = None
    reasons: list[str] = Field(default_factory=list)


class EvidenceQualityAssessor:
    """Build advisory evidence quality metadata without changing result status."""

    def assess(self, criterion: DynamicCriterion, evidence: dict[str, Any]) -> EvidenceQualitySummary:
        decisive_refs: list[str] = []
        candidate_refs: list[str] = []
        moderate_refs: list[str] = []
        weak_refs: list[str] = []
        reasons: list[str] = []

        for key, value in evidence.items():
            if key == "evidence_quality_summary":
                continue
            if isinstance(value, list):
                for index, item in enumerate(value):
                    ref = f"{key}[{index}]"
                    quality, decisive = self._item_quality(item)
                    if decisive:
                        decisive_refs.append(ref)
                    elif quality in {"observed", "computed", "event_listener"}:
                        candidate_refs.append(ref)
                        moderate_refs.append(ref)
                    elif quality == "static_candidate":
                        candidate_refs.append(ref)
                    elif quality == "weak_keyword":
                        weak_refs.append(ref)
                    elif self._has_real_value(item):
                        candidate_refs.append(ref)
            elif self._has_real_value(value):
                candidate_refs.append(key)

        decisive_refs = self._dedupe(decisive_refs)
        candidate_refs = self._dedupe(candidate_refs)
        moderate_refs = self._dedupe(moderate_refs)
        weak_refs = self._dedupe(weak_refs)

        evidence_strength = self._strength(decisive_refs, moderate_refs, candidate_refs, weak_refs)
        applicability = self._applicability(evidence_strength)
        if decisive_refs:
            reasons.append("direct observed/computed evidence is available")
        if candidate_refs:
            reasons.append("candidate evidence is available for model review")
        if weak_refs:
            reasons.append("weak keyword/static evidence should be treated cautiously")
        if evidence_strength == "absent":
            reasons.append("no non-empty evidence items were found")

        return EvidenceQualitySummary(
            criterion_id=criterion.id,
            probe=criterion.probe,
            applicability=applicability,
            evidence_strength=evidence_strength,
            decisive_evidence_refs=decisive_refs,
            candidate_evidence_refs=candidate_refs,
            weak_signal_refs=weak_refs,
            recommended_status_floor=None,
            recommended_status_ceiling="warning" if evidence_strength == "weak" else None,
            reasons=reasons,
        )

    def _item_quality(self, item: Any) -> tuple[str, bool]:
        if not isinstance(item, dict):
            return ("static_candidate" if self._has_real_value(item) else "weak_keyword", False)
        quality = str(item.get("quality") or "").strip()
        source = str(item.get("source") or "").strip()
        decisive = bool(item.get("decisive"))
        if quality:
            return quality, decisive
        if source in {"runtime_event", "computed_style", "media_api", "mutation_observer", "accessibility_tree"}:
            return "observed", decisive
        if source == "event_listener":
            return "event_listener", decisive
        if source == "dom_token":
            return "static_candidate", decisive
        if source == "text_keyword":
            return "weak_keyword", decisive
        return ("static_candidate" if item else "weak_keyword", decisive)

    def _strength(
        self,
        decisive_refs: list[str],
        moderate_refs: list[str],
        candidate_refs: list[str],
        weak_refs: list[str],
    ) -> EvidenceStrengthValue:
        if decisive_refs:
            return "strong"
        if moderate_refs:
            return "moderate"
        if candidate_refs:
            return "weak"
        if weak_refs:
            return "weak"
        return "absent"

    def _applicability(self, evidence_strength: EvidenceStrengthValue) -> ApplicabilityValue:
        if evidence_strength in {"strong", "moderate"}:
            return "applicable"
        if evidence_strength == "weak":
            return "uncertain"
        return "not_applicable_candidate"

    def _has_real_value(self, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, dict, tuple, set)):
            return bool(value)
        return True

    def _dedupe(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
        return ordered


__all__ = ["EvidenceQualityAssessor", "EvidenceQualitySummary"]
