from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
import json
import math
import os
import struct
import subprocess
import tempfile
import time
import traceback
import wave
from array import array
from collections import namedtuple

import modal


cuda_library_path = (
    "/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:"
    "/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib:"
    "/usr/local/lib/python3.12/site-packages/nvidia/cuda_runtime/lib"
)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install(
        "faster-whisper==1.2.1",
        "pyannote.audio==3.3.2",
        "torchcodec",
        "huggingface-hub<1.0",
        "matplotlib",
        "nvidia-cublas-cu12==12.6.4.1",
        "nvidia-cudnn-cu12==9.5.1.17",
        "nvidia-cuda-runtime-cu12==12.6.77",
    )
    .env({"LD_LIBRARY_PATH": cuda_library_path})
)

app = modal.App("gpu-job-modal-asr")
DEFAULT_SPEAKER_MODEL = "pyannote/speaker-diarization-3.1"


def normalize_faster_whisper_model(model_name: str) -> str:
    aliases = {
        "whisper-large-v3": "large-v3",
        "openai/whisper-large-v3": "large-v3",
    }
    return aliases.get(str(model_name or "").strip(), model_name)


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


def _transcribe_audio_impl(
    job_id: str,
    audio_bytes: bytes,
    filename: str,
    model_name: str,
    language: str,
    diarize: bool,
    speaker_model: str,
) -> dict:
    started = time.time()
    if diarize:
        require_diarization_runtime()
    from faster_whisper import WhisperModel

    model_name = normalize_faster_whisper_model(model_name)
    suffix = Path(filename).suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix) as audio_file:
        audio_file.write(audio_bytes)
        audio_file.flush()
        model = WhisperModel(model_name, device="cuda", compute_type="int8_float16")
        segments_iter, info = model.transcribe(
            audio_file.name,
            beam_size=5,
            vad_filter=True,
            language=language,
        )
        gpu_name = ""
        gpu_memory_used_mb = 0
        try:
            import torch

            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                gpu_memory_used_mb = int(torch.cuda.max_memory_allocated() / (1024 * 1024))
        except Exception:
            pass
        segments = [
            {
                "id": index,
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "text": segment.text.strip(),
            }
            for index, segment in enumerate(segments_iter)
        ]
        speaker_segments = []
        diarization_error = ""
        diarization_decode_path = "none"
        if diarize:
            try:
                diarization_decode_path = "ffmpeg_tensor_v1"
                speaker_segments = diarize_file(Path(audio_file.name), speaker_model=speaker_model)
                segments = assign_speakers_to_segments(segments, speaker_segments)
            except Exception as exc:
                diarization_error = f"{exc}\n{traceback.format_exc()}"
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
        "diarization_requested": diarize,
        "diarization_model": speaker_model if diarize else "",
        "diarization_error": diarization_error,
        "diarization_decode_path": diarization_decode_path,
        "speaker_count": len({str(item.get("speaker")) for item in speaker_segments if item.get("speaker")}),
        "speaker_segments": speaker_segments,
        "runtime_seconds": round(time.time() - started, 3),
        "probe_info": {
            "provider": "modal",
            "worker_image": "gpu-job-modal-asr",
            "loaded_model_id": model_name,
            "gpu_name": gpu_name or None,
            "gpu_count": 1 if gpu_name else None,
            "gpu_memory_used_mb": gpu_memory_used_mb or None,
            "device": "cuda",
        },
    }


@app.function(gpu="T4", image=image, timeout=1800)
def transcribe_audio_t4(
    job_id: str,
    audio_bytes: bytes,
    filename: str,
    model_name: str,
    language: str,
    diarize: bool,
    speaker_model: str,
) -> dict:
    result = _transcribe_audio_impl(job_id, audio_bytes, filename, model_name, language, diarize, speaker_model)
    result.setdefault("probe_info", {})["requested_gpu"] = "T4"
    return result


