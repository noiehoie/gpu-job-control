# RunPod Self-Hosted Endpoint Research

This note records the RunPod-specific facts that must be understood before creating another self-hosted LLM endpoint.

## Goal

The preferred RunPod path is not a one-off public endpoint. The target is a controlled self-hosted Serverless endpoint that can:

- run a selected LLM model;
- use zero warm capacity by default;
- reuse model storage or model cache across cold starts;
- expose an OpenAI-compatible API when possible;
- prove lifecycle safety through canaries before promotion;
- shut down without leaving billable resources.

## RunPod Surfaces

RunPod exposes several different surfaces that must not be confused.

| Surface | What It Is | Use in gpu-job-control |
| --- | --- | --- |
| Public Endpoints | RunPod-operated pre-deployed models | Fast canary and emergency LLM path |
| Serverless Endpoints | Operator-created endpoint using a template | Main self-hosted target |
| Serverless Templates / Serverless Repos | Reusable worker images and settings | Preferred starting point before custom Docker |
| Pod Templates | Reusable pod images/settings | Useful for staging, debugging, and volume preparation |
| Network Volumes | Persistent storage mounted at `/runpod-volume` | Model/artifact/cache storage, guarded as fixed cost |
| Cached Models | RunPod-managed Hugging Face model cache for endpoints | Preferred cold-start reduction path for HF models |
| S3-compatible Volume API | Direct file access to network volumes | Stage or inspect files without launching a paid pod |

## Current Route Status

| Route | Status | Evidence |
| --- | --- | --- |
| Public OpenAI-compatible endpoint | Proven | `qwen3-32b-awq` canary returns in about 1-2 seconds through the worker path. |
| Self-hosted Serverless vLLM from raw GraphQL template | Blocked | Endpoint and template can be created, but workers remain unreachable and requests stay queued. See [RunPod Support Question](runpod-support-question.md). |
| Hub/Console vLLM template path | Needs RunPod/template diff | Official docs point users to the Hub ready-to-deploy vLLM repo; the exact programmatic template/repo binding must be confirmed. |
| Pod / Network Volume lifecycle | Lifecycle proven | `gpu-job runpod canary-pod-lifecycle --execute` created an RTX 3090 pod, observed `desiredStatus=RUNNING`, terminated it, and post-guard showed no billable pods. |

## Key Official Facts

### Serverless endpoints

The GraphQL `saveEndpoint` mutation creates or updates endpoints. Important fields:

- `gpuIds`: e.g. `AMPERE_16`, `AMPERE_24`, `ADA_24`, `AMPERE_48`, `ADA_48_PRO`, `AMPERE_80`, `ADA_80_PRO`.
- `templateId`: required.
- `workersMin`: minimum active workers. Use `0` for scale-to-zero.
- `workersMax`: maximum concurrent workers.
- `idleTimeout`: idle worker shutdown delay.
- `networkVolumeId`: optional volume attachment.
- `flashBootType: FLASHBOOT`: cold-start optimization.
- `scalerType`: `QUEUE_DELAY` or `REQUEST_COUNT`.
- `scalerValue`: target scaler value.

Deletion has an important precondition: set both `workersMin` and `workersMax` to `0` before deleting an endpoint.

### Templates

Templates are created with `saveTemplate`.

Serverless templates must set:

- `isServerless: true`;
- `containerDiskInGb`;
- `imageName`;
- `name`;
- `volumeInGb: 0`.

Private images require `containerRegistryAuthId`. Public or provider-owned images avoid registry credentials in the first canary.

### vLLM worker

RunPod's vLLM worker is the first self-hosted LLM path to test because it already supports:

- Hugging Face model IDs through `MODEL_NAME`;
- OpenAI-compatible chat/models routes;
- vLLM engine settings through environment variables;
- `HF_TOKEN` for gated/private models;
- `MAX_MODEL_LEN`, `QUANTIZATION`, `TENSOR_PARALLEL_SIZE`, and `GPU_MEMORY_UTILIZATION`;
- `OPENAI_SERVED_MODEL_NAME_OVERRIDE` for model aliases;
- `MAX_CONCURRENCY` at the RunPod worker layer.

Custom Docker should be delayed until the official vLLM worker path is proven insufficient.

### Cached models

RunPod cached models are a distinct mechanism from manually placing model files on a network volume.

For an endpoint, the operator can set the Model field to a Hugging Face model ID. RunPod then attempts to place workers on hosts with that model cached, or downloads the model before the worker starts. This matters because model download time is not charged as worker execution time in this path.

Cached models are visible to workers under:

```text
/runpod-volume/huggingface-cache/hub/
```

The path follows Hugging Face cache conventions:

```text
/runpod-volume/huggingface-cache/hub/models--{org}--{name}/snapshots/{hash}/
```

Official docs state that cached models can start significantly faster than loading the same model from a network volume.

Limitations:

- one cached model per endpoint;
- repositories with multiple quantization variants may download all variants;
- private non-Hugging-Face models are not suitable for this mechanism and should be baked into an image or staged separately.

### Network volumes

Network volumes remain useful, but their role is narrower than "always store every model there".

