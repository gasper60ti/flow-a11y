from __future__ import annotations

from pathlib import Path

from dynamic.preprocessors.wcag_mapper import DYNAMIC_WCAG_REGISTRY, prompt_filename


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "dynamic" / "prompts"


REQUIRED_STATUS_TOKENS = ["pass", "fail", "warning", "not_applicable", "incomplete"]
REQUIRED_SCHEMA_FIELDS = ["criterion_id", "status", "summary", "missing_evidence", "violations", "evidence_refs"]
REQUIRED_CONTRACT_PHRASES = [
    "return strict json only",
    "do not invent evidence",
    "fail only with direct evidence",
    "evidence json is the only source of truth",
    "must include non-empty evidence_refs",
    "exact key or nested path present in the evidence json",
    "do not use generic page text, class names, script text",
    "downgrade weak keyword",
]
PROBE_GUIDANCE_PHRASES = {
    "media_probe": "do not fail unloaded, hidden, muted, or non-playing media",
    "keyboard_focus_probe": "do not fail focus appearance from body focus or missing inline styles alone",
    "animation_flashing_probe": "keyword-only evidence is not proof of flashing",
    "timing_interruption_probe": "script text or generic words like modal, alert, timeout, or session are not proof by themselves",
    "forms_errors_auth_probe": (
        "require observed submission, error mutation, status mutation, live region behavior, "
        "authentication challenge behavior"
    ),
    "status_message_probe": "require observed submission, error mutation, status mutation, live region behavior",
    "pointer_gesture_probe": "require observed pointer handlers or interaction traces, not just class names",
    "hover_focus_content_probe": "tooltip, popover, aria-describedby, or :hover text is a candidate signal",
    "consistency_navigation_probe": "require comparable navigation_snapshots",
}


def _prompt_text(criterion_id: str) -> str:
    return (PROMPTS_DIR / prompt_filename(criterion_id)).read_text(encoding="utf-8")


def test_prompt_file_exists_for_each_dynamic_criterion() -> None:
    for criterion_id in DYNAMIC_WCAG_REGISTRY.supported_ids():
        prompt_path = PROMPTS_DIR / prompt_filename(criterion_id)
        assert prompt_path.exists(), f"Missing prompt file for {criterion_id}"


def test_prompts_require_strict_json_statuses_and_evidence_contract() -> None:
    for criterion_id in DYNAMIC_WCAG_REGISTRY.supported_ids():
        criterion = DYNAMIC_WCAG_REGISTRY.get(criterion_id)
        prompt_text = _prompt_text(criterion_id)
        lower_text = prompt_text.lower()

        assert f"criterion {criterion_id}" in lower_text
        assert f"criterion id: {criterion_id}" in lower_text
        assert "criterion name:" in lower_text
        assert criterion.name in prompt_text
        assert "{{EVIDENCE}}" in prompt_text

        for phrase in REQUIRED_CONTRACT_PHRASES:
            assert phrase in lower_text, f"Missing prompt contract phrase for {criterion_id}: {phrase}"
        for field in REQUIRED_SCHEMA_FIELDS:
            assert f"\"{field}\"" in prompt_text
        for status in REQUIRED_STATUS_TOKENS:
            assert status in prompt_text


def test_prompts_include_probe_family_guardrails() -> None:
    for criterion_id in DYNAMIC_WCAG_REGISTRY.supported_ids():
        criterion = DYNAMIC_WCAG_REGISTRY.get(criterion_id)
        prompt_text = _prompt_text(criterion_id).lower()
        expected_phrase = PROBE_GUIDANCE_PHRASES[criterion.probe]
        assert expected_phrase in prompt_text, f"Missing {criterion.probe} guidance in {criterion_id}"
