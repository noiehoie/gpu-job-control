# RunPod Support Question v7: Serverless vLLM Hub Deploy vs GraphQL Diff

Updated: 2026-04-18 09:42 JST.

## Support-Ready Summary

We need RunPod Support to diagnose a blocker specifically on **Serverless vLLM** and confirm the exact difference between:

1. a GraphQL-created Serverless endpoint using `saveTemplate(imageName + env)` and `saveEndpoint`; and
2. the Console / Hub "Serverless vLLM" deploy flow for Hub ID `cm8h09d9n000008jvh2rqdsmb`.

Pods, Pod HTTP proxy execution, Pod `llm_heavy` generation, and Pod Network Volume attachment are proven healthy on the same account and API key. The remaining blocker is only the Serverless vLLM / Hub-template path.

## Cases

| Case | Symptom | Primary endpoint/template evidence | Priority |
|---|---|---|---|
| A: provisioning / placement / worker init | `throttled=1`, `initializing=0`, `ready=0`, `running=0`, `jobs.inQueue=1`, `/openai/v1/models` timeout | `vllm-886lfe61fzhhfg` / `n4gi1ni6kw`; retained endpoint `vllm-623t63akmshaoi` / `7nwbqj31ti` | P0 |
| B: dispatch / queue | worker reached `ready=1` or `running=1`, but job status stayed `IN_QUEUE` | earlier `Qwen/Qwen2.5-0.5B-Instruct` attempts | P1 / secondary |

Please prioritize **Case A** first. Case B may be related, but it can be treated as secondary unless internal logs show the same root cause.

## Investigation Keys

| Item | Value |
|---|---|
| Time window | 2026-04-18 00:00-01:00 UTC, plus retained endpoint inspection after that window |
| Hub ID under investigation | `cm8h09d9n000008jvh2rqdsmb` |
| Hub repo metadata reported externally | owner `runpod-workers`, title `vLLM`, release `v2.14.0` |
| Hub-derived image tested | `registry.runpod.net/runpod-workers-worker-vllm-main-dockerfile:17efb0e7d` |
| Raw Docker Hub image tested | `runpod/worker-v1-vllm:v2.14.0` |
| Retained disabled endpoint | `vllm-623t63akmshaoi` |
| Retained template | `7nwbqj31ti` |
| Hub-derived ADA_24 retest | endpoint `vllm-886lfe61fzhhfg`, template `n4gi1ni6kw` |
| Hub-derived AMPERE_80 retest | endpoint `vllm-4z0vha0ofxeupm`, template `0o8jdbjtw1` |
| CLI version tested | `runpodctl 2.1.9-673143d` |

## Core Questions For Support

1. The official docs show Hub deployment through `runpodctl serverless create --hub-id cm8h09d9n000008jvh2rqdsmb --name "my-vllm"`. Is Hub ID `cm8h09d9n000008jvh2rqdsmb` the correct stable vLLM Hub source for programmatic deployment?
2. Is there a GraphQL equivalent to `runpodctl serverless create --hub-id ...`, or is Hub deployment only supported through `runpodctl` / Console?
3. If GraphQL is the supported direct API path, can Support provide the exact resolved Serverless template and endpoint fields that Console/Hub deploy creates for Hub ID `cm8h09d9n000008jvh2rqdsmb`, so we can diff them field-by-field against our GraphQL-created endpoint?
4. For the official Hub vLLM release, what are the minimum and recommended values for `imageName`, `containerDiskInGb`, GPU pool, `ports`, `dockerArgs` or start command, readiness/health behavior, and OpenAI routing mode?
5. In Case A, why does endpoint health show `throttled=1` and `jobs.inQueue=1` while `initializing=0`, `ready=0`, `running=0`, and `/openai/v1/models` never becomes reachable?
6. Does `workersStandby` represent any warm or billable capacity when `workersMin=0`, or is it only an observed scaler state?
7. Is `workersMax=0` invalid for endpoint creation and treated as unset/default `3`? We now use `workersMin=0, workersMax=1` for canary creation and reserve `workersMax=0` only for a cleanup/quiesce attempt before deletion. Please confirm the intended spec.
8. Do `flashBootType=FLASHBOOT` and `ports=8000/http` change worker scheduling, image pull, readiness, or OpenAI gateway routing behavior for the official vLLM Serverless worker?
9. What is the exact expected native `/run` or `/runsync` input schema for Hub vLLM release `v2.14.0`? The public quickstart shows `{"input": {"prompt": "Hello World"}}`; is that still correct?
10. Is there any public API to fetch scheduler/router/worker-init logs for these endpoint IDs, or must Support inspect internal logs?

## CLI Surface Mismatch Evidence

The RunPod CLI documentation says Serverless endpoints can be created from either a template or a Hub repo:

```bash
runpodctl serverless create --name "my-endpoint" --template-id "tpl_abc123"
runpodctl hub search vllm
runpodctl serverless create --hub-id cm8h09d9n000008jvh2rqdsmb --name "my-vllm"
```

