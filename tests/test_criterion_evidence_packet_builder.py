from __future__ import annotations

from datetime import datetime, timezone

from dynamic.analyzers.decision_status_router import CalibratedStatusRouter
from dynamic.preprocessors.criterion_evidence_packet_builder import CriterionEvidencePacketBuilder
from dynamic.preprocessors.wcag_mapper import get_criterion
from dynamic.schemas.wcag_dynamic import CriterionEvidencePacket, DynamicCriterionResult, DynamicStatus


def _packet(criterion_id: str, evidence: dict) -> CriterionEvidencePacket:
    return CriterionEvidencePacketBuilder().build(get_criterion(criterion_id), evidence)


def _focus_evidence() -> dict:
    return {
        "keyboard_events": [{"step": 1, "type": "keydown", "key": "Tab"}],
        "observed_focus_events": [{"step": 1, "type": "focus"}],
        "focus_sequence": [
            {
                "step": 1,
                "tag": "button",
                "role": "button",
                "xpath": "/html/body/button[1]",
                "active_element_name": "Save",
                "bounding_client_rect": {"x": 10, "y": 10, "width": 80, "height": 32},
                "computed_style": {
                    "outlineStyle": "none",
                    "outlineWidth": "0px",
                    "outlineColor": "rgb(0, 0, 0)",
                    "boxShadow": "none",
                    "border": "0px none rgb(0, 0, 0)",
                    "backgroundColor": "rgb(255, 255, 255)",
                },
                "visible_focus_candidate": False,
            }
        ],
        "focus_visible_trace": [
            {
                "step": 1,
                "xpath": "/html/body/button[1]",
                "has_visible_indicator": False,
                "bounding_client_rect": {"x": 10, "y": 10, "width": 80, "height": 32},
                "quality": "computed",
                "source": "computed_style",
                "decisive": True,
            }
        ],
        "screenshot_paths": ["/tmp/screenshot_step_1.png"],
    }


def test_focus_packet_includes_keyboard_focus_rect_screenshot_and_style() -> None:
    packet = _packet("2.4.7", _focus_evidence())

    assert packet.criterion_family == "focus_keyboard"
    assert packet.applicability == "applicable"
    assert packet.evidence_strength == "direct"
    assert packet.step_consistency == "same_step"
    facts = packet.compact_runtime_facts
    assert facts["keyboard_event_count"] == 1
    assert facts["focused_interactive_count"] == 1
    assert facts["direct_missing_focus_indicator_count"] == 1
    assert facts["focus_entries"][0]["computed_style"]["outlineStyle"] == "none"


def test_focus_packet_keeps_strong_computed_style_direct_without_screenshot() -> None:
    evidence = _focus_evidence()
    evidence["screenshot_paths"] = []

    packet = _packet("2.4.7", evidence)

    assert packet.applicability == "applicable"
    assert packet.evidence_strength == "direct"
    assert packet.compact_runtime_facts["strong_computed_missing_focus_indicator_count"] == 1
    assert packet.compact_runtime_facts["direct_missing_focus_indicator_count"] == 0


def test_focus_packet_marks_single_body_focus_as_insufficient() -> None:
    evidence = _focus_evidence()
    evidence["focus_sequence"] = [{"step": 1, "tag": "body", "xpath": "/html/body"}]
    evidence["focus_visible_trace"] = [{"step": 1, "xpath": "/html/body", "has_visible_indicator": False}]

    packet = _packet("2.4.7", evidence)

    assert packet.applicability == "insufficient_evidence"
    assert packet.evidence_strength == "indirect"


def test_focus_packet_marks_repeated_keyboard_blocking_as_behavioral_direct() -> None:
    evidence = {
        "keyboard_events": [
            {"step": 1, "type": "keydown", "key": "Tab"},
            {"step": 2, "type": "keydown", "key": "Tab"},
            {"step": 3, "type": "keydown", "key": "Tab"},
        ],
        "focus_sequence": [
            {"step": 1, "tag": "body", "xpath": "/html/body"},
            {"step": 2, "tag": "body", "xpath": "/html/body"},
            {"step": 3, "tag": "body", "xpath": "/html/body"},
        ],
    }

    packet = _packet("2.1.1", evidence)

    assert packet.applicability == "applicable"
    assert packet.evidence_strength == "behavioral_direct"
    assert packet.compact_runtime_facts["repeated_keyboard_blocking"] is True


