from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
import json
import tempfile
import time

import modal


cuda_library_path = (
    "/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:"
    "/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib:"
    "/usr/local/lib/python3.12/site-packages/nvidia/cuda_runtime/lib"
)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "faster-whisper==1.2.1",
        "nvidia-cublas-cu12==12.6.4.1",
        "nvidia-cudnn-cu12==9.5.1.17",
        "nvidia-cuda-runtime-cu12==12.6.77",
    )
    .env({"LD_LIBRARY_PATH": cuda_library_path})
)

app = modal.App("gpu-job-modal-asr")


def read_input_bytes(input_uri: str) -> tuple[bytes, str]:
    parsed = urlparse(input_uri)
    if parsed.scheme == "file":
        path = Path(parsed.path)
    elif parsed.scheme == "local":
        path = Path(parsed.netloc + parsed.path)
    elif not parsed.scheme:
        path = Path(input_uri)
    else:
        raise ValueError(f"unsupported Modal ASR input_uri: {input_uri}")
    if not path.is_file():
        raise FileNotFoundError(f"ASR input file not found: {path}")
    return path.read_bytes(), path.name


@app.function(gpu="T4", image=image, timeout=900)
def transcribe_audio(job_id: str, audio_bytes: bytes, filename: str, model_name: str) -> dict:
    started = time.time()
    from faster_whisper import WhisperModel

    suffix = Path(filename).suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix) as audio_file:
        audio_file.write(audio_bytes)
        audio_file.flush()
        model = WhisperModel(model_name, device="cuda", compute_type="int8_float16")
        segments_iter, info = model.transcribe(
            audio_file.name,
            beam_size=5,
            vad_filter=True,
            language="ja",
        )
        segments = [
            {
                "id": index,
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "text": segment.text.strip(),
            }
            for index, segment in enumerate(segments_iter)
        ]
    text = "".join(segment["text"] for segment in segments).strip()
    return {
        "job_id": job_id,
        "provider": "modal",
        "model": model_name,
        "device": "cuda",
        "compute_type": "int8_float16",
        "language": getattr(info, "language", ""),
        "language_probability": round(float(getattr(info, "language_probability", 0.0)), 6),
        "duration_seconds": round(float(getattr(info, "duration", 0.0)), 3),
        "text": text,
        "segments": segments,
        "runtime_seconds": round(time.time() - started, 3),
    }


@app.local_entrypoint()
def main(job_id: str, artifact_dir: str, gpu_profile: str, input_uri: str, model_name: str = "large-v3"):
    out = Path(artifact_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    audio_bytes, filename = read_input_bytes(input_uri)
    result = transcribe_audio.remote(job_id, audio_bytes, filename, model_name)
    metrics = {
        "job_id": job_id,
        "provider": "modal",
        "gpu_profile": gpu_profile,
        "model": model_name,
        "input_uri": input_uri,
        "input_bytes": len(audio_bytes),
        "segment_count": len(result["segments"]),
        "text_chars": len(result["text"]),
        "runtime_seconds": round(time.time() - started, 3),
        "remote_runtime_seconds": result["runtime_seconds"],
    }
    verify = {
        "ok": bool(result["text"]) and result["duration_seconds"] > 0,
        "required": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
        "missing": [],
        "checks": {
            "text_nonempty": bool(result["text"]),
            "duration_positive": result["duration_seconds"] > 0,
            "segments_nonempty": bool(result["segments"]),
        },
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "verify.json").write_text(json.dumps(verify, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
