# Documentation

Start here when evaluating or integrating `gpu-job-control`.

## Concepts

- [Architecture](architecture.md): components, control flow, and the determinism boundary.
- [Worker Contract](worker-contract.md): required job and artifact interfaces.
- [Routing Policy](routing-policy.md): provider scoring, intake buffering, burst handling, and backpressure.

## Operations

- [Operations](operations.md): runtime paths, API auth, configuration overrides, guard loop, and Docker build posture.
- [Provider Promotion](provider-promotion.md): conditions for moving a provider from canary to production routing.
- [RunPod Self-Hosted Endpoint Research](runpod-self-hosted-research.md): RunPod templates, cached models, network volumes, and endpoint promotion gates.
- [Launch Decision](launch-decision.md): current launch route decision and deferred provider paths.
- [Generic System Integration Prompt](generic-system-integration-prompt.md): reusable prompt for adapting any system to gpu-job-control without confusing synchronous submit with asynchronous intake.
- [RunPod Support Question](runpod-support-question.md): reproducible evidence for the current Serverless vLLM endpoint blocker.
- [Worker Image Distribution](ghcr-publish-runbook.md): runtime-independent image publishing and registry-mirroring guidance.

## Canonical References

GitHub is the canonical source for this repository, even when it is private. The operational checkout on `netcup` is the runtime reference used by the fleet. A workstation checkout, including `macstudio`, is only a development clone.

- Canonical source: `https://github.com/noiehoie/gpu-job-control`
- Canonical integration prompt: `https://github.com/noiehoie/gpu-job-control/blob/main/docs/generic-system-integration-prompt.md`
- Operational checkout: `/home/admin/gpu-job-control`
- Operational integration prompt: `/home/admin/gpu-job-control/docs/generic-system-integration-prompt.md`

## Design

- [AI Workload OS Design](ai-workload-os-design.md): long-form design principles and constraints.

## Repository Map

```text
config/       Safe default policy and capability files.
docs/         Public architecture and operations documentation.
examples/     Provider-neutral job examples.
schemas/      JSON schema for the job contract.
src/gpu_job/  Core library, CLI, API, router, guard, and providers.
systemd/      Example service units.
templates/    Provider template examples.
tests/        Deterministic unit and security tests.
```
