# RunPod Serverless Phase 0 Re-Read

Date: 2026-04-23 JST

## Local Facts

- Latest minimal RunPod Serverless heartbeat canary did not reach the handler.
- Observed shape: `status=IN_QUEUE`, `jobs.inQueue=1`, `workers.throttled=1`, `workers.ready=0`, `workers.running=0`, `workers.initializing=0`.
- Cleanup deleted the created endpoint and template; post-guard showed no billable RunPod compute residue.
- RunPod Pod HTTP and ASR diarization canaries succeeded on the same account/API key.
- Current heartbeat default GPU pool is `AMPERE_16,AMPERE_24,ADA_24`.
- Current ASR Serverless default GPU pool is `ADA_24`.
- Both Serverless paths create with `workersMin=0` and `workersMax=1`.
- `config/execution-policy.json` still has `provider_module_routing.routing_by_module_enabled=false`.

## Official Sources Re-Read

- RunPod worker states define `Throttled` as temporarily unable to run due to host resource constraints and not billed:
  https://docs.runpod.io/serverless/workers/overview
- RunPod quickstart expects the first request to take minutes while workers initialize and uses `/run` or `/runsync` request flow:
  https://docs.runpod.io/serverless/quickstart
- Endpoint docs show REST creation with `workersMin=0`, positive `workersMax`, and multiple GPU types in priority order:
  https://docs.runpod.io/serverless/endpoints/overview
- `runpodctl serverless` docs currently show `--hub-id`, but the locally installed `runpodctl 2.1.9-673143d` previously did not expose `hub` or `--hub-id`:
  https://docs.runpod.io/runpodctl/reference/runpodctl-serverless
- vLLM docs say the worker can be deployed from Hub or customized from `runpod-workers/worker-vllm`; cached models are the recommended deployment option:
  https://docs.runpod.io/serverless/vllm/overview
- Re-check on 2026-04-24 using the latest downloadable Linux release still
  returned `runpodctl 2.1.9-673143d` and the same missing surface:

```text
runpodctl 2.1.9-673143d
{"error":"unknown command \"hub\" for \"runpodctl\""}
runpodctl serverless create --help
  --template-id string (required)
```

## Worker Repos Re-Read

Cloned to `/tmp/gpu-job-runpod-research` for Phase 0 inspection:

- `runpod-workers/worker-basic`
  - Minimal `python:3.10-slim` image.
  - Entrypoint is `python3 -u rp_handler.py`.
  - Handler uses `runpod.serverless.start({"handler": handler})`.
- `runpod-workers/worker-template`
  - Uses `runpod/base:0.6.3-cuda11.8.0`.
  - Installs dependencies with `uv pip install --system`.
  - Handler is the standard `runpod.serverless.start` shape.
- `runpod-workers/worker-vllm`
  - Recommended image family is `runpod/worker-v1-vllm:<version>`.
  - Requires CUDA >= 12.1.
  - Configures vLLM primarily through env vars such as `MODEL_NAME`, `MAX_MODEL_LEN`, `GPU_MEMORY_UTILIZATION`, `MAX_CONCURRENCY`, and `OPENAI_SERVED_MODEL_NAME_OVERRIDE`.
- `runpod-workers/worker-faster_whisper`
  - Uses `nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04`.
  - Loads `WhisperModel` on `cuda` when `rp_cuda.is_available()`.
  - Example output includes `"device": "cuda"`.

## Community Re-Read

- Reddit reports match the observed pattern: Serverless workers can stay throttled or initializing because of GPU availability.
- Reports also say model caching or network volumes do not solve GPU memory/model-load cold start; keeping warm workers or using Pods may be required for stable low latency.
- A recent vLLM Serverless issue thread had RunPod staff asking for full logs and worker IDs, which implies provider-side internal logs are often needed for this class of failure.

Community references:

- https://www.reddit.com/r/RunPod/comments/1r94cbo/extremely_long_initialization_process/
- https://www.reddit.com/r/RunPod/comments/1s2e9qg/runpod_gpu_supply_problem/
- https://www.reddit.com/r/RunPod/comments/1s2uw3z/cold_start_issues/
- https://www.reddit.com/r/RunPod/comments/1slfmph/runpod_serverless/

## Council Results

Real CLI engines used:

- Gemini CLI `0.38.2`
- Composer2 via `agent --print --mode ask --model composer-2`
- Kimi CLI was invoked, but returned `LLM not set`.
- Grok CLI was not found in `PATH`.

Consensus:

- The most likely current blocker is not the heartbeat handler code itself.
- The observed evidence fits RunPod Serverless placement/capacity throttling for the requested GPU pool or template constraints.
- The `404 job not found` during cancel/status is likely secondary after timeout/eviction/delete race, not the primary fault.
- The installed `runpodctl` and docs mismatch remains a separate reproduction-surface risk, especially for Hub deployment parity.
- The latest downloadable `runpodctl` still does not expose Hub deploy on the
  public CLI surface, so Hub-template reproduction remains blocked on a
  provider-side/public-surface mismatch rather than local operator error.

## Phase 0 Conclusion

RunPod Serverless should remain unpromoted. The next work should not change `routing_by_module_enabled` and should not depend on Mac Studio Docker.

The next useful evidence is a non-production, low-risk Serverless matrix that separates:

1. provider capacity/placement failure;
2. image startup or registry-pull failure;
3. GPU pool constraint failure;
4. GraphQL-created endpoint vs Console/Hub-created endpoint drift.

## Next Low-Risk Experiments

1. Snapshot every Serverless endpoint/template created by the canary: endpoint ID, template ID, image digest, `gpuIds`, `locations`, `workersMin`, `workersMax`, `workersStandby`, `flashBootType`, provider job ID, UTC start/end, and final health.
2. Add a plan-only matrix for heartbeat GPU pools before more live runs: current `AMPERE_16,AMPERE_24,ADA_24`; wider official-style fallback; one constrained single-pool run only when needed.
3. Prefer official worker shapes for future live probes:
   - minimal lifecycle: `worker-basic` / `worker-template` shape;
   - ASR: `worker-faster_whisper` contract shape;
   - LLM: `worker-vllm` / Hub-derived contract shape.
4. Ask RunPod Support with the existing evidence bundle if throttling persists: include endpoint IDs, job IDs, worker IDs if present, and UTC windows.
5. Keep RunPod Pod route as the working bounded fallback while Serverless evidence remains throttled.