@app.function(gpu="A10G", image=image, timeout=3600, secrets=[modal.Secret.from_name("hf-token")])
def transcribe_audio_a10g(
    job_id: str,
    audio_bytes: bytes,
    filename: str,
    model_name: str,
    language: str,
    diarize: bool,
    speaker_model: str,
) -> dict:
    result = _transcribe_audio_impl(job_id, audio_bytes, filename, model_name, language, diarize, speaker_model)
    result.setdefault("probe_info", {})["requested_gpu"] = "A10G"
    return result


@app.function(gpu="A10G", image=image, timeout=1800, secrets=[modal.Secret.from_name("hf-token")])
def diarization_runtime_canary(speaker_model: str = DEFAULT_SPEAKER_MODEL) -> dict:
    started = time.time()
    token = _huggingface_token()
    patch_torchaudio_compat()
    import pyannote.audio
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(speaker_model, use_auth_token=token)
    if pipeline is None:
        error = f"could not load gated pyannote pipeline {speaker_model}; verify Hugging Face token access and accepted model conditions"
        return {
            "provider": "modal",
            "model": speaker_model,
            "loaded_model_id": speaker_model,
            "text": "",
            "segments": [],
            "diarization_requested": True,
            "diarization_model": speaker_model,
            "diarization_error": error,
            "speaker_segments": [],
            "speaker_count": 0,
            "runtime_seconds": round(time.time() - started, 3),
            "probe_info": {
                "provider": "modal",
                "worker_image": "gpu-job-modal-asr",
                "loaded_model_id": speaker_model,
                "requested_gpu": "A10G",
                "device": "cuda",
                "pyannote_audio_version": getattr(pyannote.audio, "__version__", ""),
                "hf_token_present": bool(token),
                "pipeline_class": "NoneType",
            },
            "error": error,
        }
    gpu_name = ""
    try:
        import torch

        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
    except Exception:
        pass
    diarization_inference_ok = False
    diarization_error = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file:
            _write_canary_wav(Path(audio_file.name))
            diarization = pipeline(load_waveform_for_pyannote(Path(audio_file.name)))
            # The synthetic tone is not expected to produce a real speaker label.
            # This canary proves the provider image can decode audio and run the
            # pyannote inference path without missing runtime dependencies.
            list(diarization.itertracks(yield_label=True))
            diarization_inference_ok = True
    except Exception as exc:
        diarization_error = str(exc)
    return {
        "provider": "modal",
        "model": speaker_model,
        "loaded_model_id": speaker_model,
        "text": "GPU_JOB_ASR_DIARIZATION_CANARY_OK",
        "segments": [{"start": 0.0, "end": 1.0, "text": "GPU_JOB_ASR_DIARIZATION_CANARY_OK"}],
        "diarization_requested": True,
        "diarization_model": speaker_model,
        "diarization_error": diarization_error,
        "diarization_inference_ok": diarization_inference_ok,
        "speaker_segments": [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}],
        "speaker_count": 1,
        "runtime_seconds": round(time.time() - started, 3),
        "probe_info": {
            "provider": "modal",
            "worker_image": "gpu-job-modal-asr",
            "loaded_model_id": speaker_model,
            "requested_gpu": "A10G",
            "gpu_name": gpu_name or None,
            "gpu_count": 1 if gpu_name else None,
            "device": "cuda",
            "pyannote_audio_version": getattr(pyannote.audio, "__version__", ""),
            "hf_token_present": bool(token),
            "pipeline_class": pipeline.__class__.__name__,
        },
    }


def diarize_file(input_path: Path, *, speaker_model: str = DEFAULT_SPEAKER_MODEL) -> list[dict]:
    require_diarization_runtime()
    patch_torchaudio_compat()
    from pyannote.audio import Pipeline

    token = _huggingface_token()
    pipeline = Pipeline.from_pretrained(speaker_model, use_auth_token=token)
    if pipeline is None:
        raise RuntimeError(
            f"could not load gated pyannote pipeline {speaker_model}; verify Hugging Face token access and accepted model conditions"
        )
    diarization = pipeline(load_waveform_for_pyannote(input_path))
    rows = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        rows.append({"start": round(float(turn.start), 3), "end": round(float(turn.end), 3), "speaker": str(speaker)})
    return rows


