# Documentation

Start here when evaluating or integrating `gpu-job-control`.

## Concepts

- [Architecture](architecture.md): components, control flow, and the determinism boundary.
- [Worker Contract](worker-contract.md): required job and artifact interfaces.
- [Routing Policy](routing-policy.md): provider scoring, intake buffering, burst handling, and backpressure.
- [Provider Module Contracts](provider-module-contracts.md): provider-native module units for RunPod, Vast, and Modal without reimplementing their runtimes.

## Operations

- [Operations](operations.md): runtime paths, API auth, configuration overrides, guard loop, and Docker build posture.
- [Provider Promotion](provider-promotion.md): conditions for moving a provider from canary to production routing.
- [RunPod Self-Hosted Endpoint Research](runpod-self-hosted-research.md): RunPod templates, cached models, network volumes, and endpoint promotion gates.
- [Launch Decision](launch-decision.md): current launch route decision and deferred provider paths.
- [Launch Phase 0-5 Gate](launch-phase0-5-gate.md): machine-readable launch gates and current stop conditions from contract freeze through provider canaries.
- [Generic System Integration Prompt](generic-system-integration-prompt.md): reusable prompt for adapting any system to gpu-job-control without confusing synchronous submit with asynchronous intake.
- [Caller Contract](caller-contract.md): canonical caller-facing request schema and fail-closed rules.
- [Operation Catalog](operation-catalog.md): closed operation menu accepted by the public product surface.
- [Public API](public-api.md): caller-facing transport surface and compatibility notes.
- [Client Integration Guide](client-integration-guide.md): reference client and downstream integration order.
- [Finished Product Gate](finished-product-gate.md): release-blocking acceptance criteria for calling the repository an external finished product.
- [Error Codes](error-codes.md): public failure taxonomy, retry guidance, and provider responsibility boundary.
- [Data Lifecycle](data-lifecycle.md): caller input, artifact, log, retention, deletion, and privacy rules.
- [Product Invariants](product-invariants.md): release-blocking invariants that must not be relaxed.
- [RunPod Support Question](runpod-support-question.md): reproducible evidence for the current Serverless vLLM endpoint blocker.
- [Worker Image Distribution](ghcr-publish-runbook.md): runtime-independent image publishing and registry-mirroring guidance.

## Canonical References

GitHub is the canonical source for this repository, even when it is private. The operational checkout on `netcup` is the runtime reference used by the fleet. A workstation checkout, including `macstudio`, is only a development clone.

- Canonical source: `https://github.com/noiehoie/gpu-job-control`
- Canonical integration prompt: `https://github.com/noiehoie/gpu-job-control/blob/main/docs/generic-system-integration-prompt.md`
- Canonical versioned integration prompt: `https://github.com/noiehoie/gpu-job-control/blob/main/docs/generic-system-integration-prompt-v1.md`
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
