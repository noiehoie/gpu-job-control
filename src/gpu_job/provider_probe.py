from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import time

from .models import app_data_dir, now_unix
from .providers import PROVIDERS


PROBE_VERSION = "gpu-job-provider-probe-v1"


def probe_dir() -> Path:
    path = app_data_dir() / "provider-probes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def probe_provider(provider: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    if provider not in PROVIDERS:
        return {"ok": False, "provider": provider, "error": "unknown provider"}
    profile = profile or {}
    started = time.time()
    adapter = PROVIDERS[provider]
    doctor = adapter.doctor()
    signal = adapter.signal(profile)
    elapsed = round(time.time() - started, 3)
    record = {
        "probe_version": PROBE_VERSION,
        "provider": provider,
        "ok": bool(doctor.get("ok")) and bool(signal.get("available", doctor.get("ok"))),
        "recorded_at": now_unix(),
        "probe_runtime_seconds": elapsed,
        "doctor": doctor,
        "signal": signal,
    }
    _append_probe(record)
    return record


def probe_all_providers() -> dict[str, Any]:
    probes = [probe_provider(provider) for provider in sorted(PROVIDERS)]
    return {"ok": all(item.get("ok") for item in probes), "probe_version": PROBE_VERSION, "probes": probes}


def recent_probe_summary(limit: int = 100) -> dict[str, Any]:
    rows = []
    path = probe_dir() / "provider-probes.jsonl"
    if path.is_file():
        for line in path.read_text().splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    latest: dict[str, Any] = {}
    for row in rows:
        latest[str(row.get("provider") or "")] = row
    stats = _probe_stats(rows)
    return {"ok": True, "probe_version": PROBE_VERSION, "count": len(rows), "latest": latest, "stats": stats, "path": str(path)}


def active_canary_probe(provider: str) -> dict[str, Any]:
    from .models import Job
    from .runner import submit_job

    job = Job.from_dict(
        {
            "job_id": f"probe-canary-{provider}-{now_unix()}",
            "job_type": "smoke",
            "input_uri": "none://provider-canary",
            "output_uri": "local://provider-canary",
            "worker_image": "auto",
            "gpu_profile": "llm_heavy" if provider in {"modal", "runpod"} else "embedding",
            "provider": provider,
            "metadata": {"input": {"probe": True}, "routing": {"estimated_gpu_runtime_seconds": 5}},
        }
    )
    result = submit_job(job, provider_name=provider, execute=True)
    record = {
        "probe_version": PROBE_VERSION,
        "provider": provider,
        "probe_type": "active_canary",
        "ok": bool(result.get("ok")),
        "recorded_at": now_unix(),
        "job_id": job.job_id,
        "result": result,
    }
    _append_probe(record)
    return record


def _probe_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[float]] = {}
    startup: dict[str, list[float]] = {}
    for row in rows:
        provider = str(row.get("provider") or "")
        if not provider:
            continue
        if row.get("probe_runtime_seconds") is not None:
            grouped.setdefault(provider, []).append(float(row["probe_runtime_seconds"]))
        signal = row.get("signal") if isinstance(row.get("signal"), dict) else {}
        if signal.get("estimated_startup_seconds") is not None:
            startup.setdefault(provider, []).append(float(signal["estimated_startup_seconds"]))
    return {
        provider: {
            "probe_runtime_seconds": _percentiles(values),
            "estimated_startup_seconds": _percentiles(startup.get(provider, [])),
        }
        for provider, values in grouped.items()
    }


def _percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "count": 0}
    values = sorted(values)
    return {"p50": _percentile(values, 0.5), "p95": _percentile(values, 0.95), "count": len(values)}


def _percentile(values: list[float], q: float) -> float:
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * q))))
    return round(values[index], 6)


def _append_probe(record: dict[str, Any]) -> None:
    path = probe_dir() / "provider-probes.jsonl"
    with path.open("a") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
