.PHONY: check compile test lint format selftest validate

check: compile test lint format selftest validate

compile:
	uv run python -m compileall src tests

test:
	uv run python -m unittest discover -s tests

lint:
	uv run --with ruff ruff check src tests

format:
	uv run --with ruff ruff format --check src tests

selftest:
	uv run gpu-job selftest

validate:
	uv run gpu-job validate examples/jobs/asr.example.json
