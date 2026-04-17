from __future__ import annotations

from typing import Any

from .models import now_unix
from .policy import load_execution_policy
from .store import JobStore


RETENTION_VERSION = "gpu-job-retention-v1"


def retention_report(store: JobStore | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    store = store or JobStore()
    policy = policy or load_execution_policy()
    retention = dict(policy.get("retention", {}))
    artifact_days = int(retention.get("artifact_retention_days") or 30)
    audit_days = int(retention.get("audit_retention_days") or 365)
    now = now_unix()
    expired_artifacts = []
    for job in store.list_jobs(limit=5000):
        finished = int(job.finished_at or 0)
        if not finished:
            continue
        retention_until = finished + artifact_days * 86400
        if retention_until < now:
            expired_artifacts.append(
                {"job_id": job.job_id, "retention_until": retention_until, "artifact_dir": str(store.artifact_dir(job.job_id))}
            )
    return {
        "ok": True,
        "retention_version": RETENTION_VERSION,
        "artifact_retention_days": artifact_days,
        "audit_retention_days": audit_days,
        "expired_artifact_count": len(expired_artifacts),
        "expired_artifacts": expired_artifacts[:100],
        "note": "report only; no deletion is performed",
    }
