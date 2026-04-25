from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlretrieve
import base64
import json
import os
import subprocess
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
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "command": command,
    }


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
        "gpu_probe": _gpu_probe(),
        "runtime_seconds": round(time.time() - started, 6),
    }


def _audio_suffix(value: str) -> str:
    suffix = Path(urlparse(value).path).suffix.lower()
    return suffix if suffix in {".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".webm"} else ".wav"


def _materialize_audio_input(payload: dict[str, Any], tmp: Path, job_id: str) -> tuple[str, str]:
    input_uri = str(payload.get("input_uri") or "").strip()
    audio = str(payload.get("audio") or "").strip()
    audio_base64 = str(payload.get("audio_base64") or "").strip()
    provided = [name for name, value in (("input_uri", input_uri), ("audio", audio), ("audio_base64", audio_base64)) if value]
    if len(provided) > 1:
        raise ValueError(f"provide only one audio input field: {', '.join(provided)}")
    if input_uri:
        return input_uri, "input_uri"
    input_dir = tmp / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    if audio_base64:
        path = input_dir / f"{job_id}.wav"
        path.write_bytes(base64.b64decode(audio_base64))
        return str(path), "audio_base64"
    if audio:
        parsed = urlparse(audio)
        if parsed.scheme in {"http", "https"}:
            path = input_dir / f"{job_id}{_audio_suffix(audio)}"
            urlretrieve(audio, path)
            return str(path), "audio"
        return audio, "audio"
    raise ValueError("input_uri, audio, or audio_base64 is required unless probe_runtime=true")


def handler(event: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    payload = event.get("input") if isinstance(event, dict) else {}
    if not isinstance(payload, dict):
        payload = {"raw_input": payload}
    if _bool_input(payload, "probe_runtime", False):
        return _probe_payload(payload, started)

    with tempfile.TemporaryDirectory(prefix="gpu-job-runpod-asr-") as tmp:
        tmp_path = Path(tmp)
        job_id = str(payload.get("job_id") or event.get("id") or "runpod-serverless-asr")
        try:
            input_uri, input_source = _materialize_audio_input(payload, tmp_path, job_id)
        except Exception as exc:
            return {
                "ok": False,
                "provider": "runpod",
                "worker": WORKER_NAME,
                "error": str(exc),
                "runtime_seconds": round(time.time() - started, 6),
            }
        artifact_dir = Path(tmp) / "artifacts"
        args = type(
            "Args",
            (),
            {
                "job_id": job_id,
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
            "input_source": input_source,
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
    if os.environ.get("GPU_JOB_RUNPOD_LOCAL_TEST") == "1":
        print(json.dumps(handler({"input": {"probe_runtime": True, "diarize": True}}), ensure_ascii=False, sort_keys=True))
        return
    try:
        import runpod
    except Exception as exc:
        raise SystemExit(f"runpod package is required for {WORKER_NAME}: {exc}") from exc
    runpod.serverless.start({"handler": handler})


if __name__ == "__main__":
    main()
