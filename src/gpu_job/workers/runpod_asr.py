from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import tempfile
import time

from gpu_job.workers.asr import DEFAULT_SPEAKER_MODEL, probe_runtime, run_asr

HANDLER_CONTRACT_ID = "asr-diarization-runpod-serverless-large-v3-pyannote3.3.2-cuda12.4"
HANDLER_CONTRACT_MARKER = f"/opt/gpu-job-control/image-contracts/{HANDLER_CONTRACT_ID}.json"
WORKER_NAME = "gpu-job-runpod-asr-serverless"


def _bool_input(payload: dict[str, Any], key: str, default: bool = False) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _probe_payload(payload: dict[str, Any], started: float) -> dict[str, Any]:
    diarize = _bool_input(payload, "diarize", True)
    require_gpu = _bool_input(payload, "require_gpu", False)
    runtime = probe_runtime(diarize=diarize, require_gpu=require_gpu)
    checks = runtime.get("checks") if isinstance(runtime.get("checks"), dict) else {}
    handler_marker_present = Path(HANDLER_CONTRACT_MARKER).is_file()
    runtime_ok = bool(runtime.get("ok"))
    workspace_ok = runtime_ok and handler_marker_present
    return {
        **runtime,
        "provider": "runpod",
        "worker": WORKER_NAME,
        "worker_image": os.environ.get("GPU_JOB_PROVIDER_IMAGE", ""),
        "provider_image": os.environ.get("GPU_JOB_PROVIDER_IMAGE", ""),
        "handler_contract_id": HANDLER_CONTRACT_ID,
        "image_contract_id": HANDLER_CONTRACT_ID,
        "worker_image_contract_id": "asr-diarization-large-v3-pyannote3.3.2-cuda12.4",
        "workspace_contract_ok": workspace_ok,
        "asr_diarization_runtime_ok": runtime_ok,
        "hf_token_present": bool(
            os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        ),
        "image_contract_marker_present": bool(checks.get("image_contract_marker_present")),
        "serverless_handler_marker": HANDLER_CONTRACT_MARKER,
        "serverless_handler_marker_present": handler_marker_present,
        "cache_hit": bool(runtime.get("cache_hit")),
        "worker_startup_ok": True,
        "actual_cost_guard": {"ok": True, "source": "runpod_serverless_handler_probe_no_allocation_meter"},
        "cleanup": {"ok": True, "source": "runpod_serverless_handler_probe_no_local_resource"},
        "runtime_seconds": round(time.time() - started, 6),
    }


def handler(event: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    payload = event.get("input") if isinstance(event, dict) else {}
    if not isinstance(payload, dict):
        payload = {"raw_input": payload}
    if _bool_input(payload, "probe_runtime", False):
        return _probe_payload(payload, started)

    input_uri = str(payload.get("input_uri") or "")
    if not input_uri:
        return {
            "ok": False,
            "provider": "runpod",
            "worker": WORKER_NAME,
            "error": "input_uri is required unless probe_runtime=true",
            "runtime_seconds": round(time.time() - started, 6),
        }
    with tempfile.TemporaryDirectory(prefix="gpu-job-runpod-asr-") as tmp:
        artifact_dir = Path(tmp) / "artifacts"
        args = type(
            "Args",
            (),
            {
                "job_id": str(payload.get("job_id") or "runpod-serverless-asr"),
                "artifact_dir": str(artifact_dir),
                "gpu_profile": str(payload.get("gpu_profile") or "asr_diarization"),
                "input_uri": input_uri,
                "provider": "runpod",
                "model_name": str(payload.get("model") or payload.get("model_name") or "large-v3"),
                "language": str(payload.get("language") or "ja"),
                "compute_type": str(payload.get("compute_type") or "int8_float16"),
                "diarize": _bool_input(payload, "diarize", True),
                "speaker_model": str(payload.get("speaker_model") or DEFAULT_SPEAKER_MODEL),
            },
        )()
        exit_code = run_asr(args)
        result = _read_json(artifact_dir / "result.json")
        metrics = _read_json(artifact_dir / "metrics.json")
        verify = _read_json(artifact_dir / "verify.json")
        probe_info = _read_json(artifact_dir / "probe_info.json")
        return {
            "ok": exit_code == 0 and bool(verify.get("ok")),
            "provider": "runpod",
            "worker": WORKER_NAME,
            "handler_contract_id": HANDLER_CONTRACT_ID,
            "image_contract_id": HANDLER_CONTRACT_ID,
            "worker_image_contract_id": "asr-diarization-large-v3-pyannote3.3.2-cuda12.4",
            "result": result,
            "metrics": metrics,
            "verify": verify,
            "probe_info": probe_info,
            "runtime_seconds": round(time.time() - started, 6),
        }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> None:
    try:
        import runpod
    except Exception as exc:
        raise SystemExit(f"runpod package is required for {WORKER_NAME}: {exc}") from exc
    runpod.serverless.start({"handler": handler})


if __name__ == "__main__":
    if os.environ.get("GPU_JOB_RUNPOD_LOCAL_TEST") == "1":
        print(json.dumps(handler({"input": {"probe_runtime": True, "diarize": True}}), ensure_ascii=False, sort_keys=True))
    else:
        main()
