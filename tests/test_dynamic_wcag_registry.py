from __future__ import annotations

from dynamic.preprocessors.wcag_mapper import DYNAMIC_WCAG_REGISTRY, WCAG_DYNAMIC_CRITERIA


EXPECTED_DYNAMIC_CRITERIA = {
    "1.4.13",
    "2.1.1",
    "2.1.2",
    "2.1.3",
    "2.1.4",
    "2.2.3",
    "2.2.4",
    "2.2.5",
    "2.2.6",
    "2.3.3",
    "2.4.3",
    "2.4.7",
    "2.4.11",
    "2.4.12",
    "2.4.13",
    "2.5.1",
    "2.5.2",
    "2.5.4",
    "2.5.6",
    "2.5.7",
    "3.2.1",
    "3.2.3",
    "3.2.4",
    "3.2.6",
    "3.3.1",
    "3.3.3",
    "3.3.4",
    "3.3.5",
    "3.3.6",
    "3.3.7",
    "3.3.8",
    "3.3.9",
    "4.1.3",
    "1.2.1",
    "1.2.2",
    "1.2.3",
    "1.2.4",
    "1.2.5",
    "1.2.6",
    "1.2.7",
    "1.2.8",
    "1.2.9",
    "1.4.7",
    "2.3.1",
    "2.3.2",
}


def test_dynamic_wcag_registry_contains_expected_criteria() -> None:
    registered = set(WCAG_DYNAMIC_CRITERIA.keys())
    assert registered == EXPECTED_DYNAMIC_CRITERIA


def test_dynamic_criteria_have_required_evidence_and_probe() -> None:
    for criterion in DYNAMIC_WCAG_REGISTRY.supported_ids():
        item = DYNAMIC_WCAG_REGISTRY.get(criterion)
        assert item.probe, f"Expected probe mapping for {criterion}"
        assert item.required_evidence, f"Expected required evidence for {criterion}"


def test_dynamic_registry_probe_groups_are_consistent() -> None:
    for probe_name in DYNAMIC_WCAG_REGISTRY.supported_probes():
        criteria_ids = DYNAMIC_WCAG_REGISTRY.criteria_for_probe(probe_name)
        assert criteria_ids, f"Expected at least one criterion mapped to {probe_name}"
        for criterion_id in criteria_ids:
            criterion = DYNAMIC_WCAG_REGISTRY.get(criterion_id)
            assert criterion.probe == probe_name