def test_focus_packet_marks_temporary_trap_with_later_recovery_as_non_blocking() -> None:
    evidence = {
        "keyboard_events": [
            {"step": 1, "type": "keydown", "key": "Tab"},
            {"step": 2, "type": "keydown", "key": "Escape"},
        ],
        "keyboard_focus_transitions": [
            {"step": 2, "key": "Escape", "focus_changed": False},
        ],
        "focus_sequence": [
            {"step": 1, "tag": "button", "xpath": "/html/body/dialog/button[1]"},
            {"step": 2, "tag": "button", "xpath": "/html/body/dialog/button[1]"},
            {"step": 3, "tag": "button", "xpath": "/html/body/button[1]"},
        ],
    }

    packet = _packet("2.1.2", evidence)

    assert packet.compact_runtime_facts["keyboard_recovery_observed"] is True
    assert packet.compact_runtime_facts["repeated_keyboard_blocking"] is False
    assert packet.compact_runtime_facts["terminal_repeated_focus"] is False


def test_focus_packet_records_parent_focus_within_as_contradictory_signal() -> None:
    evidence = _focus_evidence()
    evidence["focus_visible_trace"][0]["ancestor_focus_within"] = [
        {
            "selector": ".field:focus-within",
            "xpath": "/html/body/div[1]",
            "border": "2px solid rgb(0, 95, 204)",
        }
    ]
    evidence["focus_visible_trace"][0]["visible_focus_signals"] = {"ancestorFocusWithin": True}

    packet = _packet("2.4.7", evidence)

    assert packet.evidence_strength == "contradicted"
    assert packet.compact_runtime_facts["direct_missing_focus_indicator_count"] == 0
    assert packet.compact_runtime_facts["focus_indicator_contradiction_count"] == 1
    assert "focus_visible_trace[0]" in packet.contradictory_evidence


def test_media_packet_not_applicable_when_media_not_playing() -> None:
    packet = _packet(
        "1.2.2",
        {
            "media_elements": [{"step": 1, "tag": "video", "paused": True, "current_time_delta": 0, "visible": True}],
            "caption_tracks": [],
            "media_events": [],
        },
    )

    assert packet.applicability == "not_applicable"
    assert packet.evidence_strength == "indirect"


def test_media_packet_applicable_when_media_is_playing() -> None:
    packet = _packet(
        "1.2.2",
        {
                "media_elements": [
                {
                    "step": 1,
                    "tag": "video",
                    "paused": False,
                    "muted": False,
                    "audible": True,
                    "current_time_delta": 1.2,
                    "visible": True,
                }
            ],
            "caption_tracks": [],
            "media_events": [{"step": 1, "type": "timeupdate"}],
        },
    )

    assert packet.applicability == "applicable"
    assert packet.evidence_strength == "behavioral_direct"


def test_media_packet_not_applicable_for_visible_muted_autoplay_animation() -> None:
    packet = _packet(
        "1.2.2",
        {
            "media_elements": [
                {
                    "step": 1,
                    "tag": "video",
                    "paused": False,
                    "muted": True,
                    "audible": False,
                    "autoplay": True,
                    "controls": False,
                    "current_time_delta": 1.2,
                    "visible": True,
                }
            ],
            "caption_tracks": [],
            "media_events": [{"step": 1, "type": "timeupdate"}],
        },
    )

    assert packet.applicability == "not_applicable"
    assert packet.compact_runtime_facts["muted_autoplay_only_count"] == 1


