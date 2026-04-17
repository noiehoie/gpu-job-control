# Documentation

Start here when evaluating or integrating `gpu-job-control`.

## Concepts

- [Architecture](architecture.md): components, control flow, and the determinism boundary.
- [Worker Contract](worker-contract.md): required job and artifact interfaces.
- [Routing Policy](routing-policy.md): provider scoring, intake buffering, burst handling, and backpressure.

## Operations

- [Operations](operations.md): runtime paths, API auth, configuration overrides, guard loop, and Docker build posture.
- [Provider Promotion](provider-promotion.md): conditions for moving a provider from canary to production routing.
- [Worker Image Distribution](ghcr-publish-runbook.md): reproducible image publishing and registry-mirroring guidance.

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
