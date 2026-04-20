from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time


REQUIRED_ARTIFACTS = ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"]
DEFAULT_SPEAKER_MODEL = "pyannote/speaker-diarization-3.1"
ASR_DIARIZATION_IMAGE_CONTRACT_ID = "asr-diarization-large-v3-pyannote3.3.2-cuda12.4"
ASR_DIARIZATION_IMAGE_CONTRACT_MARKER = f"/opt/gpu-job-control/image-contracts/{ASR_DIARIZATION_IMAGE_CONTRACT_ID}.json"
ASR_DIARIZATION_WORKER_IMAGE = "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4"


def normalize_faster_whisper_model(model_name: str) -> str:
    aliases = {
        "whisper-large-v3": "large-v3",
        "openai/whisper-large-v3": "large-v3",
    }
    return aliases.get(str(model_name or "").strip(), model_name)


def resolve_local_input(input_uri: str) -> Path:
    parsed = urlparse(input_uri)
    if parsed.scheme == "file":
        path = Path(parsed.path)
    elif parsed.scheme == "local":
        path = Path(parsed.netloc + parsed.path)
    elif parsed.scheme == "":
        path = Path(input_uri)
    else:
        raise ValueError(f"unsupported local ASR input_uri: {input_uri}")
    if not path.is_file():
        raise FileNotFoundError(f"ASR input file not found: {path}")
    return path