def test_pointer_packet_not_applicable_for_listener_only_evidence() -> None:
    packet = _packet(
        "2.5.1",
        {
            "pointer_handlers": [
                {"step": 1, "event_type": "click", "source": "event_listener_registration", "quality": "event_listener"}
            ],
            "observed_pointer_events": [],
            "dragging_indicators": [],
        },
    )

    assert packet.applicability == "not_applicable"
    assert packet.compact_runtime_facts["listener_only"] is True


def test_pointer_packet_applicable_for_observed_drag_requirement() -> None:
    packet = _packet(
        "2.5.1",
        {
            "pointer_handlers": [],
            "observed_pointer_events": [{"step": 1, "type": "dragstart"}],
            "dragging_indicators": [
                {
                    "step": 1,
                    "event_type": "dragstart",
                    "source": "observed_pointer_event",
                    "quality": "observed",
                    "decisive": True,
                    "has_keyboard_alternative": False,
                }
            ],
        },
    )

    assert packet.applicability == "applicable"
    assert packet.evidence_strength == "behavioral_direct"


def test_form_packet_insufficient_for_generic_mutation_text() -> None:
    packet = _packet(
        "3.3.1",
        {
            "form_snapshots": [{"step": 1}],
            "error_messages": [{"step": 1, "quality": "weak_keyword", "source": "text_keyword"}],
            "observed_error_mutations": [{"step": 1, "mutation": {"added_text": "required error"}}],
            "submit_events": [],
            "input_events": [],
            "change_events": [],
        },
    )

    assert packet.applicability == "insufficient_evidence"
    assert packet.evidence_strength == "indirect"


def test_form_packet_applicable_for_submit_with_tied_error_feedback() -> None:
    packet = _packet(
        "3.3.1",
        {
            "form_snapshots": [{"step": 1}],
            "submit_events": [{"step": 2, "target": {"tag": "form", "xpath": "/html/body/form[1]"}}],
            "input_events": [],
            "change_events": [],
            "observed_error_mutations": [
                {
                    "step": 2,
                    "mutation": {"added_text": "Email is required"},
                    "quality": "observed",
                    "source": "mutation_observer",
                    "decisive": True,
                }
            ],
            "error_messages": [],
            "has_error_semantics": True,
        },
    )

    assert packet.applicability == "applicable"
    assert packet.evidence_strength == "behavioral_direct"


def test_help_packet_requires_complete_action_tied_help_check() -> None:
    weak = _packet(
        "3.3.5",
        {
            "form_snapshots": [{"step": 1}],
            "help_for_forms": [{"step": 1, "text": "Help"}],
            "contextual_help_checks": [],
        },
    )
    direct = _packet(
        "3.3.5",
        {
            "submit_events": [{"step": 2, "type": "submit", "target": {"tag": "form"}}],
            "contextual_help_checks": [
                {
                    "step": 2,
                    "invalid_after_action": True,
                    "error_feedback_present": True,
                    "help_present": False,
                    "evaluation_complete": True,
                }
            ],
            "contextual_help_failures": [
                {
                    "step": 2,
                    "help_requested": True,
                    "help_present": False,
                    "source": "runtime_help_interaction",
                    "quality": "observed",
                    "decisive": True,
                }
            ],
        },
    )

    assert weak.criterion_family == "forms"
    assert weak.applicability == "not_applicable"
    assert weak.evidence_strength == "indirect"
    assert direct.criterion_family == "forms"
    assert direct.applicability == "applicable"
    assert direct.evidence_strength == "behavioral_direct"
    assert direct.compact_runtime_facts["missing_contextual_help_count"] == 1


def test_related_form_criteria_use_form_family() -> None:
    assert _packet("3.3.7", {}).criterion_family == "forms"
    assert _packet("3.3.8", {}).criterion_family == "forms"
    assert _packet("3.3.9", {}).criterion_family == "forms"


def test_other_packet_indirect_only_is_not_eligible_for_fail() -> None:
    packet = _packet(
        "2.3.3",
        {
            "interaction_animation_events": [{"quality": "static_candidate", "decisive": False}],
            "evidence_quality_summary": {"evidence_strength": "weak"},
        },
    )

    assert packet.criterion_family == "other"
    assert packet.applicability == "insufficient_evidence"
    assert packet.evidence_strength == "indirect"


