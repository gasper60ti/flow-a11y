from __future__ import annotations

from typing import Any

from dynamic.schemas.runtime_state import RuntimeState


class TimingInterruptionProbe:
    name = "timing_interruption_probe"

    def collect(self, states: list[RuntimeState]) -> dict[str, Any]:
        timing_snapshots: list[dict[str, Any]] = []
        timeout_warnings: list[dict[str, Any]] = []
        interruption_signals: list[dict[str, Any]] = []

        for step, state in enumerate(states, start=1):
            if state.timing_info is not None:
                timing_snapshots.append({"step": step, "timing_info": state.timing_info})

            combined_text = " ".join(
                [
                    (state.dom_html or ""),
                    " ".join(str(item) for item in state.console_logs),
                    " ".join(str(item) for item in state.dom_mutations),
                ]
            ).lower()

            if any(token in combined_text for token in ["timeout", "session expired", "extend session", "time remaining"]):
                timeout_warnings.append({"step": step, "url": state.url})
            if any(token in combined_text for token in ["modal", "alert", "interrupt", "reauth"]):
                interruption_signals.append({"step": step, "url": state.url})

        return {
            "timing_snapshots": timing_snapshots,
            "timeout_warnings": timeout_warnings,
            "interruption_signals": interruption_signals,
            "reauthentication_events": [item for item in interruption_signals if "auth" in item.get("url", "")],
        }
