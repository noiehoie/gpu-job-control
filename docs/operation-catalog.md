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
