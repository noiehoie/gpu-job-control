# Provider Promotion

Providers are not promoted because an API call succeeds. They are promoted only after repeatable evidence shows that they can execute a bounded job, produce valid artifacts, and shut down cleanly.

## States

```text
unconfigured
  -> health_check_ok
  -> lifecycle_proven
  -> canary_passed
  -> controlled_canary
  -> production_primary
```

Any of these findings demotes or quarantines a provider:

- unbounded queue wait;
- missing provider-side cancel;
- hidden warm capacity;
- unknown billable resource;
- artifact verification failure;
- provider status API inconsistency;
- cost estimate above policy;
- repeated timeout or startup failure.

## Promotion Gates

Every provider must pass:

1. Provider CLI/API health check.
2. Clean pre-guard.
3. Bounded canary submission.
4. Provider job ID persisted before waiting.
5. Queue wait bounded by policy.
6. Provider-side cancel/delete tested.
7. Artifact manifest verified.
8. Clean post-guard.
9. Decision record persisted.

RunPod self-hosted Serverless endpoints have additional gates:

1. Template source identified as official RunPod worker, RunPod Hub/repo worker, or operator image.
2. Model source identified as Public Endpoint, cached Hugging Face model, network volume, or baked image.
3. Endpoint has `workersMin=0` before and after canary unless paid warm capacity is explicitly selected.
4. Endpoint delete path is proven: set `workersMax=0`, then delete.
5. Model materialization strategy is measured separately from inference runtime.
6. If a network volume is attached, it is on the approved volume allowlist and within storage budget.
7. If the endpoint uses a gated/private Hugging Face model, an approved secret reference is present.
8. OpenAI-compatible `/models` and short generation canaries pass before production traffic.

## Startup Policy

Cold start is not globally good or bad. It is evaluated against the job:

```text
startup_is_acceptable =
  startup_seconds <= hard_startup_limit
  and startup_seconds <= estimated_gpu_runtime_seconds * max_startup_fraction
```

For large batch jobs, a long cold start may be rational. For interactive or short jobs, the same cold start may be wasteful.

## Burst Handling

The intake layer may hold jobs briefly to observe burst shape before routing.

The router should distinguish:

- one small job;
- many independent small jobs;
- a large batch that amortizes cold start;
- latency-sensitive interactive work;
- jobs requiring specific quality or model capabilities.

Callers may provide `metadata.routing.burst_size`, but the control plane must also observe bursts at intake/submit time because not every caller will supply correct metadata.