But the current Linux binary we installed reported:

```text
runpodctl 2.1.9-673143d
```

and returned:

```json
{"error":"unknown command \"hub\" for \"runpodctl\""}
```

`runpodctl serverless create --help` also showed no `--hub-id` flag. It requires `--template-id`.

## Short Account Health Evidence

This does not appear to be an account-wide GPU allocation failure. Bounded Pod canaries can allocate GPUs, serve HTTP through the RunPod proxy, run a standard `llm_heavy` job, attach a Network Volume, and clean up with no active billable resources.

Representative known-good Pod result:

```json
{
  "ok": true,
  "status": "succeeded",
  "provider": "runpod",
  "provider_job_id": "0fojxrmy4s1t81",
  "runtime_seconds": 27,
  "exit_code": 0,
  "gpu_probe_stdout": "NVIDIA GeForce RTX 3090, 24576 MiB",
  "billable_resources_after_cleanup": []
}
```

Network Volume canary also succeeded with RTX 4090 in `US-NC-1`:

```text
volume_probe.ok=true
volume_probe.write_read_delete=true
cleanup.ok=true
post_guard.providers.runpod.billable_resources=[]
```

## Detailed Evidence Appendix

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

The same Pod HTTP worker path also works through the standard `llm_heavy` job contract:

```bash
gpu-job submit examples/jobs/llm-heavy.runpod-pod.json --provider runpod --execute
```

Observed result:

```text
job_id=llm_heavy-20260418-081252-d9463abf
status=succeeded
exit_code=0
runtime_seconds=27
provider_job_id=t8lvo42moh2dwr
gpu_probe_stdout=NVIDIA GeForce RTX 3090, 24576 MiB
generate_ok=True
generate_text_chars=160
actual_cost_per_hour=0.46
post_submit_guard.providers.runpod.billable_resources=[]
```

Network Volume attachment also works when placed on available hardware in the volume's data center:

```text
gpuTypeId=NVIDIA GeForce RTX 4090
dataCenterId=US-NC-1
runtime_seconds=22.683
gpu_probe_stdout=NVIDIA GeForce RTX 4090, 24564 MiB
volume_probe.exists=true
volume_probe.is_dir=true
volume_probe.ok=true
volume_probe.write_read_delete=true
actual_cost_per_hour=0.69
actual_cost_guard.ok=true
cleanup.ok=true
post_pod_http_canary_guard.providers.runpod.billable_resources=[]
```

An RTX 3090 + US-NC-1 Network Volume attempt failed before Pod creation with:

```text
SUPPLY_CONSTRAINT: There are no longer any instances available with the requested specifications.
```

Switching only the GPU type to RTX 4090 succeeded, so the Network Volume path itself is not the blocker.

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

## Official Hub / CLI Facts To Compare Against

The RunPod CLI documentation says Serverless endpoints can be created from either a template or a Hub repo:

```bash
runpodctl serverless create --name "my-endpoint" --template-id "tpl_abc123"
runpodctl hub search vllm
runpodctl serverless create --hub-id cm8h09d9n000008jvh2rqdsmb --name "my-vllm"
```

The same documentation states that when `--hub-id` is used, GPU IDs and container disk size are automatically pulled from the Hub release config, and that each Serverless template can only be bound to one endpoint at a time.

The official vLLM quickstart says the easiest deployment path is RunPod Hub's ready-to-deploy vLLM repo. It also shows the native Serverless test request as:

```json
{"input": {"prompt": "Hello World"}}
```

External review of the Hub metadata reported these Hub-derived values:

```text
Hub repo ID: cm8h09d9n000008jvh2rqdsmb
Title: vLLM
Owner: runpod-workers
Release tag: v2.14.0
Hub worker image: registry.runpod.net/runpod-workers-worker-vllm-main-dockerfile:17efb0e7d
Hub containerDiskInGb: 150
Default GPU pools: ADA_80_PRO, AMPERE_80
```

These values differ materially from our failing GraphQL-created template:

```text
imageName: runpod/worker-v1-vllm:v2.14.0
containerDiskInGb: 30
gpuIds: ADA_24
flashBootType: FLASHBOOT
```

## Hub-Derived GraphQL Retest Results

After receiving external review, we changed the GraphQL-created template to match the reported Hub image and disk sizing:

```text
imageName=registry.runpod.net/runpod-workers-worker-vllm-main-dockerfile:17efb0e7d
containerDiskInGb=150
flashBootType omitted
model=facebook/opt-125m
MAX_MODEL_LEN=512
GPU_MEMORY_UTILIZATION=0.8
workersMin=0
workersMax=1
```

### Retest A: `ADA_24`

