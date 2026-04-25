# Provider Wheel-Reinvention Research - 2026-04-22

## Conclusion

We are not uniformly reinventing the wheel. The audit/control plane is justified
custom code, but parts of provider execution should be moved closer to official
worker, SDK, and CLI surfaces before launch.

Keep custom:

- cross-provider launch gates
- cost guards and post-guard residue checks
- provider-module canary evidence schema
- artifact verification and execution record requirements
- `routing_by_module_enabled=false` launch policy

Do not keep reinventing:

- RunPod LLM worker implementation when `runpod-workers/worker-vllm` already
  provides OpenAI-compatible vLLM serverless behavior
- RunPod model cache handling when official model caching and
  `/runpod-volume/huggingface-cache/hub` conventions are available
- Vast PyWorker protocol and autoscaler interaction when `vast-ai/pyworker`,
  `vastai.Serverless`, endpoint/workergroup APIs, and Deployment abstractions
  exist
- Vast direct instance SSH/SCP orchestration where `vastai execute` and
  `vastai copy` can cover parts of the transport layer

## Confirmed Local Baseline

Current branch state still has provider-adapter diffs. This remains a Phase 0
blocker and must not be hidden.

```text
git status --short --branch
## main...origin/main
 M config/image-contracts.json
 M docker/runpod-asr-worker.Dockerfile
 M src/gpu_job/launch_gate.py
 M src/gpu_job/provider_contract_probe.py
 M src/gpu_job/provider_module_contracts.py
 M src/gpu_job/providers/runpod.py
 M src/gpu_job/providers/vast.py
 M src/gpu_job/workers/runpod_asr.py
 M tests/test_image_distribution.py
 M tests/test_launch_gate.py
 M tests/test_provider_contract_probe.py
 M tests/test_runpod_config.py
 M tests/test_runpod_serverless_asr.py
 M tests/test_vast_asr_provider.py
```

`routing_by_module_enabled` remains false in `config/execution-policy.json`.

## Sources Read

RunPod:

- https://docs.runpod.io/serverless/workers/handler-functions
- https://docs.runpod.io/serverless/overview
- https://docs.runpod.io/serverless/endpoints/overview
- https://github.com/runpod-workers/worker-template
- https://github.com/runpod-workers/worker-vllm
- https://github.com/runpod-workers/worker-faster_whisper
- https://github.com/runpod-workers/mock-worker
- https://github.com/runpod-workers/model-store-cache-example

Vast:

- https://docs.vast.ai/documentation/serverless/architecture
- https://docs.vast.ai/documentation/serverless/getting-started-with-serverless
- https://github.com/vast-ai/pyworker
- https://github.com/vast-ai/vast-cli
- https://github.com/vast-ai/vast-sdk
- https://github.com/vast-ai/base-image
- https://github.com/vast-ai/docs

Local clones were placed under `/tmp/gpu-provider-research/`.

## RunPod Findings

### Official Contract

RunPod queue-based serverless handlers receive a job object with top-level `id`
and `input`, then start through:

```python
runpod.serverless.start({"handler": handler})
```

Official docs also describe streaming handlers, async handlers,
`return_aggregate_stream`, progress updates, worker refresh, and best practice
of initializing heavy models outside the handler.

### Existing Worker Repos

`runpod-workers/worker-faster_whisper` already implements the ASR-side handler
shape we should copy:

- global `MODEL = predict.Predictor(); MODEL.setup()`
- schema validation via `runpod.serverless.utils.rp_validator.validate`
- `audio` URL and `audio_base64` inputs
- `download_files_from_urls(job["id"], ...)`
- `rp_cleanup.clean(["input_objects"])`
- `runpod.serverless.start({"handler": run_whisper_job})`

Our current `src/gpu_job/workers/runpod_asr.py` is compatible with the basic
RunPod handler contract, but it does not yet match the official faster-whisper
input surface. It requires `input_uri` unless `probe_runtime=true`.

`runpod-workers/worker-vllm` should be the default reference for RunPod LLM:

- OpenAI-compatible `/openai/v1` behavior
- async generator handler
- CUDA error exits to force worker replacement
- `concurrency_modifier`
- `return_aggregate_stream=True`
- `MODEL_NAME`, `BASE_PATH`, `HF_HOME`, and vLLM env auto-discovery

`model-store-cache-example` confirms the cache convention:

- cached models are under `/runpod-volume/huggingface-cache/hub`
- model is loaded once at startup in offline mode
- endpoint Model setting should match `MODEL_NAME`

### Keep Custom

RunPod official tooling does not provide our launch audit:

- no cross-provider `launch_gate`
- no artifact schema enforcement
- no provider-module identity check
- no guard that rejects hidden warm capacity by policy
- no execution record contract for our launch review

### High-Risk Reinvention

- Implementing a custom LLM worker instead of adopting `worker-vllm`.
- Implementing our own model cache convention instead of RunPod model caching.
- Maintaining large raw GraphQL/REST mutations where official REST/SDK/CLI can
  safely cover the operation.
- Treating Hub/Console vLLM behavior as reproducible through raw API calls
  without evidence. Existing docs already mark this path deferred.

## Vast Findings

### Official Contract

Vast Serverless is not RunPod handler-compatible. Its structure is:

- Endpoint
- Workergroup
- Worker instance
- PyWorker process
- Serverless SDK client

Vast docs state that an Endpoint owns scaling parameters such as `max_workers`,
`min_load`, `min_workers`, `cold_mult`, `min_cold_load`, `target_util`,
`inactivity_timeout`, `max_queue_time`, and `target_queue_time`.

A Workergroup defines the template/code, marketplace search filters, hardware
requirements such as `gpu_ram`, and worker creation.

Workers run a PyWorker that monitors model readiness, proxies requests, and
reports autoscaling metrics.

### Existing SDK / Worker Repos

`vast-ai/pyworker` documents the worker-side standard:

- template startup clones `PYWORKER_REPO`
- installs `requirements.txt`
- starts the model server
- runs `python worker.py`
- `worker.py` builds `WorkerConfig`, `HandlerConfig`, `BenchmarkConfig`, and
  `LogActionConfig`
- exactly one benchmark handler is recommended for capacity estimation

`vast-ai/pyworker/workers/openai/worker.py` already gives an OpenAI-compatible
vLLM worker:

- routes `/v1/completions` and `/v1/chat/completions`
- uses `MODEL_NAME`
- allows parallel requests
- calculates workload from `max_tokens`
- uses log patterns to detect load and errors

`vast-ai/vast-cli` and `vast-ai/vast-sdk` already expose serverless lifecycle:

- create endpoint
- create workergroup
- delete workergroup
- delete endpoint
- get endpoint workers
- `Serverless().get_endpoint(...)`
- endpoint request helpers
- Deployment abstractions that can package code and manage endpoint/workergroup

Local CLI facts:

```text
vastai --version
1.0.1

vastai create endpoint --help
--min_load
--min_cold_load
--target_util
--cold_mult
--cold_workers
--max_workers
--endpoint_name
--max_queue_time
--target_queue_time
--inactivity_timeout

vastai create workergroup --help
--template_hash
--template_id
--launch_args
--endpoint_name
--endpoint_id
--test_workers
--gpu_ram
--search_params
--cold_workers
```

`vastai delete workergroup --help` explicitly says deleting a workergroup does
not automatically destroy all associated instances. Our post-guard/orphan
inventory is therefore still required.

### Keep Custom

Vast official tooling does not provide our launch audit:

- direct check that serverless evidence includes both `endpoint_id` and
  `workergroup_id`
- refusal to let direct instance evidence satisfy `vast_pyworker_serverless`
- post-guard that checks residual instances
- local JobStore correlation for ghost/zombie resource detection
- launch policy that keeps Vast reserve/canary only

### High-Risk Reinvention

- Reimplementing PyWorker serverless with direct instances.
- Replacing endpoint/workergroup lifecycle with inferred CLI/API fragments.
- Continuing to grow manual SSH/SCP orchestration for direct instance ASR
  instead of narrowing it or delegating transport to `vastai execute` /
  `vastai copy`.
- Creating custom serverless request routing instead of using `vastai.Serverless`
  where available.

## Council Result

Composer2:

- Keep launch gate, contract probe schema, cost guard, and module evidence.
- Treat RunPod handler usage as correct SDK usage.
- Treat RunPod Hub/vLLM raw API recreation as high-risk.
- Treat Vast pyworker serverless direct-instance fallback as invalid.

Gemini:

- Keep cost guard and canary verification.
- Keep Vast orphan/reaper audit.
- Reduce custom RunPod GraphQL/REST where SDK methods can cover it.
- Migrate Vast orchestration toward official SDK/serverless features.

Kimi K2.5:

- Highest risk is Vast direct instance SSH/SCP/manual remote script.
- RunPod handler is low/moderate risk because it uses official `runpod`.
- Image contract registry may be simplified later but is useful now as audit
  metadata.

Grok:

- No completed response was available before this memo was written.

## Launch-Safe Direction

1. Freeze current audit schema and launch gates as custom control-plane code.
2. For RunPod LLM, adopt official `worker-vllm` image/contract instead of
   writing our own vLLM worker.
3. For RunPod ASR, align input compatibility with `worker-faster_whisper` while
   preserving our artifact/evidence wrapper.
4. For Vast serverless, stop treating direct instance as a path to serverless.
   Build only on Endpoint + Workergroup + PyWorker + SDK evidence.
5. For Vast direct instance reserve, cap scope to conservative canary and
   replace custom SSH/SCP pieces with official CLI/SDK primitives where safe.
6. Do not enable `routing_by_module_enabled`.
7. Do not promote provider adapters until Phase 0 blocker is resolved by an
   explicit decision: commit the adapter changes as the provider slice, or back
   them out before launch freeze.

## Next Concrete Research Tasks

No Docker on Mac Studio.

1. Map every custom RunPod GraphQL/REST call to an official REST/SDK/CLI
   equivalent, marking keep/replace/defer.
2. Map every Vast direct instance SSH/SCP helper to `vastai execute`,
   `vastai copy`, or a documented SDK call.
3. Prototype a non-allocating Vast SDK import/shape probe with `uv run python`
   only.
4. Draft a RunPod ASR compatibility patch that accepts
   `audio`/`audio_base64`/`input_uri`, but do not allocate GPU resources.
