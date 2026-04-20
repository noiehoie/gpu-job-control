from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re
import shutil
import subprocess

from .models import Job, now_unix


def run_cpu_workflow_helper(job: Job, artifact_dir: Path) -> dict[str, Any]:
    payload = job.metadata.get("input") if isinstance(job.metadata.get("input"), dict) else {}
    action = str(
        payload.get("action")
        or payload.get("plugin")
        or job.metadata.get("workflow_plugin")
        or job.metadata.get("workflow_splitter")
        or job.metadata.get("workflow_reducer")
        or ""
    )
    if not action:
        action = _action_from_uri(job.input_uri)
    if action == "ffprobe_estimator":
        return _ffprobe_estimator(job, payload)
    if action == "ffmpeg_time_splitter":
        return _ffmpeg_time_splitter(job, payload, artifact_dir)
    if action == "timeline_reducer":
        return _timeline_reducer(job, payload)
    if action == "pdf_page_estimator":
        return _pdf_page_estimator(job, payload)
    if action == "pdf_page_splitter":
        return _pdf_page_splitter(job, payload, artifact_dir)
    if action == "page_result_merger":
        return _page_result_merger(job, payload)
    raise RuntimeError(f"unknown cpu_workflow_helper action: {action}")


def _action_from_uri(input_uri: str) -> str:
    if input_uri.startswith("workflow://"):
        tail = input_uri.rstrip("/").rsplit("/", 1)[-1]
        if tail:
            return tail
    return ""


def _local_path(value: str) -> Path:
    raw = str(value or "")
    if raw.startswith("file://"):
        raw = raw.removeprefix("file://")
    if not raw:
        raise RuntimeError("missing local input path")
    if raw.startswith(("http://", "https://", "s3://", "gs://")):
        raise RuntimeError(f"cpu_workflow_helper requires local file input, got remote URI: {raw}")
    path = Path(raw).expanduser()
    if not path.is_file():
        raise RuntimeError(f"input file not found: {path}")
    return path


def _run_json(command: list[str]) -> dict[str, Any]:
    proc = subprocess.run(command, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(command)}\n{proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"command returned invalid JSON: {' '.join(command)}") from exc


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"required tool not found on worker PATH: {name}")
    return path


def _ffprobe_estimator(job: Job, payload: dict[str, Any]) -> dict[str, Any]:
    ffprobe = _require_tool("ffprobe")
    path = _local_path(str(payload.get("input_uri") or payload.get("path") or job.input_uri))
    data = _run_json(
        [
            ffprobe,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ]
    )
    duration = float((data.get("format") or {}).get("duration") or 0)
    streams = data.get("streams") if isinstance(data.get("streams"), list) else []
    return {
        "ok": True,
        "action": "ffprobe_estimator",
        "input_path": str(path),
        "duration_seconds": duration,
        "stream_count": len(streams),
        "streams": [
            {
                "index": stream.get("index"),
                "codec_type": stream.get("codec_type"),
                "codec_name": stream.get("codec_name"),
                "duration": stream.get("duration"),
            }
            for stream in streams
            if isinstance(stream, dict)
        ],
    }


