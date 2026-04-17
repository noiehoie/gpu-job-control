from __future__ import annotations

from typing import Any

from .canonical import canonical_hash
from .models import Job
from .store import JobStore


def input_manifest_hash(job: Job) -> str:
    payload = {
        "input_uri": job.input_uri,
        "metadata_input": job.metadata.get("input", {}),
    }
    return canonical_hash(payload)["sha256"]


def idempotency_key(job: Job) -> str:
    explicit = str(job.metadata.get("idempotency_key") or "").strip()
    if explicit:
        return explicit
    payload = {
        "source_system": job.metadata.get("source_system") or "unknown",
        "source_job_id": job.metadata.get("source_job_id") or job.job_id,
        "job_type": job.job_type,
        "gpu_profile": job.gpu_profile,
        "input_manifest_hash": input_manifest_hash(job),
    }
    return canonical_hash(payload)["sha256"]


def find_duplicate(job: Job, store: JobStore | None = None) -> Job | None:
    store = store or JobStore()
    key = idempotency_key(job)
    for existing in store.list_jobs(limit=5000):
        if existing.job_id == job.job_id:
            continue
        if str(existing.metadata.get("idempotency_key") or "") != key:
            continue
        if existing.status not in {"failed", "cancelled"}:
            return existing
    return None


def apply_idempotency(job: Job, store: JobStore | None = None) -> dict[str, Any]:
    key = idempotency_key(job)
    job.metadata["idempotency_key"] = key
    duplicate = find_duplicate(job, store=store)
    return {
        "ok": duplicate is None,
        "idempotency_key": key,
        "duplicate_job_id": duplicate.job_id if duplicate else None,
        "duplicate_status": duplicate.status if duplicate else None,
    }
