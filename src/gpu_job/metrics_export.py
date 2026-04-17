from __future__ import annotations

from typing import Any

from .capacity import queue_capacity
from .guard import collect_cost_guard
from .stats import collect_stats


METRICS_VERSION = "gpu-job-metrics-v1"


def metrics_snapshot() -> dict[str, Any]:
    stats = collect_stats()
    guard = collect_cost_guard()
    capacity = queue_capacity()
    return {
        "ok": bool(stats.get("ok")) and bool(guard.get("ok")) and bool(capacity.get("ok")),
        "metrics_version": METRICS_VERSION,
        "stats": stats,
        "guard_ok": guard.get("ok"),
        "estimated_hourly_usd": guard.get("estimated_hourly_usd"),
        "capacity": capacity,
    }


def metrics_prometheus() -> str:
    snap = metrics_snapshot()
    lines = [
        "# HELP gpu_job_readiness_ok GPU job metrics snapshot ok.",
        "# TYPE gpu_job_readiness_ok gauge",
        f"gpu_job_readiness_ok {1 if snap['ok'] else 0}",
        "# HELP gpu_job_estimated_hourly_usd Estimated active hourly spend.",
        "# TYPE gpu_job_estimated_hourly_usd gauge",
        f"gpu_job_estimated_hourly_usd {snap.get('estimated_hourly_usd') or 0}",
    ]
    capacity = snap.get("capacity", {})
    if isinstance(capacity, dict):
        for provider, item in dict(capacity.get("providers", {})).items():
            lines.append(f'gpu_job_provider_active{{provider="{provider}"}} {item.get("active") or 0}')
            lines.append(f'gpu_job_provider_queued{{provider="{provider}"}} {item.get("queued") or 0}')
    return "\n".join(lines) + "\n"
