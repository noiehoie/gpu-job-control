from __future__ import annotations

from typing import Any

from .models import Job, now_unix
from .store import JobStore


DLQ_VERSION = "gpu-job-dlq-v1"


def dead_letter(job: Job, reason: str, error_class: str = "dead_lettered", store: JobStore | None = None) -> dict[str, Any]:
    store = store or JobStore()
    job.status = "failed"
    job.error = reason
    job.finished_at = now_unix()
    job.metadata["dead_letter"] = {
        "dlq_version": DLQ_VERSION,
        "reason": reason,
        "error_class": error_class,
        "dead_lettered_at": now_unix(),
    }
    store.save(job)
    return {"ok": True, "job_id": job.job_id, "reason": reason, "error_class": error_class}


def dlq_status(limit: int = 100) -> dict[str, Any]:
    store = JobStore()
    jobs = [job for job in store.list_jobs(status="failed", limit=limit) if "dead_letter" in job.metadata]
    return {"ok": True, "count": len(jobs), "jobs": [job.to_dict() for job in jobs]}
