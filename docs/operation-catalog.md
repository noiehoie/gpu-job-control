# Operation Catalog

The product accepts a closed operation catalog. Free-form `job_type` is not part of the caller-facing API.

- Catalog file: [`config/operation-catalog.json`](../config/operation-catalog.json)
- Public catalog endpoint: `/catalog/operations`
- Catalog version: `gpu-job-operation-catalog-v1`

## Current operations

- `asr.transcribe`
- `asr.transcribe_diarize`
- `llm.generate`
- `embedding.embed`
- `ocr.document`
- `ocr.image`
- `gpu.container.run`
- `smoke.gpu`

Each operation defines:

- input contract
- output contract
- required verify files
- artifact contract
- failure taxonomy
- required secrets
- allowed lanes
- forbidden lanes
- deterministic execution-job defaults

## LLM Production Boundary

`llm.generate` is the production text-generation operation, but
`gpu-job-control` does not treat every LLM call as an external-GPU workload.
For `preferences.quality_tier=production_quality`, the caller must also send:

- `preferences.quality_requires_gpu=true`
- `preferences.local_fixed_resource_policy=unsuitable`
- `preferences.model_size_billion_parameters >= 70` or
  `preferences.model_size_class=at_least_70b`

Small LLM requests remain valid for `smoke`, `development`, or `degraded`
quality tiers. They are not production-quality GPU workload evidence.

## Generic Lane Boundary

ASR is only the first fully proven workload family; it is not the product
boundary. Every cloud lane is modeled as a generic GPU execution lane:

- `modal_function`
- `runpod_pod`
- `runpod_serverless`
- `vast_instance`
- `vast_pyworker_serverless`

The operation catalog therefore keeps all five lanes eligible for the public
GPU operations, including `llm.generate`, OCR, embedding, ASR, and
`gpu.container.run`. Runtime promotion is still evidence-gated: Modal may be
production primary, RunPod may be conditional, and Vast may remain
reserve/canary without turning the catalog into an ASR-only design.

`gpu.container.run` is the generic escape hatch for bounded GPU workloads that
do not fit the named operations. It is still a closed operation: the caller must
provide `input.parameters.workload`, explicit limits, artifact expectations, and
an idempotency key. It is not permission to send provider-specific payloads or
credentials.

When a caller needs a specific lane, it sets
`preferences.execution_lane_id` to one of the `allowed_lanes`. The router pins
the parent provider for that lane and does not fall back to another provider. An
unknown lane is rejected before provider selection.