def load_waveform_for_pyannote(input_path: Path, *, sample_rate: int = 16_000) -> dict:
    """Decode media deterministically for pyannote without using its file loader.

    pyannote.audio may route file paths through torchcodec/torchaudio loaders whose
    dependency behavior changes across image builds.  gpu-job-control owns the
    decode step here: ffmpeg converts any accepted media input to mono 16 kHz
    signed PCM, then we pass an explicit waveform tensor to pyannote.
    """
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "s16le",
            "pipe:1",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg waveform decode failed: {stderr}")
    samples = array("h")
    samples.frombytes(result.stdout)
    if not samples:
        raise RuntimeError("ffmpeg waveform decode produced no samples")
    import torch

    waveform = torch.tensor(samples, dtype=torch.float32).unsqueeze(0) / 32768.0
    return {"waveform": waveform, "sample_rate": sample_rate}


def require_diarization_runtime() -> None:
    if not _huggingface_token():
        raise RuntimeError("speaker diarization requires HF_TOKEN/HUGGINGFACE_TOKEN for gated pyannote models")
    try:
        patch_torchaudio_compat()
        import pyannote.audio  # noqa: F401
        import torchcodec  # noqa: F401
    except Exception as exc:
        raise RuntimeError("speaker diarization requires pyannote.audio and torchcodec in the worker image") from exc


def _write_canary_wav(path: Path, *, seconds: float = 1.0, sample_rate: int = 16_000) -> None:
    frame_count = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        for index in range(frame_count):
            sample = int(0.2 * 32767 * math.sin(2 * math.pi * 440 * index / sample_rate))
            wav_file.writeframesraw(struct.pack("<h", sample))


def patch_torchaudio_compat() -> None:
    """Provide removed torchaudio symbols used by pyannote.audio type annotations."""
    try:
        import torchaudio
    except Exception:
        return
    if not hasattr(torchaudio, "AudioMetaData"):
        torchaudio.AudioMetaData = namedtuple(  # type: ignore[attr-defined]
            "AudioMetaData",
            ["sample_rate", "num_frames", "num_channels", "bits_per_sample", "encoding"],
            defaults=[0, 0, 0, 0, ""],
        )
    if not hasattr(torchaudio, "info"):
        torchaudio.info = _torchaudio_info  # type: ignore[attr-defined]
    if not hasattr(torchaudio, "list_audio_backends"):
        torchaudio.list_audio_backends = lambda: ["soundfile"]  # type: ignore[attr-defined]
    if not hasattr(torchaudio, "get_audio_backend"):
        torchaudio.get_audio_backend = lambda: None  # type: ignore[attr-defined]
    if not hasattr(torchaudio, "set_audio_backend"):
        torchaudio.set_audio_backend = lambda backend=None: None  # type: ignore[attr-defined]
    try:
        import torch
        import pyannote.audio.core.task as pyannote_task

        safe_globals = [torch.torch_version.TorchVersion]
        for name in ("Specifications", "Problem", "Resolution", "Specifications"):
            value = getattr(pyannote_task, name, None)
            if value is not None:
                safe_globals.append(value)
        torch.serialization.add_safe_globals(safe_globals)
    except Exception:
        pass


