# Worker contract

All GPU workers must write the same artifact set:

- `result.json`
- `metrics.json`
- `verify.json`
- `stdout.log`
- `stderr.log`

`verify.json.ok` must be true only when deterministic checks pass. ASR checks require non-empty text, positive duration, and at least one segment.

## ASR worker

Entrypoint:

```bash
gpu-job-asr-worker \
  --job-id "$GPU_JOB_ID" \
  --artifact-dir "$GPU_JOB_ARTIFACT_DIR" \
  --gpu-profile "$GPU_JOB_PROFILE" \
  --input-uri "$GPU_JOB_INPUT_URI" \
  --provider "$GPU_JOB_PROVIDER" \
  --model-name large-v3 \
  --language ja \
  --compute-type int8_float16
```

The current container worker supports `file://`, `local://`, and plain local paths. Object storage inputs must be staged before invoking the worker.

## Container image

Reference Dockerfile:

```text
docker/asr-worker.Dockerfile
```

The image must include CUDA runtime, cuDNN, ffmpeg, `gpu-job-control`, and `faster-whisper==1.2.1`.

Warm capacity settings are outside the worker contract. RunPod `workers_min`/`workers_standby` and Vast `cold_workers` must stay at zero unless paid warm capacity is explicitly selected.

RunPod network volume storage is also outside the worker contract. Workers may rely on volumes that `config/execution-policy.json` marks as approved fixed storage, but workers must not create, resize, or delete volumes.
