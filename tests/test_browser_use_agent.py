"""Compatibility module for runtime trace benchmark utilities.

The runtime benchmark test now lives in tests/test_browser_use_agent_runtime_traces.py.
"""

from tests.test_browser_use_agent_runtime_traces import OUTPUT_ROOT, SCENARIOS, assert_trace_outputs, load_json, log, run_async

__all__ = ["OUTPUT_ROOT", "SCENARIOS", "assert_trace_outputs", "load_json", "log", "run_async"]