def test_other_packet_direct_runtime_signal_remains_applicable() -> None:
    packet = _packet(
        "2.3.3",
        {
            "interaction_animation_events": [
                {"quality": "observed", "source": "runtime_event", "decisive": True}
            ],
            "evidence_quality_summary": {"evidence_strength": "moderate"},
        },
    )

    assert packet.criterion_family == "other"
    assert packet.applicability == "applicable"
    assert packet.evidence_strength == "direct"


def _result(status: DynamicStatus) -> DynamicCriterionResult:
    return DynamicCriterionResult(
        criterion_id="1.4.13",
        criterion_name="Content on Hover or Focus",
        status=status,
        summary="Routed status.",
        generated_at=datetime.now(timezone.utc),
    )


def _manual_packet(applicability: str, strength: str, *, positive: list[str] | None = None) -> CriterionEvidencePacket:
    return CriterionEvidencePacket(
        criterion_id="1.4.13",
        criterion_family="other",
        applicability=applicability,
        applicability_reason="manual packet",
        evidence_strength=strength,
        positive_evidence=positive or [],
        missing_evidence=[],
        contradictory_evidence=[],
        step_consistency="unknown",
        compact_runtime_facts={},
    )


def test_status_router_confirmed_fail_maps_to_fail() -> None:
    routed = CalibratedStatusRouter().route(get_criterion("1.4.13"), _result(DynamicStatus.fail), _manual_packet("applicable", "direct"))

    assert routed.status == DynamicStatus.fail
    assert routed.decision_level == "confirmed_fail"


def test_status_router_probable_violation_maps_to_warning() -> None:
    routed = CalibratedStatusRouter().route(
        get_criterion("1.4.13"),
        _result(DynamicStatus.warning),
        _manual_packet("applicable", "direct"),
    )

    assert routed.status == DynamicStatus.warning
    assert routed.decision_level == "probable_violation"


def test_status_router_indirect_other_signal_maps_to_incomplete() -> None:
    routed = CalibratedStatusRouter().route(
        get_criterion("1.4.13"),
        _result(DynamicStatus.warning),
        _manual_packet("applicable", "indirect"),
    )

    assert routed.status == DynamicStatus.incomplete
    assert routed.decision_level == "insufficient_evidence"


def test_status_router_insufficient_maps_to_incomplete() -> None:
    routed = CalibratedStatusRouter().route(
        get_criterion("1.4.13"),
        _result(DynamicStatus.fail),
        _manual_packet("insufficient_evidence", "insufficient"),
    )

    assert routed.status == DynamicStatus.incomplete
    assert routed.decision_level == "insufficient_evidence"


def test_status_router_not_applicable_maps_to_not_applicable() -> None:
    routed = CalibratedStatusRouter().route(
        get_criterion("1.4.13"),
        _result(DynamicStatus.fail),
        _manual_packet("not_applicable", "insufficient"),
    )

    assert routed.status == DynamicStatus.not_applicable
    assert routed.decision_level == "not_applicable"


def test_status_router_surfaces_behavioral_focus_pass_as_warning() -> None:
    packet = _packet(
        "2.1.3",
        {
            "keyboard_events": [
                {"step": 1, "type": "keydown", "key": "Tab"},
                {"step": 2, "type": "keydown", "key": "Tab"},
            ],
            "focus_sequence": [
                {"step": 1, "tag": "body", "xpath": "/html/body"},
                {"step": 2, "tag": "body", "xpath": "/html/body"},
            ],
        },
    )
    result = DynamicCriterionResult(
        criterion_id="2.1.3",
        criterion_name="Keyboard (No Exception)",
        status=DynamicStatus.pass_status,
        summary="Pass judgment.",
        requires_llm=True,
        generated_at=datetime.now(timezone.utc),
    )

    routed = CalibratedStatusRouter().route(get_criterion("2.1.3"), result, packet)

    assert routed.status == DynamicStatus.warning
    assert routed.decision_level == "probable_violation"