Good uses:

- durable model artifacts not hosted on Hugging Face;
- intermediate outputs and large artifacts;
- custom worker assets;
- caches that are not supported by RunPod cached-model integration;
- controlled reproduction of a model snapshot.

Risks:

- loading large model weights from a network volume can still dominate cold start;
- region/datacenter placement affects GPU availability;
- volume cost is fixed recurring spend and must stay in the approved budget;
- volume deletion or resize is destructive and requires explicit approval.

The S3-compatible API can access network volumes without launching a pod. Serverless workers see volume files at `/runpod-volume/...`; S3 clients address them as `s3://NETWORK_VOLUME_ID/...`.

### Pods

Pods are the most controllable RunPod fallback path because the lifecycle is explicit: create, observe, run, terminate. They are not automatically scale-to-zero serverless workers, so every Pod path must be treated as a bounded lifecycle operation with a hard cost cap.

The first deterministic Pod canary uses the official Pod creation surface:

- create with `podFindAndDeployOnDemand` semantics through the RunPod SDK;
- use `runpod/pytorch` and `dockerArgs="bash -lc 'nvidia-smi; sleep 300'"` for a minimal GPU proof;
- disable public IP and SSH;
- require a clean pre-guard before creation;
- estimate maximum cost from the selected GPU hourly price and `max_uptime_seconds`;
- observe `desiredStatus=RUNNING` or runtime uptime before declaring lifecycle success;
- terminate the Pod in a `finally` block;
- require post-guard to report no active billable Pods.

Operator commands:

```bash
gpu-job runpod plan-pod-worker
gpu-job runpod canary-pod-lifecycle --max-uptime-seconds 60 --max-estimated-cost-usd 0.02 --execute
```

The first successful canary used:

```text
gpuTypeId=NVIDIA GeForce RTX 3090
imageName=runpod/pytorch
uninterruptablePrice=0.22 USD/hour
max_uptime_seconds=60
estimated_cost_usd=0.003667
observed_runtime=true
post_pod_canary_guard.ok=true
post_pod_canary_guard.providers.runpod.billable_resources=[]
```

This promotes the Pod / Network Volume route to `lifecycle_proven`, not to `production_primary`. The next gate is a real HTTP worker on a Pod with explicit health, artifact, timeout, and teardown behavior.

## Community Signals

Community discussions agree with the official model:

- heavy workloads should use a small CUDA image plus models on cache/volume plus a deliberate handler;
- downloading tens of GB during worker init is a cold-start and cost failure mode;
- network volumes can help, but they do not magically eliminate all model load time;
- for serverless LLMs, model materialization often dominates container startup.

Treat community reports as operational warnings, not as policy proof. Convert them into measurable canary gates.

## Candidate Self-Hosted Designs

### Design A: Official vLLM worker + cached Hugging Face model

Use when:

- model is hosted on Hugging Face;
- vLLM supports the model architecture;
- one model per endpoint is acceptable;
- OpenAI-compatible API is desired.

Endpoint shape:

```text
template image: runpod/worker-v1-vllm:<pinned-version>
endpoint Model field: <hugging-face-model-id>
env:
  MODEL_NAME=<hugging-face-model-id>
  MAX_MODEL_LEN=<bounded-context>
  QUANTIZATION=<awq|gptq|... when needed>
  GPU_MEMORY_UTILIZATION=0.90
  OPENAI_SERVED_MODEL_NAME_OVERRIDE=<stable-model-alias>
  MAX_CONCURRENCY=<small integer for canary>
workersMin=0
workersMax=1
gpuCount=1
idleTimeout=90
flashBootType=FLASHBOOT
scalerType=QUEUE_DELAY
scalerValue=15
locations=<empty for first canary>
```

This is the preferred next experiment.

gpu-job-control now exposes that experiment as a deterministic promotion helper:

```bash
gpu-job runpod plan-vllm-endpoint \
  --model Qwen/Qwen2.5-0.5B-Instruct \
  --image runpod/worker-v1-vllm:v2.14.0
```

The plan command performs no provider mutation. It prints the exact template, endpoint shape, secret reference, and safety invariants.

Promotion requires an explicit execute flag:

```bash
gpu-job runpod promote-vllm-endpoint \
  --model Qwen/Qwen2.5-0.5B-Instruct \
  --image runpod/worker-v1-vllm:v2.14.0 \
  --execute
```

The promotion helper must keep these invariants:

- `workersMin=0`;
- `workersMin=0` is the fixed warm-capacity guard; `workersStandby` may be observed in API responses but is not accepted by `EndpointInput`;
- `workersMax=1` for the first canary;
- clean pre-guard before creation;
- short OpenAI-compatible canary before traffic promotion;
- clean post-guard after canary;
- failed canaries disable and delete the endpoint.

The default canary timeout is intentionally short: 180 seconds. If RunPod leaves the request queued without assigning a worker, the helper treats that as no usable capacity for the current promotion attempt and deletes the endpoint.

RunPod support guidance refined the first self-hosted vLLM canary:

- leave `locations` empty so the scheduler can use any eligible datacenter;
- start with one known GPU class instead of a broad tier list;
- use a more forgiving `idleTimeout` of 60-120 seconds and `QUEUE_DELAY` of 10-30 seconds;
- compare the GraphQL-created endpoint object against a Console/Hub-created endpoint if workers stay at `initializing=0`.

The helper therefore defaults to `gpuIds=ADA_24`, `gpuCount=1`, empty `locations`, `idleTimeout=90`, `scalerValue=15`, and a 300-second OpenAI canary timeout. Empty optional endpoint fields such as `locations` and `networkVolumeId` are omitted from the GraphQL input.

RunPod `saveEndpoint.gpuIds` requires GPU pool IDs, not concrete GPU type names. A direct canary using `gpuIds=NVIDIA L4` failed before endpoint creation with `Invalid GPU Pool ID`; the error listed valid pool IDs as `AMPERE_16`, `AMPERE_24`, `ADA_24`, `AMPERE_48`, `ADA_48_PRO`, `AMPERE_80`, `ADA_80_PRO`, `HOPPER_141`, `ADA_32_PRO`, `BLACKWELL_96`, and `BLACKWELL_180`. Concrete GPU type names are only valid as exclusions inside a pool, for example `ADA_24,-NVIDIA L4`. The helper validates this locally before calling RunPod.

### Design B: Official vLLM worker + attached network volume

Use when:

- cached-model support is insufficient;
- a specific snapshot must be staged and preserved;
- the model is not suitable for RunPod cached models.

Endpoint shape is similar, but `MODEL_NAME` may point to a local path under `/runpod-volume/...`.

This path must measure cold-start and first-token time carefully because model reads from volume can still be slow.

Current status: unresolved for Serverless vLLM because the raw GraphQL-created endpoint does not reach a usable worker. The Pod lifecycle path is separately proven and can be used to develop a non-serverless Pod-hosted worker if the Hub/Serverless path remains blocked.

### Design C: Custom worker image + network volume

Use only after A and B fail.

Good for:

- non-vLLM runtimes;
- special VLM/ASR/video workers;
- custom validation/orchestration;
- models not hosted on Hugging Face.

Requirements:

- small image;
- no runtime package installation;
- clear health endpoint or first-job canary;
- deterministic artifact contract;
- explicit input/output staging;
- strict teardown and guard verification.

## Promotion Pipeline

No self-hosted endpoint should become production-primary immediately.

Promotion states:

```text
template_planned
  -> endpoint_created
  -> scale_to_zero_verified
  -> models_route_ok
  -> one_token_generation_ok
  -> short_generation_ok
  -> long_context_canary_ok
  -> queue_timeout_cancel_ok
  -> clean_post_guard_ok
  -> controlled_canary
  -> production_primary
```

Required canaries:

1. `GET /openai/v1/models` or native equivalent succeeds.
2. 1-token generation succeeds.
3. 16-token deterministic response succeeds.
4. 1K token context succeeds.
5. target production-shape context succeeds.
6. queue timeout cancels provider-side job.
7. endpoint has `workersMin=0` and no standby/warm workers.
8. endpoint queue is empty after canary.
9. `gpu-job guard` reports no unapproved billable resource.

Failed canary response:

1. set `workersMax=0`;
2. delete endpoint if safe;
3. delete template only if no endpoint uses it and delete preconditions pass;
4. keep network volumes unless explicitly approved for deletion;
5. persist failure reason and measured timings.

## Deterministic Fields to Add

Self-hosted RunPod endpoints need structured policy fields.

```json
{
  "runpod": {
    "endpoint_mode": "public_openai | self_vllm_cached_model | self_vllm_volume | custom_worker",
    "template_source": "runpod_official | runpod_repo | operator_image",
    "model_source": "runpod_public | huggingface_cached | network_volume | baked_image",
    "model_id": "Qwen/Qwen3-32B-AWQ",
    "model_cache_required": true,
    "network_volume_id": "optional",
    "gpu_ids": "AMPERE_24",
    "workers_min": 0,
    "workers_max": 1,
    "idle_timeout_seconds": 5,
    "flashboot": true,
    "max_concurrency": 1
  }
}
```

Hard gates:

- reject if `workers_min > 0` unless paid warm capacity is explicitly selected;
- reject if an endpoint has unbounded queue wait;
- reject if template image is unpinned in production;
- reject if model source is Hugging Face gated/private and no approved secret reference exists;
- reject if network volume is unknown or exceeds approved storage budget;
- reject if the selected GPU tier cannot fit the declared model/context.

## Immediate Next RunPod Plan

1. Query RunPod account for existing templates, endpoints, volumes, and provider queues.
2. Identify official or Hub vLLM template options available through the console/API.
3. Build `gpu-job runpod plan-vllm-endpoint` without creating resources.
4. Build `gpu-job runpod promote-vllm-endpoint --execute` with:
   - scale-to-zero defaults;
   - pre/post guard;
   - automatic delete on failed canary;
   - explicit persistent volume allowlist;
   - endpoint state persisted only after canary success.
5. Start with a small public Hugging Face model before moving to 32B/70B class models.
