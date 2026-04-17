# Changelog

All notable changes to this project will be documented in this file.

This project follows a conservative pre-1.0 policy: public contracts may change, but every externally visible change should be recorded here before release.

## Unreleased

- Added RunPod OpenAI-compatible Public Endpoint execution mode for LLM canaries.
- Allowed RunPod GraphQL helpers to read the existing `~/.runpod/config.toml` API key without exporting it.
- Updated the worker systemd template so queued workers can read provider endpoint settings from the service environment file.
- Added RunPod self-hosted endpoint research notes for templates, cached models, network volumes, and promotion gates.
- Added `gpu-job runpod plan-vllm-endpoint` and `promote-vllm-endpoint` for scale-to-zero RunPod vLLM Serverless endpoint canaries.
- Refined RunPod vLLM canary defaults to an empty location filter, `ADA_24`, `gpuCount=1`, `idleTimeout=90`, `QUEUE_DELAY=15`, and local GPU pool validation.
- Added a RunPod vLLM support probe and canonical support-question document for the current Serverless vLLM worker-allocation blocker.
- Added bounded RunPod Pod lifecycle planning and canary commands with cost estimation, clean pre/post guards, runtime observation, and forced termination.

## 0.1.1 - 2026-04-17

- Documented that GitHub, GitHub Actions, and GHCR are publication surfaces, not runtime dependencies.
- Added operator-controlled worker image mirroring guidance.
- Added `gpu-job image mirror` for digest-pinned registry mirroring through a remote builder.

## 0.1.0 - 2026-04-17

- Initial public-ready repository structure.
- Provider-neutral job contract, router, guard, queue, intake, and artifact verification.
- Local, Ollama-style, Modal, RunPod, and Vast provider adapters.
- GitHub Actions CI and GHCR worker publishing workflow.
- Security defaults for fail-closed API auth, constrained CORS, request size limits, and secret hygiene.
