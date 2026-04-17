# Architecture

`gpu-job-control` is a small control plane, not a model server.

It separates upstream applications from provider-specific GPU execution details. Upstream systems submit normalized jobs. The control plane validates, plans, routes, executes, tracks, verifies, and audits those jobs.

## Design Goals

1. Prevent accidental spend.
2. Avoid wasted queue time.
3. Preserve required model quality.
4. Protect local fleet resources.
5. Treat provider API state as a live signal, not as a permanent truth.
6. Avoid direct-submit decisions when a short intake buffer can make a better batch-level decision.
7. Keep destructive provider operations behind explicit approval.
8. Make every routing decision replayable from metadata, signals, stats, and policy.

## Components

- **CLI/API**: entry points for validation, routing, queueing, submission, cancellation, and verification.
- **Canonical job model**: normalizes job payloads before any provider-specific logic.
- **Policy engine**: deterministic gates for cost, quota, resource pressure, capabilities, secrets, and provider eligibility.
- **Router**: ranks eligible providers using declared job requirements and live provider signals.
- **Queue/intake**: groups near-simultaneous work before planning so burst behavior is visible.
- **Providers**: adapters for local execution and external GPU services.
- **Store**: local durable state for jobs, artifacts, WAL records, and audit entries.
- **Guard**: fail-closed checks for billing, local resource pressure, provider queues, and unknown resources.
- **Verification**: artifact-level checks that do not rely on log interpretation.

## Control Flow

```text
job JSON
  -> canonical validation
  -> policy gates
  -> intake grouping
  -> provider signal collection
  -> deterministic routing decision
  -> guarded submit
  -> provider execution
  -> artifact verification
  -> post-submit guard
  -> audit record
```

## Determinism Boundary

The planner should use deterministic calculation whenever inputs are measurable:

- declared quality requirement;
- estimated input size;
- estimated CPU/GPU runtime;
- queue depth;
- provider startup estimate;
- known provider failure rate;
- cost cap;
- local resource pressure;
- artifact contract.

Model reasoning may help classify tasks or estimate missing metadata, but the final execution decision must be explainable as deterministic policy over recorded inputs.
