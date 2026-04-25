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
