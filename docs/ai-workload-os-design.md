# AI Workload OS Design

This document records the design direction for a local-fleet and cloud-GPU workload operating system.

## Constitution

1. Do not create accidental spend.
2. Do not create avoidable waiting.
3. Do not route quality-sensitive work to an execution target that cannot satisfy it.
4. Do not crush local fleet resources.
5. Do not convert transient provider API state into permanent global policy.
6. Do not make direct-submit decisions when intake buffering can reveal burst shape.
7. Do not run destructive provider operations without explicit authorization.
8. Explain every decision from job metadata, live signals, observed stats, and policy.

## Normalized Constraints

Every constraint must map to deterministic inputs and deterministic gates.

| Constraint | Deterministic Inputs | Hard Gate |
| --- | --- | --- |
| Spend | max cost, active resources, persistent resources, provider price | reject if estimated or observed spend exceeds policy |
| Waiting | queue depth, startup estimate, deadline, TTL | reject or reroute when wait cannot fit the deadline |
| Quality | job type, model capability, context window, modality, quality flag | reject providers that do not meet declared capability |
| Local safety | memory, swap, disk, load, active local jobs | reject local execution when resource guard fails |
| Provider instability | current health, recent failures, circuit state | degrade or quarantine provider by policy |
| Direct submit | intake hold window, observed burst, batch size | use intake planner for burst candidates |
| Destructive action | action type, target role, approval record | block when approval is absent |
| Explainability | recorded facts, policy version, score components | persist a decision record before execution |

## Determinism Boundary

The system should first extract all measurable facts:

- input size;
- expected output size;
- modality;
- quality requirement;
- deadline;
- estimated CPU runtime;
- estimated GPU runtime;
- batch and burst shape;
- provider queue depth;
- provider startup estimate;
- provider price;
- provider capability;
- local resource pressure;
- historical success and timeout rate.

Only after those facts are exhausted may a model assist with classification or estimation. The final decision still must be deterministic over recorded values.

## Planner Layers

1. **Canonicalization**: parse job JSON and produce a stable internal representation.
2. **Policy gate**: reject jobs that violate schema, quota, secret, or safety policy.
3. **Intake buffer**: hold compatible jobs briefly to observe burst shape.
4. **Batch planner**: decide whether to keep jobs separate or route as a group.
5. **Provider eligibility**: filter providers by capability, health, cost, and resource guard.
6. **Scoring**: rank eligible providers with explainable score components.
7. **Execution**: submit only after pre-guard passes.
8. **Verification**: trust artifacts, not provider optimism or logs.
9. **Reconciliation**: detect orphaned provider resources and stale local jobs.

## Burst Planning

Caller-provided `burst_size` is a hint, not a source of truth. The intake layer must also observe actual arrival rate.

The planner should distinguish:

- one small request;
- many small independent requests;
- a batch that amortizes cold start;
- latency-sensitive interactive requests;
- long-running jobs where cold start is negligible;
- jobs that must use a high-quality model even when cheaper targets are idle.

## Provider Promotion

Provider states are evidence-based:

```text
unconfigured -> health_check_ok -> lifecycle_proven -> canary_passed -> controlled_canary -> production_primary
```

Promotion requires repeatable canary evidence, provider-side cancellation, artifact verification, and clean post-guard. A provider may be demoted by hidden warm capacity, unknown resources, unbounded queues, repeated timeouts, or inconsistent API state.

## Non-Goals

- This is not a universal model-serving framework.
- This is not a replacement for Kubernetes.
- This is not a place to hide provider-specific complexity from policy.
- This is not allowed to trade observability for convenience.
