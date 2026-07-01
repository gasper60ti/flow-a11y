"""Dynamic analysis package."""

from __future__ import annotations

__all__ = ["BrowserUseAccessibilityAgent"]


def __getattr__(name: str):
    if name == "BrowserUseAccessibilityAgent":
        from dynamic.browser_use_agent import BrowserUseAccessibilityAgent

        return BrowserUseAccessibilityAgent
    raise AttributeError(name)
