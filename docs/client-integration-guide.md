# Client Integration Guide

For downstream systems, the integration order is:

1. use the [Generic System Integration Prompt](generic-system-integration-prompt.md)
2. emit the [Caller Contract](caller-contract.md)
3. choose an operation from the [Operation Catalog](operation-catalog.md)
4. send that request to the [Public API](public-api.md)

## Minimal Success Path

Set the API URL and token:

```bash
export GPU_JOB_API_URL=http://127.0.0.1:8765
export GPU_JOB_API_TOKEN=replace-me
```

Validate a request:

```bash
curl -sS -H "Authorization: Bearer $GPU_JOB_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d @examples/caller-requests/asr.transcribe.json \
  "$GPU_JOB_API_URL/validate"
```

Submit it:

```bash
curl -sS -H "Authorization: Bearer $GPU_JOB_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d @examples/caller-requests/asr.transcribe.json \
  "$GPU_JOB_API_URL/submit"
```

Poll status:

```bash
curl -sS -H "Authorization: Bearer $GPU_JOB_API_TOKEN" \
  "$GPU_JOB_API_URL/jobs/<job_id>"
```

Verify artifacts:

```bash
curl -sS -H "Authorization: Bearer $GPU_JOB_API_TOKEN" \
  "$GPU_JOB_API_URL/verify/<job_id>"
```

## Python reference client

Use [`src/gpu_job/public_client.py`](../src/gpu_job/public_client.py) as the minimal stdlib-based reference client.

```python
from gpu_job.public_client import PublicClient

client = PublicClient("http://127.0.0.1:8765", token="replace-me")
payload = {
    "contract_version": "gpu-job-caller-request-v1",
    "operation": "llm.generate",
    "input": {"uri": "text://hello", "parameters": {"prompt": "hello"}},
    "output_expectation": {
        "target_uri": "local://caller-output",
        "required_files": ["result.json", "metrics.json", "verify.json"],
    },
    "limits": {"max_runtime_minutes": 5, "max_cost_usd": 1, "max_output_gb": 1},
    "idempotency": {"key": "example-001"},
    "caller": {
        "system": "example",
        "operation": "demo",
        "request_id": "req-001",
        "version": "1",
    },
}
print(client.validate(payload))
```

## Minimal Failure Examples

Validation reject:

```bash
curl -sS -H "Authorization: Bearer $GPU_JOB_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"operation":"unknown.op"}' \
  "$GPU_JOB_API_URL/validate"
```

Auth failure:

```bash
curl -i -sS "$GPU_JOB_API_URL/catalog/operations"
```

Backpressure or quota failure:

```text
HTTP 429 or 409 with an error class from docs/error-codes.md
```

Artifact verification failure:

```text
/verify/<job_id> returns a verification payload whose ok field is false.
Treat the job as failed even if provider execution returned success.
```

## Quickstarts

- ASR: `examples/caller-requests/asr.transcribe.json`
- LLM: `examples/caller-requests/llm.generate.json`
- OCR document: `examples/caller-requests/ocr.document.json`
- OCR image: `examples/caller-requests/ocr.image.json`
- Generic GPU container: `examples/caller-requests/gpu.container.run.json`

## Generic GPU Lane Examples

Use these examples when an external system must deliberately exercise one
product lane. The only caller-facing lane selector is
`preferences.execution_lane_id`.

| Lane | Example | Runtime precondition |
| --- | --- | --- |
| `modal_function` | `examples/caller-requests/gpu.container.run.modal_function.json` | Modal credentials and guard clean |
| `runpod_pod` | `examples/caller-requests/gpu.container.run.runpod_pod.json` | RunPod pod create/terminate guard clean |
| `runpod_serverless` | `examples/caller-requests/gpu.container.run.runpod_serverless.json` | `RUNPOD_GPU_TASK_ENDPOINT_ID` points to an approved endpoint |
| `vast_instance` | `examples/caller-requests/gpu.container.run.vast_instance.json` | explicit guarded direct-instance approval metadata at execution time |
| `vast_pyworker_serverless` | `examples/caller-requests/gpu.container.run.vast_pyworker_serverless.json` | `VAST_PYWORKER_ENDPOINT_URL` points to an approved pyworker endpoint |

If the precondition is missing, the server rejects or fails the job. Callers
must not implement hidden local fallback or direct provider calls.

## Non-Python Callers

Use the OpenAPI document and JSON Schema directly:

```text
schemas/gpu-job-public-api.openapi.json
schemas/gpu-job-caller-request.schema.json
config/operation-catalog.json
```

Any language can integrate by generating a JSON object that validates against
the caller schema, choosing only operations from the catalog, and calling the
public endpoint set in [Public API](public-api.md).

## Integration constraints

- callers do not select providers
- callers may request a documented product lane with `preferences.execution_lane_id`
- callers do not emit execution-job fields directly
- callers must supply idempotency and limits
- callers must fail closed on local validation failure
- callers preserve the same idempotency key across retries
- callers never put provider secrets in payloads
