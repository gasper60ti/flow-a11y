from __future__ import annotations

from datetime import datetime, timezone

from dynamic.analyzers.fail_confidence_validator import FailConfidenceValidator
from dynamic.preprocessors.wcag_mapper import get_criterion
from dynamic.schemas.wcag_dynamic import DynamicCriterionResult, DynamicStatus, DynamicViolation


def _fail_result(criterion_id: str) -> DynamicCriterionResult:
    criterion = get_criterion(criterion_id)
    return DynamicCriterionResult(
        criterion_id=criterion.id,
        criterion_name=criterion.name,
        status=DynamicStatus.fail,
        summary="LLM predicted a failure.",
        violations=[
            DynamicViolation(
                title="Failure",
                summary="The model cited runtime evidence.",
                evidence_refs=["focus_visible_trace[0]"],
            )
        ],
        requires_llm=True,
        generated_at=datetime.now(timezone.utc),
    )


def _validate(criterion_id: str, evidence: dict) -> DynamicCriterionResult:
    return FailConfidenceValidator().validate(get_criterion(criterion_id), _fail_result(criterion_id), evidence)


def _direct_focus_evidence(*, rect: dict | None = None, screenshots: bool = True) -> dict:
    rect = rect if rect is not None else {"x": 10, "y": 20, "width": 120, "height": 32}
    return {
        "focus_sequence": [
            {
                "step": 1,
                "xpath": "/html/body/button[1]",
                "tag": "button",
                "bounding_client_rect": rect,
                "attributes": {},
            }
        ],
        "focus_visible_trace": [
            {
                "step": 1,
                "xpath": "/html/body/button[1]",
                "has_visible_indicator": False,
                "bounding_client_rect": rect,
                "quality": "computed",
                "source": "computed_style",
                "decisive": True,
            }
        ],
        "keyboard_events": [{"step": 1, "type": "keydown", "key": "Tab"}],
        "observed_focus_events": [{"step": 1, "type": "focus"}],
        "screenshot_paths": ["screenshot_step_1.png"] if screenshots else [],
    }


def test_focus_fail_preserved_for_direct_missing_indicator_on_interactive_element() -> None:
    result = _validate("2.4.7", _direct_focus_evidence())

    assert result.status == DynamicStatus.fail
    assert result.fail_validation is not None
    assert result.fail_validation.evidence_strength == "direct"
    assert result.fail_validation.validated_status == "fail"


def test_focus_fail_downgraded_when_focus_is_only_body_or_html() -> None:
    evidence = _direct_focus_evidence()
    evidence["focus_sequence"] = [{"step": 1, "xpath": "/html[1]/body[1]", "tag": "body"}]
    evidence["focus_visible_trace"][0]["xpath"] = "/html[1]/body[1]"
    evidence["focus_visible_trace"][0]["tag"] = "body"

    result = _validate("2.4.7", evidence)

    assert result.status == DynamicStatus.warning
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "body_html_focus_guard"


def test_focus_fail_preserved_without_screenshot_when_computed_style_is_strong() -> None:
    result = _validate("2.4.13", _direct_focus_evidence(screenshots=False))

    assert result.status == DynamicStatus.fail
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "focus_appearance_computed_style"


def test_focus_fail_downgraded_when_keyboard_event_is_from_different_step() -> None:
    evidence = _direct_focus_evidence()
    evidence["keyboard_events"][0]["step"] = 2

    result = _validate("2.4.7", evidence)

    assert result.status == DynamicStatus.warning
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "focus_appearance_keyboard_causality"


def test_keyboard_fail_preserved_for_repeated_keyboard_trap_pattern() -> None:
    evidence = {
        "focus_sequence": [
            {"step": 1, "xpath": "/html/body/button[1]", "tag": "button"},
            {"step": 2, "xpath": "/html/body/button[1]", "tag": "button"},
            {"step": 3, "xpath": "/html/body/button[1]", "tag": "button"},
        ],
        "keyboard_events": [
            {"step": 1, "type": "keydown", "key": "Tab"},
            {"step": 2, "type": "keydown", "key": "Tab"},
            {"step": 3, "type": "keydown", "key": "Tab"},
        ],
    }

    result = _validate("2.1.1", evidence)

    assert result.status == DynamicStatus.fail
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "keyboard_blocking_pattern"


