# Caller Contract

`gpu-job-control` accepts two shapes:

1. internal execution-job shape
2. caller-facing canonical request shape

The finished product surface is the caller-facing shape.

## Current contract

- Schema file: [`schemas/gpu-job-caller-request.schema.json`](../schemas/gpu-job-caller-request.schema.json)
- Contract version: `gpu-job-caller-request-v1`
- Public schema endpoint: `/schemas/caller-request`

## Required top-level fields

- `contract_version`
- `operation`
- `input`
- `output_expectation`
- `limits`
- `idempotency`
- `caller`

Optional:

- `trace_context`
- `preferences`

## Lane Request Field

Use `preferences.execution_lane_id` when a caller must exercise a specific
published product lane. It is a closed enum:

- `modal_function`
- `runpod_pod`
- `runpod_serverless`
- `vast_instance`
- `vast_pyworker_serverless`

This field pins the parent provider for that lane and disables provider
fallback for that request. If the lane is unknown, or if the lane's provider
cannot pass health, capability, startup, endpoint, or policy checks, the request
fails closed. It does not enable
`provider_module_routing.routing_by_module_enabled`; that flag remains `false`.

`preferences.provider_module_id` is kept only as a compatibility alias. New
integrations must use `execution_lane_id`.

## Forbidden top-level fields

Caller requests must not send execution-job fields directly:

- `job_type`
- `input_uri`
- `output_uri`
- `worker_image`
- `gpu_profile`
- `provider`
- `provider_job_id`

Those are produced by the deterministic compiler inside `gpu-job-control`.

## Fail-closed rule

If a request cannot be compiled without guessing, it is rejected locally and is not sent to a provider.

## Versioning

- Current contract: `gpu-job-caller-request-v1`
- Compatibility rule: new optional fields may be added without changing the version; required field changes require a new contract version.

## Product Boundary

`gpu-job-control` is not a generic small-LLM wrapper. It is the public product
boundary for workloads that should not run on local fixed resources.

Callers express that boundary with `preferences`:

- `quality_tier`: `smoke`, `development`, `degraded`, or `production_quality`
- `local_fixed_resource_policy`: `unsuitable`, `suitable`, or `unknown`
- `model_size_billion_parameters`: numeric model size, when known
- `model_size_class`: `under_70b`, `at_least_70b`, or `unknown`
- `quality_requires_gpu`: boolean

For `llm.generate`, `quality_tier=production_quality` is accepted only when all
of these are true:

- `quality_requires_gpu=true`
- `local_fixed_resource_policy=unsuitable`
- `model_size_billion_parameters >= 70` or `model_size_class=at_least_70b`

Smaller LLMs may be used for `smoke`, `development`, or `degraded` requests, but
they are not a production-quality external-GPU contract.

## Generic GPU Workloads

`gpu-job-control` is a generic GPU broker, not an ASR service. ASR canaries prove
provider lifecycle, cleanup, artifact, and secret handling, but they do not
limit provider lanes to ASR.

When no named operation fits, callers may use `gpu.container.run`. The request
must still be deterministic and bounded:

- `input.parameters.workload` is required.
- `limits` must be finite.
- `output_expectation.required_files` must list the artifact contract.
- provider credentials and provider-native routing hints remain forbidden.

Reference lane-specific examples:

- [`gpu.container.run.modal_function.json`](../examples/caller-requests/gpu.container.run.modal_function.json)
- [`gpu.container.run.runpod_pod.json`](../examples/caller-requests/gpu.container.run.runpod_pod.json)
- [`gpu.container.run.runpod_serverless.json`](../examples/caller-requests/gpu.container.run.runpod_serverless.json)
- [`gpu.container.run.vast_instance.json`](../examples/caller-requests/gpu.container.run.vast_instance.json)
- [`gpu.container.run.vast_pyworker_serverless.json`](../examples/caller-requests/gpu.container.run.vast_pyworker_serverless.json)