def _ffmpeg_time_splitter(job: Job, payload: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    ffmpeg = _require_tool("ffmpeg")
    path = _local_path(str(payload.get("input_uri") or payload.get("path") or job.input_uri))
    segment_seconds = max(1, int(payload.get("segment_seconds") or payload.get("chunk_seconds") or 600))
    out_dir = artifact_dir / "segments"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix or ".mp4"
    pattern = out_dir / f"segment-%05d{suffix}"
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-y",
            "-i",
            str(path),
            "-map",
            "0",
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(segment_seconds),
            "-reset_timestamps",
            "1",
            str(pattern),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg split failed ({proc.returncode}): {proc.stderr.strip()}")
    duration = _media_duration_seconds(path)
    segments = []
    for index, item in enumerate(sorted(out_dir.glob(f"*{suffix}"))):
        start = float(index * segment_seconds)
        end = min(duration, float((index + 1) * segment_seconds)) if duration > 0 else float((index + 1) * segment_seconds)
        segments.append(
            {
                "index": index,
                "path": str(item),
                "bytes": item.stat().st_size,
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "duration_seconds": round(max(0.0, end - start), 3),
            }
        )
    return {
        "ok": bool(segments),
        "action": "ffmpeg_time_splitter",
        "input_path": str(path),
        "segment_seconds": segment_seconds,
        "duration_seconds": duration,
        "segments": segments,
        "count": len(segments),
    }


def _timeline_reducer(job: Job, payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("items")
    if not isinstance(items, list):
        items = payload.get("segments") if isinstance(payload.get("segments"), list) else []
    timeline = []
    segments = []
    speaker_segments = []
    text_parts = []
    for index, item in enumerate(items):
        if isinstance(item, dict):
            row = dict(item)
        else:
            row = {"value": item}
        row.setdefault("index", index)
        text = str(row.get("text") or row.get("transcript") or "")
        if text:
            text_parts.append(text)
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        for segment in result.get("segments") or []:
            if isinstance(segment, dict):
                segments.append(dict(segment))
        for segment in result.get("speaker_segments") or []:
            if isinstance(segment, dict):
                speaker_segments.append(dict(segment))
        timeline.append(row)
    return {
        "ok": True,
        "action": "timeline_reducer",
        "timeline": timeline,
        "count": len(timeline),
        "text": "\n".join(text_parts),
        "segments": segments,
        "speaker_segments": speaker_segments,
        "speaker_count": len({str(item.get("speaker")) for item in speaker_segments if item.get("speaker")}),
    }


def _media_duration_seconds(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        data = _run_json([ffprobe, "-v", "error", "-show_format", "-of", "json", str(path)])
        return float((data.get("format") or {}).get("duration") or 0)
    except Exception:
        return 0.0


def _pdf_page_estimator(job: Job, payload: dict[str, Any]) -> dict[str, Any]:
    path = _local_path(str(payload.get("input_uri") or payload.get("path") or job.input_uri))
    count, method = _pdf_page_count(path)
    return {"ok": True, "action": "pdf_page_estimator", "input_path": str(path), "page_count": count, "method": method}


def _pdf_page_splitter(job: Job, payload: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    path = _local_path(str(payload.get("input_uri") or payload.get("path") or job.input_uri))
    out_dir = artifact_dir / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    pages, method = _split_pdf_pages(path, out_dir)
    return {
        "ok": bool(pages),
        "action": "pdf_page_splitter",
        "input_path": str(path),
        "method": method,
        "pages": pages,
        "count": len(pages),
    }


def _page_result_merger(job: Job, payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("items")
    if not isinstance(items, list):
        items = payload.get("pages") if isinstance(payload.get("pages"), list) else []
    pages = []
    text_parts = []
    for index, item in enumerate(items):
        if isinstance(item, dict):
            row = dict(item)
            text = str(row.get("text") or "")
        else:
            row = {"text": str(item)}
            text = str(item)
        row.setdefault("page", index + 1)
        pages.append(row)
        if text:
            text_parts.append(text)
    return {"ok": True, "action": "page_result_merger", "pages": pages, "count": len(pages), "text": "\n".join(text_parts)}


def _pdf_page_count(path: Path) -> tuple[int, str]:
    reader_class = _pdf_reader_class()
    if reader_class is not None:
        with path.open("rb") as fh:
            reader = reader_class(fh)
            return len(reader.pages), reader_class.__module__.split(".")[0]
    pdfinfo = shutil.which("pdfinfo")
    if pdfinfo:
        proc = subprocess.run([pdfinfo, str(path)], text=True, capture_output=True, check=False)
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.lower().startswith("pages:"):
                    return int(line.split(":", 1)[1].strip()), "pdfinfo"
    raw = path.read_bytes()
    count = len(re.findall(rb"/Type\s*/Page\b", raw))
    if count:
        return count, "pdf-token-scan"
    raise RuntimeError("could not determine PDF page count; install pypdf, PyPDF2, or pdfinfo")


def _split_pdf_pages(path: Path, out_dir: Path) -> tuple[list[dict[str, Any]], str]:
    writer_class, reader_class = _pdf_writer_reader_classes()
    if writer_class is not None and reader_class is not None:
        pages = []
        with path.open("rb") as fh:
            reader = reader_class(fh)
            for index, page in enumerate(reader.pages):
                writer = writer_class()
                writer.add_page(page)
                out = out_dir / f"page-{index + 1:05d}.pdf"
                with out.open("wb") as out_fh:
                    writer.write(out_fh)
                pages.append({"page": index + 1, "path": str(out), "bytes": out.stat().st_size})
        return pages, writer_class.__module__.split(".")[0]
    qpdf = shutil.which("qpdf")
    if qpdf:
        page_count, _ = _pdf_page_count(path)
        pages = []
        for page in range(1, page_count + 1):
            out = out_dir / f"page-{page:05d}.pdf"
            proc = subprocess.run(
                [qpdf, str(path), "--pages", str(path), str(page), "--", str(out)], text=True, capture_output=True, check=False
            )
            if proc.returncode != 0:
                raise RuntimeError(f"qpdf page split failed ({proc.returncode}): {proc.stderr.strip()}")
            pages.append({"page": page, "path": str(out), "bytes": out.stat().st_size})
        return pages, "qpdf"
    raise RuntimeError("PDF splitting requires pypdf, PyPDF2, or qpdf on the CPU worker")


def _pdf_reader_class() -> Any:
    try:
        from pypdf import PdfReader

        return PdfReader
    except Exception:
        pass
    try:
        from PyPDF2 import PdfReader

        return PdfReader
    except Exception:
        return None


def _pdf_writer_reader_classes() -> tuple[Any, Any]:
    try:
        from pypdf import PdfReader, PdfWriter

        return PdfWriter, PdfReader
    except Exception:
        pass
    try:
        from PyPDF2 import PdfReader, PdfWriter

        return PdfWriter, PdfReader
    except Exception:
        return None, None


def helper_metrics(job: Job, result: dict[str, Any], runtime_seconds: int) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "runtime_seconds": runtime_seconds,
        "model": job.model,
        "gpu_profile": job.gpu_profile,
        "worker_image": job.worker_image,
        "job_type": job.job_type,
        "provider": job.provider,
        "action": result.get("action"),
        "ok": bool(result.get("ok")),
        "recorded_at": now_unix(),
    }
