# RunPod Support Question v4: Need Official Serverless vLLM Hub Template Diff

Updated: 2026-04-18 08:05 JST.

We need RunPod Support to confirm the exact difference between:

1. A GraphQL-created Serverless endpoint using `saveTemplate(imageName + env)` and `saveEndpoint`.
2. The Console / Hub "Serverless vLLM" deploy flow.

Our current evidence suggests the failure is not a missing required endpoint scalar, but a template/runtime mismatch: the GraphQL template is only a raw Docker image template, while the Console / Hub vLLM flow likely uses repository/template plumbing that sets HTTP routing, ports, entrypoint, readiness, or worker mode.

Scope update: RunPod Pod lifecycle and Pod HTTP proxy execution are now proven separately. The remaining blocker is specifically the Serverless vLLM / Hub-template path.

## Direct Questions

1. What official Hub template ID or repository template should be used for Serverless vLLM when creating endpoints programmatically?
2. Can that Hub/Console Serverless vLLM template be referenced directly as `templateId` in `saveEndpoint`, instead of creating a new template with `saveTemplate(imageName=...)`?
3. What exact fields are present in the Console/Hub-created Serverless vLLM template that are absent from the `saveTemplate` response below?
4. Is `runpod/worker-v1-vllm:v2.14.0` supported for direct use as a plain GraphQL `saveTemplate` Serverless endpoint image, or only through a Hub wrapper/template?
5. Why does the endpoint health show `throttled: 1` and queued jobs while no worker ever becomes reachable through `/openai/v1/models`?
6. Is there any public API to fetch scheduler/router/worker init logs for this endpoint ID, or must Support inspect internal logs?

## Known-Good Pod Evidence

This is not an account-wide GPU allocation failure. A bounded RunPod Pod canary works through the same account and API key.

Standard gpu-job command:

```bash
gpu-job submit examples/jobs/smoke.runpod-pod.json --provider runpod --execute
```

Result:

```json
{
  "ok": true,
  "status": "succeeded",
  "provider": "runpod",
  "provider_job_id": "0fojxrmy4s1t81",
  "runtime_seconds": 27,
  "exit_code": 0
}
```

The canary created a Pod with:

```json
{
  "imageName": "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
  "gpuTypeId": "NVIDIA GeForce RTX 3090",
  "ports": "8000/http",
  "costPerHr": 0.46
}
```

It then successfully reached the proxy health endpoint:

```json
{
  "observed_runtime": true,
  "observed_http_worker": true,
  "gpu_probe": {
    "exit_code": 0,
    "stdout": "NVIDIA GeForce RTX 3090, 24576 MiB"
  },
  "cleanup": {
    "ok": true
  }
}
```

Post-guard after termination:

```json
{
  "ok": true,
  "estimated_hourly_usd": 0.0,
  "billable_resources": []
}
```

One Pod-side issue was identified and fixed: untagged `runpod/pytorch` resolved to `runpod/pytorch:latest`, and the provider log showed `manifest for runpod/pytorch:latest not found`. Pinning `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` fixed that. This is separate from the Serverless vLLM blocker.

## Failing Endpoint Evidence

Model used for the latest probe:

```text
facebook/opt-125m
```

This was chosen to avoid gated-model, large-model, and HF auth variables.

## Dashboard Log Evidence From Retained Endpoint

We reran the probe in a mode that disables the endpoint but does not delete it, so the endpoint remains visible in the Console for log inspection.

Current retained endpoint:

```json
{
  "id": "vllm-623t63akmshaoi",
  "name": "gpu-job-disabled",
  "templateId": "7nwbqj31ti",
  "workersMin": 0,
  "workersMax": 0,
  "gpuIds": "AMPERE_24"
}
```

Dashboard logs observed:

```text
image pull: runpod/worker-v1-vllm:v2.14.0: pending
image pull: runpod/worker-v1-vllm:v2.14.0: pending
```

This indicates that, at least for this retained run, RunPod did attempt to create a worker and got stuck during image pull before the OpenAI server became reachable.

### `saveTemplate`

```graphql
mutation {
  saveTemplate(input: {
    name: "gpu-job-vllm-support-diff-20260418064131",
    imageName: "runpod/worker-v1-vllm:v2.14.0",
    isServerless: true,
    containerDiskInGb: 30,
    volumeInGb: 0,
    dockerArgs: "",
    env: [
      { key: "MODEL_NAME", value: "facebook/opt-125m" },
      { key: "MAX_MODEL_LEN", value: "512" },
      { key: "GPU_MEMORY_UTILIZATION", value: "0.8" },
      { key: "MAX_CONCURRENCY", value: "1" },
      { key: "OPENAI_SERVED_MODEL_NAME_OVERRIDE", value: "facebook/opt-125m" }
    ]
  }) {
    id name imageName isServerless containerDiskInGb volumeInGb dockerArgs env { key value }
  }
}
```

Response:

```json
{
  "id": "ejlp9cqx06",
  "imageName": "runpod/worker-v1-vllm:v2.14.0",
  "isServerless": true,
  "containerDiskInGb": 30,
  "volumeInGb": 0,
  "dockerArgs": "",
  "env": [
    {"key": "MODEL_NAME", "value": "facebook/opt-125m"},
    {"key": "MAX_MODEL_LEN", "value": "512"},
    {"key": "GPU_MEMORY_UTILIZATION", "value": "0.8"},
    {"key": "MAX_CONCURRENCY", "value": "1"},
    {"key": "OPENAI_SERVED_MODEL_NAME_OVERRIDE", "value": "facebook/opt-125m"}
  ]
}
```

### `saveEndpoint`

