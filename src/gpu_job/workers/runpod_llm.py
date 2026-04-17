from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

import runpod


def _stable_response(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("prompt") or "")
    model = str(payload.get("model") or "deterministic-canary")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    return {
        "text": payload.get("expected_text") or "gpu-job runpod worker ok",
        "model": model,
        "provider": "runpod",
        "worker": "gpu-job-runpod-llm",
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