def transcribe_file(
    input_path: Path,
    job_id: str,
    provider: str,
    model_name: str,
    language: str,
    compute_type: str,
    *,
    diarize: bool = False,
    speaker_model: str = DEFAULT_SPEAKER_MODEL,
) -> dict:
    started = time.time()
    if diarize:
        require_diarization_runtime()
    from faster_whisper import WhisperModel

    model_name = normalize_faster_whisper_model(model_name)
    model = WhisperModel(model_name, device="cuda", compute_type=compute_type)
    segments_iter, info = model.transcribe(
        str(input_path),
        beam_size=5,
        vad_filter=True,
        language=language,
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
    speaker_segments: list[dict] = []
    diarization_error = ""
    if diarize:
        try:
            with tempfile.TemporaryDirectory(prefix="gpu-job-diarize-") as tmp:
                diarization_input = prepare_diarization_audio(input_path, Path(tmp) / "diarization.wav")
                speaker_segments = diarize_file(diarization_input, speaker_model=speaker_model)
                segments = assign_speakers_to_segments(segments, speaker_segments)
        except Exception as exc:
            diarization_error = str(exc)
    text = "".join(segment["text"] for segment in segments).strip()
    return {
        "job_id": job_id,
        "provider": provider,
        "model": model_name,
        "device": "cuda",
        "compute_type": compute_type,
        "language": getattr(info, "language", ""),
        "language_probability": round(float(getattr(info, "language_probability", 0.0)), 6),
        "duration_seconds": round(float(getattr(info, "duration", 0.0)), 3),
        "text": text,
        "segments": segments,
        "diarization_requested": diarize,
        "diarization_model": speaker_model if diarize else "",
        "diarization_error": diarization_error,
        "speaker_count": len({str(item.get("speaker")) for item in speaker_segments if item.get("speaker")}),
        "speaker_segments": speaker_segments,
        "runtime_seconds": round(time.time() - started, 3),
    }


def prepare_diarization_audio(input_path: Path, output_path: Path) -> Path:
    """Convert arbitrary media to a mono WAV file accepted by pyannote."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        str(output_path),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"ffmpeg failed to prepare diarization audio: {message}")
    if not output_path.is_file() or output_path.stat().st_size <= 44:
        raise RuntimeError("ffmpeg produced empty diarization audio")
    return output_path


def collect_gpu_probe() -> tuple[list[dict], dict]:
    logs: list[dict] = []
    metrics: dict = {}
    commands = [
        "nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader",
        "nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits",
    ]
    for command in commands:
        proc = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        row = {
            "cmd": command,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
        }
        logs.append(row)
        if proc.returncode == 0 and "memory.used" in command:
            fields = [item.strip() for item in proc.stdout.strip().split(",")]
            if len(fields) > 1 and fields[1]:
                metrics["gpu_memory_used_mb"] = int(float(fields[1]))
            if len(fields) > 3 and fields[3]:
                metrics["gpu_utilization_percent"] = int(float(fields[3]))
    return logs, metrics


def diarize_file(input_path: Path, *, speaker_model: str = DEFAULT_SPEAKER_MODEL) -> list[dict]:
    require_diarization_runtime()
    from pyannote.audio import Pipeline

    token = _huggingface_token()
    pipeline = _pyannote_pipeline_from_pretrained(Pipeline, speaker_model, token)
    diarization = pipeline(str(input_path))
    rows = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        rows.append(
            {
                "start": round(float(turn.start), 3),
                "end": round(float(turn.end), 3),
                "speaker": str(speaker),
            }
        )
    return rows


def _pyannote_pipeline_from_pretrained(pipeline_cls, speaker_model: str, token: str):
    try:
        return pipeline_cls.from_pretrained(speaker_model, token=token)
    except TypeError as exc:
        if "token" not in str(exc):
            raise
        return pipeline_cls.from_pretrained(speaker_model, use_auth_token=token)


def require_diarization_runtime() -> None:
    if not _huggingface_token():
        raise RuntimeError("speaker diarization requires HF_TOKEN/HUGGINGFACE_TOKEN for gated pyannote models")
    try:
        import pyannote.audio  # noqa: F401
    except Exception as exc:
        raise RuntimeError("speaker diarization requires pyannote.audio in the worker image") from exc


def probe_runtime(*, diarize: bool = False, require_gpu: bool = False) -> dict:
    checks = {
        "ffmpeg_present": bool(shutil.which("ffmpeg")),
        "faster_whisper_import": False,
        "pyannote_import": not diarize,
        "matplotlib_import": not diarize,
        "image_contract_marker_present": (not diarize) or Path(ASR_DIARIZATION_IMAGE_CONTRACT_MARKER).is_file(),
        "nvidia_smi_present": (not require_gpu) or bool(shutil.which("nvidia-smi")),
    }
    errors: dict[str, str] = {}
    try:
        import faster_whisper  # noqa: F401

        checks["faster_whisper_import"] = True
    except Exception as exc:
        errors["faster_whisper_import"] = str(exc)
    if diarize:
        try:
            import pyannote.audio  # noqa: F401

            checks["pyannote_import"] = True
        except Exception as exc:
            errors["pyannote_import"] = str(exc)
        try:
            import matplotlib  # noqa: F401

            checks["matplotlib_import"] = True
        except Exception as exc:
            errors["matplotlib_import"] = str(exc)
    ok = all(bool(value) for value in checks.values())
    return {
        "ok": ok,
        "probe": "asr_worker_runtime",
        "diarize": diarize,
        "require_gpu": require_gpu,
        "image_contract_id": ASR_DIARIZATION_IMAGE_CONTRACT_ID if diarize else "",
        "image_contract_marker": ASR_DIARIZATION_IMAGE_CONTRACT_MARKER if diarize else "",
        "cache_hit": bool(
            diarize
            and checks["image_contract_marker_present"]
            and checks["faster_whisper_import"]
            and checks["pyannote_import"]
            and checks["matplotlib_import"]
        ),
        "checks": checks,
        "errors": errors,
    }


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
        text = str(segment.get("text") or "").strip()
        label = f"{speaker}: " if speaker else ""
        blocks.append(
            f"{index}\n"
            f"{format_timestamp(float(segment.get('start') or 0))} --> {format_timestamp(float(segment.get('end') or 0))}\n"
            f"{label}{text}"
        )
    return "\n\n".join(blocks).strip() + ("\n" if blocks else "")


def render_vtt(segments: list[dict]) -> str:
    body = []
    for segment in segments:
        speaker = str(segment.get("speaker") or "").strip()
        text = str(segment.get("text") or "").strip()
        label = f"{speaker}: " if speaker else ""
        body.append(
            f"{format_timestamp(float(segment.get('start') or 0), sep='.')} --> "
            f"{format_timestamp(float(segment.get('end') or 0), sep='.')}\n"
            f"{label}{text}"
        )
    return "WEBVTT\n\n" + "\n\n".join(body).strip() + ("\n" if body else "")


def write_artifacts(
    artifact_dir: Path,
    result: dict,
    metrics: dict,
    stdout: str = "",
    stderr: str = "",
) -> dict:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    cache_hit = bool(metrics.get("cache_hit") or result.get("cache_hit"))
    probe_info = {
        "provider": result.get("provider") or metrics.get("provider") or "",
        "worker_image": metrics.get("worker_image") or result.get("worker_image") or ASR_DIARIZATION_WORKER_IMAGE,
        "loaded_model_id": result.get("diarization_model") or result.get("model") or metrics.get("model") or "",
        "image_contract_id": metrics.get("image_contract_id")
        or result.get("image_contract_id")
        or (ASR_DIARIZATION_IMAGE_CONTRACT_ID if result.get("diarization_requested") else ""),
        "image_contract_marker": metrics.get("image_contract_marker")
        or result.get("image_contract_marker")
        or (ASR_DIARIZATION_IMAGE_CONTRACT_MARKER if result.get("diarization_requested") else ""),
        "image_contract_marker_present": bool(metrics.get("image_contract_marker_present") or result.get("image_contract_marker_present")),
        "cache_hit": cache_hit,
        "execution_mode": "asr_worker",
    }
    diarization_ok = (not result.get("diarization_requested")) or (
        bool(result.get("speaker_segments")) and not bool(result.get("diarization_error"))
    )
    verify = {
        "ok": bool(result.get("text")) and float(result.get("duration_seconds") or 0) > 0 and diarization_ok,
        "required": REQUIRED_ARTIFACTS,
        "missing": [],
        "checks": {
            "text_nonempty": bool(result.get("text")),
            "duration_positive": float(result.get("duration_seconds") or 0) > 0,
            "segments_nonempty": bool(result.get("segments")),
            "diarization_ok": diarization_ok,
        },
    }
    (artifact_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (artifact_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (artifact_dir / "probe_info.json").write_text(json.dumps(probe_info, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (artifact_dir / "verify.json").write_text(json.dumps(verify, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (artifact_dir / "transcript.srt").write_text(render_srt(list(result.get("segments") or [])))
    (artifact_dir / "transcript.vtt").write_text(render_vtt(list(result.get("segments") or [])))
    (artifact_dir / "speaker_timeline.json").write_text(
        json.dumps(result.get("speaker_segments") or [], ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    (artifact_dir / "stdout.log").write_text(stdout)
    (artifact_dir / "stderr.log").write_text(stderr)
    return verify


def run_asr(args: argparse.Namespace) -> int:
    started = time.time()
    input_path = resolve_local_input(args.input_uri)
    model_name = normalize_faster_whisper_model(args.model_name)
    try:
        result = transcribe_file(
            input_path=input_path,
            job_id=args.job_id,
            provider=args.provider,
            model_name=model_name,
            language=args.language,
            compute_type=args.compute_type,
            diarize=args.diarize,
            speaker_model=args.speaker_model,
        )
    except Exception as exc:
        result = {
            "job_id": args.job_id,
            "provider": args.provider,
            "model": model_name,
            "device": "cuda",
            "compute_type": args.compute_type,
            "language": args.language,
            "language_probability": 0.0,
            "duration_seconds": 0.0,
            "text": "",
            "segments": [],
            "diarization_requested": args.diarize,
            "diarization_model": args.speaker_model if args.diarize else "",
            "diarization_error": str(exc) if args.diarize else "",
            "speaker_count": 0,
            "speaker_segments": [],
            "error": str(exc),
            "runtime_seconds": round(time.time() - started, 3),
        }
    gpu_logs, gpu_metrics = collect_gpu_probe()
    runtime_probe = probe_runtime(diarize=args.diarize, require_gpu=False)
    image_contract_marker_present = bool(runtime_probe.get("checks", {}).get("image_contract_marker_present"))
    cache_hit = bool(runtime_probe.get("cache_hit"))
    result.update(
        {
            "image_contract_id": ASR_DIARIZATION_IMAGE_CONTRACT_ID if args.diarize else "",
            "image_contract_marker": ASR_DIARIZATION_IMAGE_CONTRACT_MARKER if args.diarize else "",
            "image_contract_marker_present": image_contract_marker_present,
            "cache_hit": cache_hit,
            "worker_image": ASR_DIARIZATION_WORKER_IMAGE if args.diarize else "",
            "loaded_model_id": args.speaker_model if args.diarize else model_name,
        }
    )
    metrics = {
        "job_id": args.job_id,
        "provider": args.provider,
        "gpu_profile": args.gpu_profile,
        "model": model_name,
        "input_uri": args.input_uri,
        "input_bytes": input_path.stat().st_size,
        "segment_count": len(result["segments"]),
        "text_chars": len(result["text"]),
        "diarization_requested": args.diarize,
        "diarization_ok": not bool(result.get("diarization_error")),
        "speaker_count": result.get("speaker_count"),
        "worker_image": ASR_DIARIZATION_WORKER_IMAGE if args.diarize else "",
        "image_contract_id": ASR_DIARIZATION_IMAGE_CONTRACT_ID if args.diarize else "",
        "image_contract_marker": ASR_DIARIZATION_IMAGE_CONTRACT_MARKER if args.diarize else "",
        "image_contract_marker_present": image_contract_marker_present,
        "cache_hit": cache_hit,
        "runtime_seconds": round(time.time() - started, 3),
        "remote_runtime_seconds": result["runtime_seconds"],
    }
    metrics.update(gpu_metrics)
    verify = write_artifacts(
        Path(args.artifact_dir),
        result,
        metrics,
        stdout=json.dumps(gpu_logs, ensure_ascii=False, indent=2),
        stderr=str(result.get("error") or ""),
    )
    return 0 if verify["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gpu-job-asr-worker")
    parser.add_argument("--job-id")
    parser.add_argument("--artifact-dir")
    parser.add_argument("--gpu-profile")
    parser.add_argument("--input-uri")
    parser.add_argument("--provider", default="container")
    parser.add_argument("--model-name", default="large-v3")
    parser.add_argument("--language", default="ja")
    parser.add_argument("--compute-type", default="int8_float16")
    parser.add_argument("--diarize", action="store_true")
    parser.add_argument("--speaker-model", default=DEFAULT_SPEAKER_MODEL)
    parser.add_argument("--probe-runtime", action="store_true")
    parser.add_argument("--require-gpu", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.probe_runtime:
        result = probe_runtime(diarize=args.diarize, require_gpu=args.require_gpu)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["ok"] else 1
    missing = [name for name in ("job_id", "artifact_dir", "gpu_profile", "input_uri") if not getattr(args, name)]
    if missing:
        raise SystemExit(f"missing required argument(s): {', '.join('--' + name.replace('_', '-') for name in missing)}")
    return run_asr(args)


if __name__ == "__main__":
    raise SystemExit(main())
