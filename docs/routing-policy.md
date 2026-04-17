# Routing policy

`gpu-job-control` does not treat cold start as a universal provider failure.

Short interactive jobs use strict startup limits. Long batch jobs use amortized startup limits, where startup is acceptable when it is a small fraction of the total allowed runtime and still below a hard maximum.

## Current policy fields

- `startup_policy.mode=strict`: reject providers whose estimated startup exceeds `max_startup_seconds`.
- `startup_policy.mode=amortized`: allow slower providers when `estimated_startup_seconds / max_runtime_seconds` is below `max_startup_fraction`.
- `hard_max_startup_seconds`: absolute upper bound even for batch jobs.
- `metadata.routing.estimated_cpu_runtime_seconds`: caller-side estimate for the non-GPU or current fallback path.
- `metadata.routing.estimated_gpu_runtime_seconds`: caller-side estimate for the requested GPU path after startup.
- `metadata.routing.estimated_input_tokens`: estimated prompt/input tokens. If omitted, text prompts are estimated as `chars / 4`.
- `metadata.routing.batch_size`: number of items that amortize provider startup.
- `metadata.routing.deadline_seconds`: maximum useful end-to-end latency for this job.
- `metadata.routing.quality_requires_gpu`: allow GPU even when it is not faster than CPU/local because quality requires it.

This keeps low-latency canaries on Modal while allowing Vast.ai or RunPod for longer ASR batches, VLM/OCR, video generation, and heavy LLM jobs when their lower hourly cost or larger GPU availability justifies cold start.

Routing now evaluates each candidate as:

```text
expected_total_seconds =
  queue_wait_seconds
  + estimated_startup_seconds / batch_size
  + estimated_gpu_runtime_seconds
```

If `estimated_cpu_runtime_seconds` is present and `expected_total_seconds >= estimated_cpu_runtime_seconds`, the provider is rejected unless `quality_requires_gpu=true`.

For `llm_heavy`, the current Ollama profile also has `ollama_max_input_tokens=10000`. Jobs above that estimate are not sent to local Ollama and must be handled by another provider or a caller-side fallback until paid external LLM workers are registered.

## Backpressure

`POST /submit?provider=auto&execute=1` is direct execution. It now checks provider capacity before execution. If the selected provider is already at `config/execution-policy.json` concurrency limit, the API returns:

```json
{
  "ok": false,
  "error": "provider concurrency limit reached",
  "status_code": 429,
  "retry_after_seconds": 30,
  "capacity": {
    "provider": "ollama",
    "max_concurrent": 1,
    "active": 1,
    "queued": 0
  }
}
```

The response also includes the HTTP `Retry-After` header. Callers must treat 429 as backpressure, not as model failure.

`GET /guard` and `GET /queue` include `capacity` with provider limits, active counts, queued counts, saturation, available slots, and expected wait seconds.

RunPod serverless endpoint health is included in `guard.providers.runpod.serverless_queue`. `jobs.inQueue` is not an hourly billing resource, but it is a real wait source. Routing adds this external queue depth to `queue_wait_seconds`, so deadline-sensitive jobs avoid RunPod when an endpoint queue is already backed up.

Do not purge RunPod endpoint queues automatically. Queue purge cancels existing provider-side jobs and requires operator confirmation.

## External LLM Workers

`llm_heavy` has three execution tiers:

- `ollama`: fixed-cost resident model. Used for short prompts below `ollama_max_input_tokens`.
- `modal`: executable GPU LLM canary worker. It runs `src/gpu_job/modal_llm.py` on Modal L4 and writes the standard artifact contract.
- `runpod`: executable only when an existing serverless LLM endpoint is configured or discoverable. The endpoint must accept `input.prompt`, `input.system_prompt`, `input.model`, and `input.max_tokens`, and return one of `text`, `response`, `output`, `generated_text`, `answer`, or OpenAI-style `choices`.
- `runpod` can also target an OpenAI-compatible RunPod endpoint by setting `RUNPOD_LLM_ENDPOINT_ID`, `RUNPOD_LLM_ENDPOINT_MODE=openai`, and `RUNPOD_LLM_MODEL_OVERRIDE` in the service environment. This is the preferred canary path for RunPod Public Endpoints because it avoids creating a private endpoint before basic provider liveness is proven.

