from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_SEGMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\[[0-9]+\])*$")
_INDEX_RE = re.compile(r"\[([0-9]+)\]")


@dataclass(frozen=True)
class EvidenceReferenceResolution:
    ref: str
    resolved: bool
    value: Any = None
    reason: str = ""


def resolve_evidence_ref(
    evidence: dict[str, Any],
    ref: str,
    *,
    allow_empty: bool = False,
) -> EvidenceReferenceResolution:
    """Resolve evidence refs such as focus_sequence[0].xpath against evidence JSON."""

    ref = str(ref or "").strip()
    if not ref:
        return EvidenceReferenceResolution(ref=ref, resolved=False, reason="empty_ref")

    current: Any = evidence
    for segment in ref.split("."):
        if not segment:
            return EvidenceReferenceResolution(ref=ref, resolved=False, reason="empty_path_segment")
        match = _SEGMENT_RE.match(segment)
        if match is None:
            return EvidenceReferenceResolution(ref=ref, resolved=False, reason=f"invalid_segment:{segment}")

        key = match.group(1)
        if not isinstance(current, dict):
            return EvidenceReferenceResolution(ref=ref, resolved=False, reason=f"not_a_dict:{key}")
        if key not in current:
            return EvidenceReferenceResolution(ref=ref, resolved=False, reason=f"missing_key:{key}")
        current = current[key]

        for raw_index in _INDEX_RE.findall(segment):
            index = int(raw_index)
            if not isinstance(current, list):
                return EvidenceReferenceResolution(ref=ref, resolved=False, reason=f"not_a_list:{key}")
            if index >= len(current):
                return EvidenceReferenceResolution(ref=ref, resolved=False, reason=f"index_out_of_range:{key}[{index}]")
            current = current[index]

    if not allow_empty and not _has_real_value(current):
        return EvidenceReferenceResolution(ref=ref, resolved=False, value=current, reason="empty_value")
    return EvidenceReferenceResolution(ref=ref, resolved=True, value=current)


def canonicalize_evidence_ref(evidence: dict[str, Any], ref: str) -> str:
    """Normalize common model aliases to exact paths in the exported evidence JSON."""

    ref = str(ref or "").strip()
    if not ref:
        return ref
    if resolve_evidence_ref(evidence, ref, allow_empty=True).resolved:
        return ref

    candidates: list[str] = []
    if ref == "compact_runtime_facts" or ref.startswith("compact_runtime_facts."):
        candidates.append(f"criterion_evidence_packet.{ref}")
    if ref == "focus_entries" or ref.startswith("focus_entries[") or ref.startswith("focus_entries."):
        candidates.append(f"criterion_evidence_packet.compact_runtime_facts.{ref}")
    if ref.split(".", 1)[0] in {
        "applicability",
        "applicability_reason",
        "contradictory_evidence",
        "evidence_strength",
        "missing_evidence",
        "positive_evidence",
        "step_consistency",
    }:
        candidates.append(f"criterion_evidence_packet.{ref}")

    focus_indicator_match = re.fullmatch(r"focus_sequence(\[[0-9]+\])\.has_visible_indicator", ref)
    if focus_indicator_match:
        candidates.append(f"focus_visible_trace{focus_indicator_match.group(1)}.has_visible_indicator")

    focus_body_match = re.fullmatch(r"focus_visible_trace(\[[0-9]+\])\.is_body_html", ref)
    if focus_body_match:
        candidates.append(
            "criterion_evidence_packet.compact_runtime_facts."
            f"focus_entries{focus_body_match.group(1)}.is_body_html"
        )

    media_support_match = re.fullmatch(
        r"media_elements\[[0-9]+\]\.(caption_tracks|captions_or_subtitles|text_tracks|tracks)",
        ref,
    )
    if media_support_match:
        candidates.append("caption_tracks")

    for candidate in candidates:
        if resolve_evidence_ref(evidence, candidate, allow_empty=True).resolved:
            return candidate
    return ref


def canonicalize_evidence_refs(
    evidence: dict[str, Any],
    refs: list[str],
) -> tuple[list[str], list[dict[str, str]]]:
    """Canonicalize refs and discard isolated unresolved refs when valid refs remain."""

    valid_refs: list[str] = []
    unresolved_refs: list[str] = []
    audit: list[dict[str, str]] = []
    for raw_ref in refs:
        original = str(raw_ref or "").strip()
        canonical = canonicalize_evidence_ref(evidence, original)
        if canonical != original:
            audit.append({"original": original, "canonical": canonical})
        resolution = resolve_evidence_ref(evidence, canonical, allow_empty=True)
        if resolution.resolved:
            if canonical not in valid_refs:
                valid_refs.append(canonical)
        else:
            unresolved_refs.append(canonical)

    if valid_refs and unresolved_refs:
        audit.extend(
            {
                "original": unresolved,
                "canonical": "",
                "action": "dropped_unresolved",
            }
            for unresolved in unresolved_refs
        )
        return valid_refs, audit
    return valid_refs or unresolved_refs, audit


def _has_real_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


__all__ = [
    "EvidenceReferenceResolution",
    "canonicalize_evidence_ref",
    "canonicalize_evidence_refs",
    "resolve_evidence_ref",
]
