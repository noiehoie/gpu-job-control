# Contributing

Contributions should preserve the control plane's core invariant:

> Deterministic policy first. Reasoning only when deterministic inputs are insufficient.

## Development Checks

Install development tools:

```bash
uv sync --extra dev
```

Run:

```bash
make check
```

Equivalent explicit commands:

```bash
uv run python -m compileall src tests
uv run python -m unittest discover -s tests
uv run --with ruff ruff check src tests
uv run --with ruff ruff format --check src tests
uv run gpu-job selftest
uv run gpu-job validate examples/jobs/asr.example.json
```

## Pull Request Expectations

- Keep provider-specific behavior behind provider adapters.
- Do not add credentials, endpoint IDs, internal hostnames, or private paths to examples or docs.
- Add or update deterministic tests for routing, guard, or artifact-contract behavior.
- Treat billing and destructive-provider behavior as fail-closed paths.
- Prefer structured parsers and structured provider responses over log scraping.
- Keep documentation examples copy-pasteable and free of live private identifiers.
- Update `CHANGELOG.md` for externally visible behavior, configuration, or API changes.

## Documentation

Public docs should describe reusable architecture and policy. Environment-specific incident reports, endpoint IDs, and private deployment details do not belong in this repository.