```graphql
mutation {
  saveEndpoint(input: {
    flashBootType: FLASHBOOT,
    gpuCount: 1,
    gpuIds: "ADA_24",
    idleTimeout: 180,
    name: "gpu-job-vllm-support-diff-20260418064131",
    scalerType: "QUEUE_DELAY",
    scalerValue: 15,
    templateId: "ejlp9cqx06",
    workersMax: 1,
    workersMin: 0
  }) {
    id name gpuIds gpuCount idleTimeout locations flashBootType
    scalerType scalerValue templateId workersMax workersMin workersStandby networkVolumeId
  }
}
```

Response:

```json
{
  "id": "vllm-0r9pvbijdejiry",
  "gpuIds": "ADA_24",
  "gpuCount": 1,
  "idleTimeout": 180,
  "locations": null,
  "flashBootType": "FLASHBOOT",
  "scalerType": "QUEUE_DELAY",
  "scalerValue": 15,
  "templateId": "ejlp9cqx06",
  "workersMax": 1,
  "workersMin": 0,
  "workersStandby": 1,
  "networkVolumeId": null
}
```

### `GET /openai/v1/models`

Request:

```http
GET https://api.runpod.ai/v2/vllm-0r9pvbijdejiry/openai/v1/models
Authorization: Bearer <api key>
```

Result after 300 seconds:

```json
{
  "ok": false,
  "status": "TimeoutError",
  "body": "The read operation timed out"
}
```

Endpoint health after the timeout:

```json
{
  "jobs": {
    "completed": 0,
    "failed": 0,
    "inProgress": 0,
    "inQueue": 1,
    "retried": 0
  },
  "workers": {
    "idle": 0,
    "initializing": 0,
    "ready": 0,
    "running": 0,
    "throttled": 1,
    "unhealthy": 0
  }
}
```

## Native Queue API Test Also Fails

Request:

```http
POST https://api.runpod.ai/v2/<endpoint_id>/run
Authorization: Bearer <api key>
Content-Type: application/json

{"input":{"prompt":"Hello World","max_tokens":8}}
```

Initial response:

```json
{
  "id": "4e09fdc5-90d3-4383-9a65-3c66c2e1397d-e2",
  "status": "IN_QUEUE"
}
```

After about 591 seconds, it was still:

```json
{
  "id": "4e09fdc5-90d3-4383-9a65-3c66c2e1397d-e2",
  "status": "IN_QUEUE"
}
```

Endpoint health at that time:

```json
{
  "jobs": {
    "completed": 0,
    "failed": 0,
    "inProgress": 0,
    "inQueue": 1,
    "retried": 0
  },
  "workers": {
    "idle": 0,
    "initializing": 0,
    "ready": 0,
    "running": 0,
    "throttled": 1,
    "unhealthy": 0
  }
}
```

For `Qwen/Qwen2.5-0.5B-Instruct`, we also observed workers reaching `ready: 1` or `running: 1`, but the job status stayed `IN_QUEUE`.

## GPU Pool Validation

We verified that `gpuIds` expects RunPod GPU pool IDs, not concrete GPU names.

For example, `gpuIds=NVIDIA L4` fails before endpoint creation with `Invalid GPU Pool ID`. Valid pool IDs reported by the API were:

```text
AMPERE_16, AMPERE_24, ADA_24, AMPERE_48, ADA_48_PRO, AMPERE_80,
ADA_80_PRO, HOPPER_141, ADA_32_PRO, BLACKWELL_96, BLACKWELL_180
```

So the current failing canary uses:

```json
{
  "gpuIds": "ADA_24",
  "gpuCount": 1,
  "locations": null,
  "workersMin": 0,
  "workersMax": 1,
  "flashBootType": "FLASHBOOT"
}
```

## Attempted Template Discovery

We also tried to discover the official vLLM template programmatically through GraphQL `podTemplates`.

Command output summary:

```json
{
  "label": "official",
  "errors": null,
  "count": 13,
  "vllm_hits": []
}
```

Community template search does find vLLM templates, but the hits are Pod templates, not Serverless templates:

```json
{
  "label": "community",
  "errors": null,
  "count": 906,
  "vllm_hits": [
    {
      "id": "iqilnw0ymf",
      "name": "vllm-latest",
      "imageName": "vllm/vllm-openai:latest",
      "isServerless": false,
      "ports": "8000/http,22/tcp,22/udp",
      "dockerArgs": "--host 0.0.0.0 --port 8000 --model meta-llama/Meta-Llama-3.1-8B-Instruct --dtype bfloat16 --enforce-eager --gpu-memory-utilization 0.95 --api-key <redacted> --max-model-len 8128"
    },
    {
      "id": "pvcdqlwm9r",
      "name": "vLLM Latest",
      "imageName": "vllm/vllm-openai:latest",
      "isServerless": false,
      "ports": "8000/http",
      "dockerArgs": "Qwen/Qwen3-8B --host 0.0.0.0 --port 8000 --dtype auto --enforce-eager --gpu-memory-utilization 0.95 --max-model-len 8128"
    }
  ]
}
```

This is why we need Support to identify the official Console / Hub Serverless vLLM template ID or the API endpoint that exposes it.

## Cleanup

For each test endpoint, we set:

```json
{
  "workersMin": 0,
  "workersMax": 0
}
```

Then we deleted the endpoint. Post-guard showed:

```json
{
  "billable_resources": [],
  "estimated_hourly_usd": 0,
  "ok": true,
  "serverless_queue": []
}
```

## Attached File

We can attach the probe script as:

```text
runpod-vllm-support-probe.py.txt
```

It is a byte-for-byte copy of:

```text
runpod-vllm-support-probe.py
```

The script creates the template and endpoint, probes `/openai/v1/models`, captures health, disables workers, deletes the endpoint, and runs the billing guard.
