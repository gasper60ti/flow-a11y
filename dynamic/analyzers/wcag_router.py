from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from dynamic.preprocessors.wcag_mapper import DYNAMIC_WCAG_REGISTRY, DynamicWCAGCriterionRegistry


class DynamicWCAGRouter:
    """Maps criteria to probe groups."""

    def __init__(self, registry: DynamicWCAGCriterionRegistry | None = None) -> None:
        self.registry = registry or DYNAMIC_WCAG_REGISTRY

    def probe_for(self, criterion_id: str) -> str:
        criterion = self.registry.get(criterion_id)
        return criterion.probe

    def criteria_for_probe(self, probe_name: str) -> list[str]:
        return self.registry.criteria_for_probe(probe_name)

    def resolve(self, criteria: Iterable[str] | None = None) -> tuple[list[str], list[str]]:
        return self.registry.resolve(criteria)

    def probe_groups_for_criteria(self, criteria: Iterable[str]) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for criterion_id in criteria:
            if not self.registry.is_supported(criterion_id):
                continue
            grouped[self.probe_for(criterion_id)].append(criterion_id)
        return {probe: sorted(ids) for probe, ids in grouped.items()}