def test_keyboard_trap_downgraded_when_later_focus_recovery_is_observed() -> None:
    evidence = {
        "focus_sequence": [
            {"step": 1, "xpath": "/html/body/dialog/button[1]", "tag": "button"},
            {"step": 2, "xpath": "/html/body/dialog/button[1]", "tag": "button"},
            {"step": 3, "xpath": "/html/body/button[1]", "tag": "button"},
        ],
        "keyboard_events": [
            {"step": 1, "type": "keydown", "key": "Tab"},
            {"step": 2, "type": "keydown", "key": "Escape"},
        ],
        "keyboard_focus_transitions": [
            {"step": 2, "key": "Escape", "focus_changed": False},
        ],
    }

    result = _validate("2.1.2", evidence)

    assert result.status == DynamicStatus.warning
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "keyboard_trap_recovered"


def test_keyboard_trap_preserved_when_repeated_attempts_never_recover() -> None:
    evidence = {
        "focus_sequence": [
            {"step": 1, "xpath": "/html/body/dialog/button[1]", "tag": "button"},
            {"step": 2, "xpath": "/html/body/dialog/button[1]", "tag": "button"},
            {"step": 3, "xpath": "/html/body/dialog/button[1]", "tag": "button"},
        ],
        "keyboard_events": [
            {"step": 1, "type": "keydown", "key": "Tab"},
            {"step": 2, "type": "keydown", "key": "Tab"},
            {"step": 3, "type": "keydown", "key": "Tab"},
        ],
    }

    result = _validate("2.1.2", evidence)

    assert result.status == DynamicStatus.fail
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "keyboard_trap_persistent"


def test_focus_fail_downgraded_for_zero_size_focused_element() -> None:
    evidence = _direct_focus_evidence(rect={"x": 10, "y": 20, "width": 0, "height": 32})

    result = _validate("2.4.7", evidence)

    assert result.status == DynamicStatus.warning
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "focus_appearance_direct_indicator"


def test_focus_fail_downgraded_for_parent_focus_within_indicator() -> None:
    evidence = _direct_focus_evidence()
    evidence["focus_visible_trace"][0]["visible_focus_signals"] = {
        "outline": False,
        "boxShadow": False,
        "focusRule": False,
        "ancestorFocusWithin": True,
    }
    evidence["focus_visible_trace"][0]["ancestor_focus_within"] = [
        {
            "selector": ".search-card:focus-within",
            "xpath": "/html/body/div[1]",
            "border": "2px solid rgb(0, 95, 204)",
        }
    ]

    result = _validate("2.4.7", evidence)

    assert result.status == DynamicStatus.warning
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "focus_appearance_direct_indicator"
    assert "contradict" in result.fail_validation.reason.lower()


def test_media_fail_downgraded_when_media_merely_exists() -> None:
    evidence = {
        "media_elements": [
            {
                "step": 1,
                "tag": "video",
                "paused": True,
                "muted": False,
                "current_time": 0,
                "current_time_delta": 0,
                "visible": True,
                "quality": "observed",
                "source": "media_api",
                "decisive": True,
            }
        ],
        "caption_tracks": [],
        "media_events": [],
    }

    result = _validate("1.2.2", evidence)

    assert result.status == DynamicStatus.warning
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "media_runtime_relevance"


def test_media_fail_preserved_when_playing_media_lacks_required_support() -> None:
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
                "quality": "observed",
                "source": "media_api",
                "decisive": True,
            }
        ],
        "caption_tracks": [],
        "media_events": [{"step": 1, "type": "timeupdate"}],
    }

    result = _validate("1.2.2", evidence)

    assert result.status == DynamicStatus.fail
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "media_runtime_relevance"


