from __future__ import annotations

from typing import Any

from .capacity import queue_capacity
from .guard import collect_cost_guard
from .stats import collect_stats
from .workflow import workflow_budget_monitor


METRICS_VERSION = "gpu-job-metrics-v1"


def metrics_snapshot() -> dict[str, Any]:
    stats = collect_stats()
    guard = collect_cost_guard()
    capacity = queue_capacity()
    workflow_budget = workflow_budget_monitor()
    return {
        "ok": bool(stats.get("ok")) and bool(guard.get("ok")) and bool(capacity.get("ok")) and bool(workflow_budget.get("ok")),
        "metrics_version": METRICS_VERSION,
        "stats": stats,
        "guard_ok": guard.get("ok"),
        "estimated_hourly_usd": guard.get("estimated_hourly_usd"),
        "capacity": capacity,
        "workflow_budget": workflow_budget,
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
    workflow_budget = snap.get("workflow_budget", {})
    totals = workflow_budget.get("totals") if isinstance(workflow_budget, dict) else {}
    if isinstance(totals, dict):
        lines.extend(
            [
                "# HELP gpu_job_workflow_count Stored workflow count in the metrics window.",
                "# TYPE gpu_job_workflow_count gauge",
                f"gpu_job_workflow_count {totals.get('workflow_count') or 0}",
                "# HELP gpu_job_workflow_running_or_queued Running, queued, or approved workflow count.",
                "# TYPE gpu_job_workflow_running_or_queued gauge",
                f"gpu_job_workflow_running_or_queued {totals.get('running_or_queued') or 0}",
                "# HELP gpu_job_workflow_draining Draining workflow count.",
                "# TYPE gpu_job_workflow_draining gauge",
                f"gpu_job_workflow_draining {totals.get('draining') or 0}",
                "# HELP gpu_job_workflow_projected_cost_usd Sum of projected workflow cost.",
                "# TYPE gpu_job_workflow_projected_cost_usd gauge",
                f"gpu_job_workflow_projected_cost_usd {totals.get('projected_cost_usd') or 0}",
                "# HELP gpu_job_workflow_actual_cost_usd Sum of actual workflow cost.",
                "# TYPE gpu_job_workflow_actual_cost_usd gauge",
                f"gpu_job_workflow_actual_cost_usd {totals.get('actual_cost_usd') or 0}",
            ]
        )
    return "\n".join(lines) + "\n"
