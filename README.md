# gpu-job-control

[![CI](https://github.com/noiehoie/gpu-job-control/actions/workflows/ci.yml/badge.svg)](https://github.com/noiehoie/gpu-job-control/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%3E%3D3.11-blue.svg)](pyproject.toml)
[![Code style](https://img.shields.io/badge/code%20style-ruff-black.svg)](pyproject.toml)
[![Security](https://img.shields.io/badge/security-policy-green.svg)](SECURITY.md)

`gpu-job-control` is a provider-neutral workload control plane for GPU jobs.

It accepts a normalized job contract, evaluates policy and live provider signals, then routes work to local workers or external GPU providers with deterministic cost, capacity, timeout, and artifact checks.

The project is built around one operational rule:

> Use deterministic policy for everything that can be deterministic. Use model reasoning only after deterministic planning has exhausted the available facts.

## What It Does

- Validates provider-neutral GPU job JSON.
- Plans and routes jobs across local, Modal, RunPod, Vast.ai, and Ollama-style providers.
- Applies pre-submit and post-submit billing guards.
- Enforces provider concurrency limits and backpressure.
- Maintains a local durable job store and queue.
- Produces replayable routing decisions.
- Verifies artifact manifests after execution.
- Provides CLI and HTTP interfaces for upstream systems.
- Builds deterministic worker images for provider canaries.

## Why Use It

GPU work becomes expensive when every application talks to every provider directly. `gpu-job-control` gives those applications one contract and one policy boundary:

- applications describe work, not cloud-provider mechanics;
- provider routing is explainable and replayable;
- burst traffic is buffered before commitment;
- billing guards run before and after execution;
- worker success is verified by artifacts, not by optimistic logs.

## Repository Status

This repository is an early control-plane implementation. The public surface is intentionally conservative:

- provider credentials are read from environment or provider CLIs only;
- no secrets belong in job payloads, examples, logs, or source control;
- external providers remain behind promotion gates until repeatable canaries pass;
- destructive provider operations must be explicitly authorized by policy.
- GitHub and GHCR are publication and CI surfaces, not required runtime dependencies.

## Quick Start

Install and run with `uv`:

```bash
uv --version
uv run gpu-job doctor
uv run gpu-job validate examples/jobs/asr.example.json
uv run gpu-job route examples/jobs/asr.example.json
uv run gpu-job guard
uv run gpu-job selftest
```

Provider SDKs are optional and are not required for local validation:

```bash
uv sync --extra providers
```

Run a zero-spend local canary:

```bash
uv run gpu-job submit examples/jobs/embedding.local.json --provider local --execute
uv run gpu-job verify ~/.local/share/gpu-job-control/artifacts/<job_id>
```

Start the HTTP API on localhost:

```bash
GPU_JOB_API_TOKEN=replace-me uv run gpu-job serve \
  --host 127.0.0.1 \
  --port 8765 \
  --require-token
```

Check the API:

```bash
curl -sS -H "Authorization: Bearer $GPU_JOB_API_TOKEN" \
  http://127.0.0.1:8765/guard
```

## Core Concepts

- **Job contract**: a provider-neutral JSON document describing input, output, limits, quality requirements, and routing hints.
- **Planner**: deterministic policy that scores providers using job metadata, provider signals, cost, capacity, resource guard status, and historical statistics.
- **Guard**: a fail-closed check that detects active paid resources, unexpected persistent resources, local resource pressure, and queue saturation.
- **Promotion gate**: a provider cannot be used for production routing until canary execution, artifact verification, cancellation behavior, and post-guard checks are all proven.
- **Artifact manifest**: every worker must write enough structured output for the control plane to verify completion without trusting logs.

## Documentation

- [Documentation Index](docs/index.md)
- [Architecture](docs/architecture.md)
- [Job Contract](docs/worker-contract.md)
- [Routing Policy](docs/routing-policy.md)
- [Provider Promotion](docs/provider-promotion.md)
- [RunPod Self-Hosted Endpoint Research](docs/runpod-self-hosted-research.md)
- [Worker Image Distribution](docs/ghcr-publish-runbook.md)
- [Operations](docs/operations.md)

## Canonical Source

GitHub is the canonical source for the repository, including the reusable integration prompt for downstream systems. The `netcup` checkout is the operational runtime reference. Workstation checkouts are development clones only.

- Canonical repository: `https://github.com/noiehoie/gpu-job-control`
- Canonical integration prompt: `https://github.com/noiehoie/gpu-job-control/blob/main/docs/generic-system-integration-prompt.md`
- Operational checkout: `/home/admin/gpu-job-control`
- Operational integration prompt: `/home/admin/gpu-job-control/docs/generic-system-integration-prompt.md`

## Runtime Independence

`gpu-job-control` should not require GitHub, GitHub Actions, or GHCR in the runtime path.

Recommended deployment posture:

- use GitHub for source review, releases, and reproducible CI evidence;
- run the control plane on your own host or private network;
- keep provider credentials in your own secret store, not in GitHub;
- mirror worker images to the registry or provider-native template system you operate;
- pin production worker images by digest and verify artifacts after execution.

Any GHCR-hosted image produced by this repository is only an optional reproducibility artifact. Production deployments should mirror worker images into an operator-controlled registry or provider-native template system before use.

## Docker and Worker Images

Do not assume the developer workstation has Docker. Use a remote Linux builder or CI for image builds.

The repository includes a GitHub Actions workflow that demonstrates a reproducible RunPod canary image build:

```text
.github/workflows/publish-runpod-worker.yml
```

The workflow builds and tests:

```text
docker/runpod-llm-worker.Dockerfile
src/gpu_job/workers/runpod_llm.py
```

Treat that workflow as a reproducibility example, not as required production plumbing.

Mirror images into your own runtime registry before using them in production:

```bash
uv run gpu-job image mirror \
  --source ghcr.io/example/gpu-job-control-runpod-llm@sha256:<digest> \
  --target registry.example.com/gpu-job-control/runpod-llm@sha256:<digest> \
  --builder netcup
```

## Security

See [SECURITY.md](SECURITY.md).

Security defaults:

- no provider API key in source control;
- no long-lived registry token in a workload host unless explicitly approved;
- destructive provider operations must be explicitly modeled and policy-gated before exposure;
- no automatic production promotion from a single successful API call.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development checks and pull request expectations. Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).