Current policy prefers `modal` before `runpod` for long LLM jobs because Modal execution is verified and RunPod may have provider-side queue backlog.

## Routing v5: Intake Buffer and Group Planning

The primary production path is now `POST /intake`, not direct `POST /submit`.

`/intake` stores jobs as `buffered` for a short policy-controlled hold window. The worker then groups jobs by:

- `metadata.source_system`
- `job_type`
- `gpu_profile`
- `metadata.task_family` or `metadata.purpose`

The group planner computes observed burst size even when callers did not declare `metadata.routing.burst_size`. It writes:

```json
{
  "metadata": {
    "routing": {
      "observed_burst_size": 500,
      "effective_burst_size": 500,
      "burst_size": 500,
      "batch_size": 500
    },
    "intake": {
      "state": "planned",
      "group_size": 500,
      "selected_provider": "modal"
    }
  }
}
```

This prevents the first job in a large fanout from being routed as a single light job.

`queued` jobs are planned but not yet provider-committed. `starting` and `running` jobs are provider-committed. Automatic merge/replan is safe before commit and destructive after commit.

`POST /enqueue` remains for legacy callers. New system adapters should use `/intake`.

## Routing v4 Core: Burst-Aware Scoring

Routing no longer means "pick the first healthy provider in profile order." Each candidate gets a workload score. Lower score wins.

The score includes:

- live provider availability
- resource guard
- provider concurrency and queue wait
- provider-side external queue depth
- startup seconds
- startup amortization by `batch_size`
- `burst_size`
- estimated GPU runtime
- estimated CPU/local runtime
- deadline
- quality requirement
- provider-specific fit

Recommended caller metadata:

```json
{
  "metadata": {
    "routing": {
      "estimated_input_tokens": 800,
      "estimated_cpu_runtime_seconds": 3600,
      "estimated_gpu_runtime_seconds": 30,
      "batch_size": 500,
      "burst_size": 500,
      "deadline_seconds": 600,
      "latency_class": "batch",
      "quality_requires_gpu": false
    }
  }
}
```

Current core rules:

- `ollama` is preferred for single, light, fixed-cost interactive jobs.
- `ollama` is rejected when `burst_size > ollama_max_burst_size`.
- `modal` is preferred for burst fanout (`burst_size >= modal_preferred_burst_size`).
- `runpod` / `vast` are preferred for long-running batch jobs where cold start is amortized.
- long `llm_heavy` jobs above `ollama_max_input_tokens` cannot use Ollama.

Examples:

- 1 short translation: `ollama`
- 500 independent short LLM tasks: `modal`
- 36K token topic engine: `modal`, then `runpod` / `vast` if Modal unavailable
- multi-hour ASR/video/OCR batch: `runpod` or `vast`

When successful job history exists, routing adds `observed` values to provider signals from `gpu-job stats`. The current observed startup input is `runtime_seconds - remote_runtime_seconds` p50 for the same `provider:job_type:gpu_profile`.

## Provider warm-capacity notes

- RunPod Serverless can keep active workers above zero for a chosen endpoint, which reduces cold-start wait at the cost of ongoing worker spend.
- Vast.ai endpoints and workergroups expose `cold_workers`, `max_workers`, queue time, and inactivity timeout settings. These must be treated as billable-capacity levers and guarded by `gpu-job guard`.

`gpu-job guard` treats RunPod pods and serverless endpoints with `workersMin` or `workersStandby` above zero as blocking billable resources. RunPod network volumes are allowed only when they match `config/execution-policy.json` approved fixed-storage entries. Unknown volumes, approved volume size growth, or monthly estimate above the failure budget block job submission.
