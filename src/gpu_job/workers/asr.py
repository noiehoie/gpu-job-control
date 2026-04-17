from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
import argparse
import json
import time


REQUIRED_ARTIFACTS = ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"]


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


def transcribe_file(input_path: Path, job_id: str, provider: str, model_name: str, language: str, compute_type: str) -> dict:
    started = time.time()
    from faster_whisper import WhisperModel

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
        "runtime_seconds": round(time.time() - started, 3),
    }


def write_artifacts(
    artifact_dir: Path,
    result: dict,
    metrics: dict,
    stdout: str = "",
    stderr: str = "",
) -> dict:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    verify = {
        "ok": bool(result.get("text")) and float(result.get("duration_seconds") or 0) > 0,
        "required": REQUIRED_ARTIFACTS,
        "missing": [],
        "checks": {
            "text_nonempty": bool(result.get("text")),
            "duration_positive": float(result.get("duration_seconds") or 0) > 0,
            "segments_nonempty": bool(result.get("segments")),
        },
    }
    (artifact_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (artifact_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (artifact_dir / "verify.json").write_text(json.dumps(verify, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (artifact_dir / "stdout.log").write_text(stdout)
    (artifact_dir / "stderr.log").write_text(stderr)
    return verify


def run_asr(args: argparse.Namespace) -> int:
    started = time.time()
    input_path = resolve_local_input(args.input_uri)
    result = transcribe_file(
        input_path=input_path,
        job_id=args.job_id,
        provider=args.provider,
        model_name=args.model_name,
        language=args.language,
        compute_type=args.compute_type,
    )
    metrics = {
        "job_id": args.job_id,
        "provider": args.provider,
        "gpu_profile": args.gpu_profile,
        "model": args.model_name,
        "input_uri": args.input_uri,
        "input_bytes": input_path.stat().st_size,
        "segment_count": len(result["segments"]),
        "text_chars": len(result["text"]),
        "runtime_seconds": round(time.time() - started, 3),
        "remote_runtime_seconds": result["runtime_seconds"],
    }
    verify = write_artifacts(Path(args.artifact_dir), result, metrics)
    return 0 if verify["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gpu-job-asr-worker")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--gpu-profile", required=True)
    parser.add_argument("--input-uri", required=True)
    parser.add_argument("--provider", default="container")
    parser.add_argument("--model-name", default="large-v3")
    parser.add_argument("--language", default="ja")
    parser.add_argument("--compute-type", default="int8_float16")
    return parser


def main(argv: list[str] | None = None) -> int:
    return run_asr(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