```text
endpoint_id=vllm-886lfe61fzhhfg
template_id=n4gi1ni6kw
gpuIds=ADA_24
workersStandby=1
result=failed
canary_status=TRANSPORT_ERROR
canary_error=The read operation timed out
health.jobs.inQueue=1
health.workers.throttled=1
health.workers.ready=0
disabled.workersMin=0
disabled.workersMax=0
deleteEndpoint=null
post_promotion_guard.ok=true
post_promotion_guard.providers.runpod.billable_resources=[]
```

### Retest B: `AMPERE_80`

```text
endpoint_id=vllm-4z0vha0ofxeupm
template_id=0o8jdbjtw1
gpuIds=AMPERE_80
workersStandby=1
result=failed
canary_status=HTTP_ERROR
http_status=500
error={"status":500,"title":"Internal Server Error","detail":"internal server error"}
disabled.workersMin=0
disabled.workersMax=0
deleteEndpoint=null
post_promotion_guard.ok=true
post_promotion_guard.providers.runpod.billable_resources=[]
```

These retests show that using the Hub-derived image and 150GB disk did not make the GraphQL-created Serverless vLLM endpoint usable. `ADA_24` still gets throttled/queued, while `AMPERE_80` fails earlier with an internal server error. This strengthens the question of whether the Hub deployment path sets additional non-template fields or uses a backend deployment path that is not reproduced by public `saveTemplate` + `saveEndpoint`.

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

## `runpodctl` Probe Results

We installed the current Linux `runpodctl` release and verified the CLI surface directly:

```text
runpodctl 2.1.9-673143d
```

The official docs currently show:

```bash
runpodctl hub search vllm
runpodctl serverless create --hub-id cm8h09d9n000008jvh2rqdsmb --name "my-vllm"
```

But the installed CLI returned:

```json
{"error":"unknown command \"hub\" for \"runpodctl\""}
```

`runpodctl serverless create --help` also showed no `--hub-id` flag. It requires `--template-id`.

We then tested whether public vLLM templates from `runpodctl template search vllm` can be bound to Serverless:

```bash
runpodctl serverless create \
  --template-id pvcdqlwm9r \
  --workers-min 0 \
  --workers-max 0 \
  --gpu-id "NVIDIA A40"
```

RunPod rejected this because the public template is a Pod template, not a Serverless template:

```json
{
  "error": "create endpoint: create endpoint: graphql: Serverless endpoints cannot use pod templates. Please use a serverless template.",
  "status": 500
}
```

Finally, we created a Serverless template with `runpodctl template create --serverless`, using the Hub-derived image, 150GB container disk, and `ports=8000/http`. Template creation succeeded:

```json
{
  "id": "t1drjw4fp9",
  "imageName": "registry.runpod.net/runpod-workers-worker-vllm-main-dockerfile:17efb0e7d",
  "isServerless": true,
  "containerDiskInGb": 150,
  "ports": ["8000/http"]
}
```

However, creating an endpoint from that template with `--workers-min 0 --workers-max 0` still returned `workersMax: 3`, and RunPod immediately created a worker object with:

```json
{
  "desiredStatus": "EXITED",
  "costPerHr": 1.39,
  "imageName": "registry.runpod.net/runpod-workers-worker-vllm-main-dockerfile:17efb0e7d",
  "ports": ["8000/http"]
}
```

A follow-up `runpodctl serverless update <endpoint-id> --workers-min 0 --workers-max 0` also returned `workersMax: 3`. We deleted the test endpoint and test template immediately. Post-guard confirmed no active RunPod billable resources.

This raises two additional support questions:

1. Why does `runpodctl serverless create --workers-max 0` return `workersMax: 3` and create a worker object?
2. Is there a supported scale-to-zero Hub deployment path that does not create a worker before the operator explicitly submits a canary job?

Based on RunPod AI's follow-up explanation, we now treat the intended scale-to-zero creation shape as `workersMin=0` with a positive `workersMax`, normally `1` for canaries. `workersMax=0` is now considered delete/quiesce-only in our tooling, and the canary planner rejects `workersMax < 1`. The remaining support issue is whether endpoint creation with `workersMin=0, workersMax=1` should create any worker object before the first request, and whether Hub/Console deployment injects additional worker-init or router metadata.

## Cleanup

For cleanup, we attempted a GraphQL quiesce update before endpoint deletion:

```json
{
  "workersMin": 0,
  "workersMax": 0
}
```

Then we deleted the endpoint. Based on later RunPod AI guidance and our own `runpodctl` probe, we no longer treat `workersMax=0` as a valid creation shape. Canary creation now uses `workersMin=0` and `workersMax=1`; `workersMax=0` is delete/quiesce-only.

Post-guard showed no active billable resources. A retained disabled endpoint may still show stale queued jobs, but all worker counts are zero:

```json
{
  "billable_resources": [],
  "estimated_hourly_usd": 0,
  "ok": true,
  "note": "A retained disabled endpoint may still show stale inQueue jobs, but workers are all zero and no billable resources are active."
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
