# Same checks as .github/workflows/ci.yml, for running before a commit.
.PHONY: check lint test coverage examples excel matlab

check: lint coverage

lint:
	ruff check .

test:
	python3 -m pytest -q

coverage:
	python3 -m pytest -q --cov --cov-report=term

examples:
	cd examples && python3 parametric_study.py && python3 site_performance.py && python3 passoff_test.py \
		&& python3 fleet_analysis.py

excel:
	python3 -m gtperf.cli excel examples/sgt400_13mw.toml excel/gtperf_site_tool.xlsx

matlab:
	python3 matlab/make_reference.py
	cd matlab && octave --no-gui --quiet --eval run_tests
