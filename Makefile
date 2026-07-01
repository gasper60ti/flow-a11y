PYTHON ?= python

.PHONY: verify verify-tests verify-notebook verify-checksums

verify: verify-tests verify-notebook verify-checksums

verify-tests:
	$(PYTHON) -m pytest -k "not test_browser_use_agent_runtime_traces"

verify-notebook:
	DYNAMIC_RESULTS_DIR=publication_results/v4 \
		$(PYTHON) -m jupyter nbconvert --to notebook --execute \
		benchmarking/dynamic/publication_analysis.ipynb \
		--output /tmp/flow-a11y-publication-analysis.ipynb \
		--ExecutePreprocessor.timeout=180

verify-checksums:
	cd benchmarking/dynamic/publication_results/v4 && shasum -a 256 -c SHA256SUMS
