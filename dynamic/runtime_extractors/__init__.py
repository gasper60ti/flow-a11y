from __future__ import annotations

_EXPORTS = {
    "AccessibilityRuntimeExtractor": "dynamic.runtime_extractors.accessibility_runtime",
    "ConsoleRuntimeExtractor": "dynamic.runtime_extractors.console_runtime",
    "DomRuntimeExtractor": "dynamic.runtime_extractors.dom_runtime",
    "FocusRuntimeExtractor": "dynamic.runtime_extractors.focus_runtime",
    "KeyboardRuntimeExtractor": "dynamic.runtime_extractors.keyboard_runtime",
    "MediaRuntimeExtractor": "dynamic.runtime_extractors.media_runtime",
    "MutationRuntimeExtractor": "dynamic.runtime_extractors.mutation_runtime",
    "RuntimeInstrumentationExtractor": "dynamic.runtime_extractors.instrumentation_runtime",
    "ScreenshotRuntimeExtractor": "dynamic.runtime_extractors.screenshot_runtime",
    "TimingRuntimeExtractor": "dynamic.runtime_extractors.timing_runtime",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)

    module = __import__(module_name, fromlist=[name])
    value = getattr(module, name)
    globals()[name] = value
    return value
