# Same checks as .github/workflows/ci.yml, for running before a commit.
.PHONY: check lint test coverage examples excel matlab requirements

check: lint coverage

lint:
	ruff check .

test:
	python3 -m pytest -q

coverage:
	python3 -m pytest -q --cov --cov-report=term

examples:
	cd examples && python3 parametric_study.py && python3 site_performance.py && python3 passoff_test.py \
		&& python3 fleet_analysis.py && python3 multipoint_calibration.py && python3 humidity_check.py \
		&& python3 environment_study.py && python3 turbofan_offdesign.py && python3 turbofan_study.py \
		&& python3 observability_study.py && python3 coverage_study.py

excel:
	python3 -m gtperf.cli excel examples/sgt400_13mw.toml excel/gtperf_site_tool.xlsx

matlab:
	python3 matlab/make_reference.py
	cd matlab && octave --no-gui --quiet --eval run_tests

requirements:
	python -m gtperf.requirements
