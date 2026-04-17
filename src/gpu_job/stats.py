from __future__ import annotations

from pathlib import Path
from statistics import median
from typing import Any
import json

from .models import Job
from .store import JobStore


def _safe_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def collect_stats(store: JobStore | None = None) -> dict[str, Any]:
    store = store or JobStore()
    store.ensure()
    groups: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(store.jobs_dir.glob("*.json")):
        job = Job.from_file(path)
        key = f"{job.provider or 'unknown'}:{job.job_type}:{job.gpu_profile}"
        artifact_dir = store.artifact_dir(job.job_id)
        metrics = _safe_json(artifact_dir / "metrics.json")
        groups.setdefault(key, []).append(
            {
                "job_id": job.job_id,
                "status": job.status,
                "provider": job.provider,
                "job_type": job.job_type,
                "gpu_profile": job.gpu_profile,
                "model": job.model,
                "runtime_seconds": job.runtime_seconds,
                "remote_runtime_seconds": metrics.get("remote_runtime_seconds"),
                "startup_overhead_seconds": _startup_overhead(job.runtime_seconds, metrics.get("remote_runtime_seconds")),
                "artifact_bytes": job.artifact_bytes,
                "error": job.error,
            }
        )

    summaries = {}
    for key, rows in groups.items():
        succeeded = [row for row in rows if row["status"] == "succeeded"]
        runtime_values = [float(row["runtime_seconds"]) for row in succeeded if row.get("runtime_seconds") is not None]
        remote_values = [float(row["remote_runtime_seconds"]) for row in succeeded if row.get("remote_runtime_seconds") is not None]
        overhead_values = [float(row["startup_overhead_seconds"]) for row in succeeded if row.get("startup_overhead_seconds") is not None]
        summaries[key] = {
            "total": len(rows),
            "succeeded": len(succeeded),
            "failed": len([row for row in rows if row["status"] == "failed"]),
            "runtime_seconds": _series(runtime_values),
            "remote_runtime_seconds": _series(remote_values),
            "startup_overhead_seconds": _series(overhead_values),
            "recent": rows[-5:],
        }
    return {"ok": True, "groups": summaries}


def _startup_overhead(runtime_seconds: int | None, remote_runtime_seconds: object) -> float | None:
    if runtime_seconds is None or remote_runtime_seconds is None:
        return None
    try:
        return max(0.0, float(runtime_seconds) - float(remote_runtime_seconds))
    except (TypeError, ValueError):
        return None


def _series(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "p50": None, "max": None}
    return {
        "min": round(min(values), 3),
        "p50": round(median(values), 3),
        "max": round(max(values), 3),
    }
