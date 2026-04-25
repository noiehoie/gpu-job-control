from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from typing import Any

import runpod

HEARTBEAT_CONTRACT_ID = "runpod-serverless-heartbeat-python3.12"
HEARTBEAT_CONTRACT_MARKER = f"/opt/gpu-job-control/image-contracts/{HEARTBEAT_CONTRACT_ID}.json"


def _gpu_probe() -> dict[str, Any]:
    command = ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except FileNotFoundError as exc:
        return {"exit_code": 127, "stdout": "", "stderr": str(exc), "command": command}
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": 124,
            "stdout": str(exc.stdout or "").strip(),
            "stderr": str(exc.stderr or "").strip() or "nvidia-smi timed out",
            "command": command,
        }
    return {"exit_code": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip(), "command": command}


def _stable_response(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("prompt") or "")
    model = str(payload.get("model") or "deterministic-canary")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    marker_present = os.path.isfile(HEARTBEAT_CONTRACT_MARKER)
    gpu_probe = _gpu_probe()
    return {
        "text": payload.get("expected_text") or "gpu-job runpod worker ok",
        "model": model,
        "provider": "runpod",
        "worker": "gpu-job-runpod-llm",
        "worker_startup_ok": True,
        "workspace_contract_ok": marker_present,
        "image_contract_marker_present": marker_present,
        "image_contract_id": HEARTBEAT_CONTRACT_ID,
        "handler_contract_id": HEARTBEAT_CONTRACT_ID,
        "provider_image": os.environ.get("GPU_JOB_PROVIDER_IMAGE", ""),
        "worker_image": os.environ.get("GPU_JOB_PROVIDER_IMAGE", ""),
        "cache_hit": True,
        "hf_token_present": bool(os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")),
        "gpu_probe": gpu_probe,
        "actual_cost_guard": {"ok": True, "source": "runpod_serverless_heartbeat_probe_no_allocation_meter"},
        "cleanup": {"ok": True, "source": "runpod_serverless_heartbeat_probe_no_local_resource"},
        "prompt_chars": len(prompt),
        "prompt_sha256_16": digest,
        "created_at_unix": int(time.time()),
    }


def handler(event: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    payload = event.get("input") if isinstance(event, dict) else {}
    if not isinstance(payload, dict):
        payload = {"raw_input": payload}
    result = _stable_response(payload)
    result["runtime_seconds"] = round(time.time() - started, 6)
    return result


def main() -> None:
    runpod.serverless.start({"handler": handler})


if __name__ == "__main__":
    if os.environ.get("GPU_JOB_RUNPOD_LOCAL_TEST") == "1":
        print(json.dumps(handler({"input": {"prompt": "local canary"}}), ensure_ascii=False, sort_keys=True))
    else:
        main()
