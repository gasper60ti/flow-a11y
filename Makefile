PYTHON ?= python3
PUBLICATION_OUTPUT_DIR ?= /tmp/flow-a11y-v5-verify
MPLCONFIGDIR ?= /tmp/flow-a11y-matplotlib

.PHONY: verify verify-help verify-tests verify-notebook verify-checksums

verify: verify-help verify-tests verify-notebook verify-checksums

verify-help:
	$(PYTHON) -m benchmarking.dynamic.run_dynamic_benchmark --help >/dev/null

verify-tests:
	$(PYTHON) -m pytest -k "not test_browser_use_agent_runtime_traces"

verify-notebook:
	rm -rf "$(PUBLICATION_OUTPUT_DIR)"
	MPLCONFIGDIR="$(MPLCONFIGDIR)" PYTHONDONTWRITEBYTECODE=1 \
		FLOWA11Y_PUBLICATION_OUTPUT_DIR="$(PUBLICATION_OUTPUT_DIR)" \
		$(PYTHON) -m jupyter nbconvert --to notebook --execute \
		benchmarking/dynamic/publication_analysis.ipynb \
		--output /tmp/flow-a11y-publication-analysis.ipynb \
		--ExecutePreprocessor.timeout=180

verify-checksums:
	cd benchmarking/dynamic/publication_results/v5 && shasum -a 256 -c SHA256SUMS