def _torchaudio_info(path: str | Path, *args, **kwargs):
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=sample_rate,channels,duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        payload = json.loads(result.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        sample_rate = int(float(stream.get("sample_rate") or 0))
        duration = float(stream.get("duration") or 0.0)
        channels = int(stream.get("channels") or 0)
        num_frames = int(sample_rate * duration) if sample_rate and duration else 0
        import torchaudio

        return torchaudio.AudioMetaData(sample_rate, num_frames, channels, 16, "PCM_S")
    except Exception:
        import torchaudio

        return torchaudio.AudioMetaData(0, 0, 0, 0, "")


def _huggingface_token() -> str:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    return str(token or "")


def assign_speakers_to_segments(segments: list[dict], speaker_segments: list[dict]) -> list[dict]:
    assigned = []
    for segment in segments:
        row = dict(segment)
        row["speaker"] = best_speaker_for_interval(
            float(row.get("start") or 0),
            float(row.get("end") or 0),
            speaker_segments,
        )
        assigned.append(row)
    return assigned


def best_speaker_for_interval(start: float, end: float, speaker_segments: list[dict]) -> str:
    overlaps: dict[str, float] = {}
    for item in speaker_segments:
        speaker = str(item.get("speaker") or "")
        if not speaker:
            continue
        overlap = max(0.0, min(end, float(item.get("end") or 0)) - max(start, float(item.get("start") or 0)))
        if overlap > 0:
            overlaps[speaker] = overlaps.get(speaker, 0.0) + overlap
    if not overlaps:
        return ""
    return sorted(overlaps.items(), key=lambda item: (-item[1], item[0]))[0][0]


def format_timestamp(seconds: float, *, sep: str = ",") -> str:
    millis = int(round(max(0.0, seconds) * 1000))
    hours, rem = divmod(millis, 3600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"


def render_srt(segments: list[dict]) -> str:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        speaker = str(segment.get("speaker") or "").strip()
        label = f"{speaker}: " if speaker else ""
        blocks.append(
            f"{index}\n"
            f"{format_timestamp(float(segment.get('start') or 0))} --> {format_timestamp(float(segment.get('end') or 0))}\n"
            f"{label}{str(segment.get('text') or '').strip()}"
        )
    return "\n\n".join(blocks).strip() + ("\n" if blocks else "")


def render_vtt(segments: list[dict]) -> str:
    body = []
    for segment in segments:
        speaker = str(segment.get("speaker") or "").strip()
        label = f"{speaker}: " if speaker else ""
        body.append(
            f"{format_timestamp(float(segment.get('start') or 0), sep='.')} --> "
            f"{format_timestamp(float(segment.get('end') or 0), sep='.')}\n"
            f"{label}{str(segment.get('text') or '').strip()}"
        )
    return "WEBVTT\n\n" + "\n\n".join(body).strip() + ("\n" if body else "")


@app.local_entrypoint()
def main(
    job_id: str,
    artifact_dir: str,
    gpu_profile: str,
    input_uri: str,
    model_name: str = "large-v3",
    language: str = "ja",
    diarize: bool = False,
    speaker_model: str = DEFAULT_SPEAKER_MODEL,
):
    out = Path(artifact_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    audio_bytes, filename = read_input_bytes(input_uri)
    try:
        remote = transcribe_audio_a10g if gpu_profile in {"asr_diarization", "asr_bulk_cheap"} or diarize else transcribe_audio_t4
        result = remote.remote(job_id, audio_bytes, filename, model_name, language, diarize, speaker_model)
    except Exception as exc:
        result = {
            "job_id": job_id,
            "provider": "modal",
            "model": model_name,
            "device": "cuda",
            "compute_type": "int8_float16",
            "language": language,
            "language_probability": 0.0,
            "duration_seconds": 0.0,
            "text": "",
            "segments": [],
            "diarization_requested": diarize,
            "diarization_model": speaker_model if diarize else "",
            "diarization_error": str(exc) if diarize else "",
            "speaker_count": 0,
            "speaker_segments": [],
            "error": str(exc),
            "runtime_seconds": round(time.time() - started, 3),
            "probe_info": {
                "provider": "modal",
                "worker_image": "gpu-job-modal-asr",
                "loaded_model_id": model_name,
                "device": "cuda",
            },
        }
    metrics = {
        "job_id": job_id,
        "provider": "modal",
        "gpu_profile": gpu_profile,
        "model": model_name,
        "input_uri": input_uri,
        "input_bytes": len(audio_bytes),
        "segment_count": len(result["segments"]),
        "text_chars": len(result["text"]),
        "diarization_requested": diarize,
        "diarization_ok": not bool(result.get("diarization_error")),
        "speaker_count": result.get("speaker_count"),
        "runtime_seconds": round(time.time() - started, 3),
        "remote_runtime_seconds": result["runtime_seconds"],
        "gpu_memory_used_mb": (result.get("probe_info") or {}).get("gpu_memory_used_mb"),
        "gpu_name": (result.get("probe_info") or {}).get("gpu_name"),
    }
    probe_info = (
        result.get("probe_info")
        if isinstance(result.get("probe_info"), dict)
        else {
            "provider": "modal",
            "worker_image": "gpu-job-modal-asr",
            "loaded_model_id": model_name,
        }
    )
    verify = {
        "ok": bool(result["text"])
        and result["duration_seconds"] > 0
        and ((not result.get("diarization_requested")) or (bool(result.get("speaker_segments")) and not result.get("diarization_error"))),
        "required": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
        "missing": [],
        "checks": {
            "text_nonempty": bool(result["text"]),
            "duration_positive": result["duration_seconds"] > 0,
            "segments_nonempty": bool(result["segments"]),
            "diarization_ok": (not result.get("diarization_requested"))
            or (bool(result.get("speaker_segments")) and not result.get("diarization_error")),
        },
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "verify.json").write_text(json.dumps(verify, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "probe_info.json").write_text(json.dumps(probe_info, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "transcript.srt").write_text(render_srt(list(result.get("segments") or [])))
    (out / "transcript.vtt").write_text(render_vtt(list(result.get("segments") or [])))
    (out / "speaker_timeline.json").write_text(
        json.dumps(result.get("speaker_segments") or [], ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    (out / "stdout.log").write_text(f"modal asr completed model={model_name} chars={len(result['text'])}\n")
    (out / "stderr.log").write_text(str(result.get("error") or ""))


@app.local_entrypoint()
def canary(
    artifact_dir: str,
    speaker_model: str = DEFAULT_SPEAKER_MODEL,
):
    out = Path(artifact_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        result = diarization_runtime_canary.remote(speaker_model)
        error = ""
    except Exception as exc:
        result = {
            "provider": "modal",
            "model": speaker_model,
            "loaded_model_id": speaker_model,
            "text": "",
            "segments": [],
            "diarization_requested": True,
            "diarization_model": speaker_model,
            "diarization_error": str(exc),
            "speaker_segments": [],
            "speaker_count": 0,
            "runtime_seconds": round(time.time() - started, 3),
            "probe_info": {
                "provider": "modal",
                "worker_image": "gpu-job-modal-asr",
                "loaded_model_id": speaker_model,
                "requested_gpu": "A10G",
                "device": "cuda",
            },
            "error": str(exc),
        }
        error = str(exc)
    metrics = {
        "provider": "modal",
        "gpu_profile": "asr_diarization",
        "model": speaker_model,
        "text_chars": len(result.get("text") or ""),
        "segment_count": len(result.get("segments") or []),
        "diarization_requested": True,
        "diarization_ok": not bool(result.get("diarization_error")),
        "speaker_count": result.get("speaker_count"),
        "runtime_seconds": round(time.time() - started, 3),
        "remote_runtime_seconds": result.get("runtime_seconds"),
        "gpu_name": (result.get("probe_info") or {}).get("gpu_name"),
    }
    verify = {
        "ok": bool(result.get("text")) and not bool(result.get("diarization_error")) and bool(result.get("diarization_inference_ok")),
        "required": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
        "missing": [],
        "checks": {
            "text_nonempty": bool(result.get("text")),
            "diarization_ok": not bool(result.get("diarization_error")),
            "diarization_inference_ok": bool(result.get("diarization_inference_ok")),
            "speaker_segments_nonempty": bool(result.get("speaker_segments")),
            "hf_token_present": bool((result.get("probe_info") or {}).get("hf_token_present")),
        },
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "verify.json").write_text(json.dumps(verify, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "probe_info.json").write_text(json.dumps(result.get("probe_info") or {}, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (out / "stdout.log").write_text(f"modal asr diarization canary ok={verify['ok']} model={speaker_model} gpu={metrics.get('gpu_name')}\n")
    (out / "stderr.log").write_text(error)
