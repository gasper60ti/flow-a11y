from __future__ import annotations

from typing import Any

from dynamic.schemas.runtime_state import RuntimeState


class StatusMessageProbe:
    name = "status_message_probe"

    def collect(self, states: list[RuntimeState]) -> dict[str, Any]:
        status_mutations: list[dict[str, Any]] = []
        static_live_regions: list[dict[str, Any]] = []
        observed_live_region_mutations: list[dict[str, Any]] = []
        live_region_presence = False

        for step, state in enumerate(states, start=1):
            for mutation in state.dom_mutations or []:
                if not isinstance(mutation, dict):
                    continue
                serialized = str(mutation).lower()
                mutation_type = str(mutation.get("mutation_type") or mutation.get("type") or "")
                if mutation_type == "static_live_region":
                    static_live_regions.append(
                        {
                            "step": step,
                            "mutation": mutation,
                            "url": state.url,
                            "quality": "static_candidate",
                            "source": "dom_token",
                            "decisive": False,
                        }
                    )
                    live_region_presence = True
                    continue
                if mutation_type == "observed_live_region_change":
                    entry = {
                        "step": step,
                        "mutation": mutation,
                        "url": state.url,
                        "quality": "observed",
                        "source": "mutation_observer",
                        "decisive": True,
                    }
                    observed_live_region_mutations.append(entry)
                    status_mutations.append(entry)
                    live_region_presence = True
                    continue
                if any(token in serialized for token in ["status", "alert", "aria-live", "log", "progress"]):
                    status_mutations.append(
                        {
                            "step": step,
                            "mutation": mutation,
                            "url": state.url,
                            "quality": "weak_keyword",
                            "source": "text_keyword",
                            "decisive": False,
                        }
                    )
                if any(
                    token in serialized
                    for token in ["aria-live", "role=status", "role=alert", "role=log", "role=progressbar"]
                ):
                    live_region_presence = True

            dom = (state.dom_html or "").lower()
            if any(token in dom for token in ['role="status"', 'role="alert"', "aria-live"]):
                static = {"source": "dom_live_region", "mutation_type": "static_live_region"}
                static_live_regions.append(
                    {
                        "step": step,
                        "mutation": static,
                        "url": state.url,
                        "quality": "static_candidate",
                        "source": "dom_token",
                        "decisive": False,
                    }
                )
                live_region_presence = True

        return {
            "status_message_mutations": status_mutations,
            "observed_status_mutations": [
                item for item in status_mutations if item.get("source") == "mutation_observer"
            ],
            "observed_live_region_mutations": observed_live_region_mutations,
            "static_live_regions": static_live_regions,
            "live_region_presence": live_region_presence,
        }
