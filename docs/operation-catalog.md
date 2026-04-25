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
