from __future__ import annotations

from typing import Any

from .guard import collect_cost_guard
from .store import JobStore


RECONCILIATION_VERSION = "gpu-job-reconcile-v1"


def desired_resources(store: JobStore | None = None) -> list[dict[str, Any]]:
    store = store or JobStore()
    resources = []
    for job in store.list_jobs(limit=5000):
        if job.status not in {"starting", "running"}:
            continue
        resources.append(
            {
                "job_id": job.job_id,
                "provider": job.metadata.get("selected_provider") or job.provider,
                "provider_job_id": job.provider_job_id,
                "status": job.status,
            }
        )
    return resources


def reconcile_detect_only() -> dict[str, Any]:
    store = JobStore()
    guard = collect_cost_guard()
    desired = desired_resources(store)
    active_billable = guard.get("active_billable_resources", [])
    unknown_persistent = guard.get("unknown_persistent_resources", [])
    return {
        "ok": guard.get("ok", False) and not active_billable and not unknown_persistent,
        "reconciliation_version": RECONCILIATION_VERSION,
        "mode": "detect_only",
        "desired_resources": desired,
        "active_billable_resources": active_billable,
        "unknown_persistent_resources": unknown_persistent,
        "guard_ok": guard.get("ok", False),
    }