def test_media_fail_downgraded_for_visible_muted_autoplay_animation() -> None:
    evidence = {
        "media_elements": [
            {
                "step": 1,
                "tag": "video",
                "paused": False,
                "muted": True,
                "audible": False,
                "autoplay": True,
                "controls": False,
                "current_time": 4.0,
                "current_time_delta": 1.0,
                "visible": True,
                "quality": "observed",
                "source": "media_api",
                "decisive": True,
            }
        ],
        "caption_tracks": [],
        "media_events": [{"step": 1, "type": "timeupdate"}],
    }

    result = _validate("1.2.2", evidence)

    assert result.status == DynamicStatus.warning
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "media_runtime_relevance"


def test_pointer_fail_downgraded_for_listener_only_evidence() -> None:
    evidence = {
        "pointer_handlers": [
            {
                "step": 1,
                "event_type": "click",
                "quality": "event_listener",
                "source": "event_listener_registration",
                "decisive": True,
            }
        ],
        "observed_pointer_events": [],
        "dragging_indicators": [],
    }

    result = _validate("2.5.1", evidence)

    assert result.status == DynamicStatus.warning
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "pointer_listener_only"


def test_pointer_fail_preserved_for_observed_drag_requirement_without_alternative() -> None:
    evidence = {
        "pointer_handlers": [],
        "dragging_indicators": [
            {
                "step": 1,
                "event_type": "dragstart",
                "has_keyboard_alternative": False,
                "quality": "observed",
                "source": "observed_pointer_event",
                "decisive": True,
            }
        ],
        "observed_pointer_events": [{"step": 1, "type": "dragstart"}],
    }

    result = _validate("2.5.1", evidence)

    assert result.status == DynamicStatus.fail
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "pointer_observed_gesture_requirement"


def test_form_fail_downgraded_for_generic_error_mutation_text() -> None:
    evidence = {
        "form_snapshots": [{"step": 1}],
        "error_messages": [{"step": 1, "quality": "weak_keyword", "source": "text_keyword", "decisive": False}],
        "observed_error_mutations": [
            {"step": 1, "mutation": {"added_text": "required error"}, "quality": "observed", "source": "mutation_observer"}
        ],
        "form_interaction_events": [],
        "has_error_semantics": False,
    }

    result = _validate("3.3.1", evidence)

    assert result.status == DynamicStatus.warning
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "form_generic_error_text"


def test_form_fail_preserved_for_error_feedback_after_submit() -> None:
    evidence = {
        "form_snapshots": [{"step": 1}],
        "error_messages": [],
        "observed_error_mutations": [
            {
                "step": 2,
                "mutation": {"added_text": "Email is required"},
                "quality": "observed",
                "source": "mutation_observer",
                "decisive": True,
            }
        ],
        "submit_events": [
            {
                "step": 2,
                "type": "submit",
                "target": {"tag": "form", "xpath": "/html/body/form[1]"},
                "quality": "observed",
                "source": "runtime_event",
                "decisive": True,
            }
        ],
        "form_interaction_events": [],
        "has_error_semantics": True,
    }

    result = _validate("3.3.1", evidence)

    assert result.status == DynamicStatus.fail
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "form_error_after_user_action"


def test_help_fail_downgraded_for_generic_form_or_help_text() -> None:
    result = _validate(
        "3.3.5",
        {
            "form_snapshots": [{"step": 1}],
            "help_for_forms": [{"step": 1, "text": "Contact support"}],
            "contextual_help_checks": [],
        },
    )

    assert result.status == DynamicStatus.warning
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "form_criterion_direct_evidence"


def test_help_fail_preserved_after_real_invalid_action_without_help() -> None:
    result = _validate(
        "3.3.5",
        {
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
        },
    )

    assert result.status == DynamicStatus.fail
    assert result.fail_validation is not None
    assert result.fail_validation.rule == "form_contextual_help_missing_after_action"
