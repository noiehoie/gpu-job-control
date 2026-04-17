from __future__ import annotations

from typing import Any

from .models import Job, now_unix
from .policy import load_execution_policy
from .router import route_job
from .store import JobStore, StoreLock


ACTIVE_STATUSES = {"starting", "running"}
QUEUED_STATUSES = {"queued"}
DEFAULT_RETRY_AFTER_SECONDS = 30


def selected_provider_for_job(job: Job, resolve_auto: bool = True) -> str:
    provider = str(job.metadata.get("selected_provider") or job.metadata.get("requested_provider") or job.provider or "auto")
    if provider == "auto" and resolve_auto:
        return str(route_job(job)["selected_provider"])
    return provider


def compact_job(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "gpu_profile": job.gpu_profile,
        "provider": job.provider,
        "selected_provider": job.metadata.get("selected_provider"),
        "requested_provider": job.metadata.get("requested_provider"),
        "status": job.status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "runtime_seconds": job.runtime_seconds,
        "artifact_count": job.artifact_count,
        "artifact_bytes": job.artifact_bytes,
        "exit_code": job.exit_code,
        "error": _clip(str(job.error or ""), 500),
    }


def queue_capacity(limit: int = 1000) -> dict[str, Any]:
    store = JobStore()
    policy = load_execution_policy()
    provider_limits = {str(k): int(v) for k, v in dict(policy.get("provider_limits", {})).items()}
    jobs = store.list_jobs(limit=limit)
    counts: dict[str, int] = {}
    by_job_type: dict[str, dict[str, int]] = {}
    providers: dict[str, dict[str, Any]] = {
        provider: {
            "provider": provider,
            "max_concurrent": max(0, max_concurrent),
            "active": 0,
            "queued": 0,
            "available_slots": max(0, max_concurrent),
            "saturated": False,
            "expected_wait_seconds": 0,
        }
        for provider, max_concurrent in provider_limits.items()
    }

    for job in jobs:
        counts[job.status] = counts.get(job.status, 0) + 1
        typed = by_job_type.setdefault(job.job_type, {})
        typed[job.status] = typed.get(job.status, 0) + 1
        if job.status not in ACTIVE_STATUSES | QUEUED_STATUSES:
            continue
        try:
            provider = selected_provider_for_job(job, resolve_auto=False)
        except Exception:
            provider = str(job.metadata.get("selected_provider") or job.provider or "unknown")
        entry = providers.setdefault(
            provider,
            {
                "provider": provider,
                "max_concurrent": 1,
                "active": 0,
                "queued": 0,
                "available_slots": 1,
                "saturated": False,
                "expected_wait_seconds": 0,
            },
        )
        if job.status in ACTIVE_STATUSES:
            entry["active"] += 1
        elif job.status in QUEUED_STATUSES:
            entry["queued"] += 1

    for entry in providers.values():
        max_concurrent = int(entry.get("max_concurrent") or 0)
        active = int(entry.get("active") or 0)
        queued = int(entry.get("queued") or 0)
        entry["available_slots"] = max(0, max_concurrent - active)
        entry["saturated"] = max_concurrent > 0 and active >= max_concurrent
        if entry["saturated"] or queued:
            entry["expected_wait_seconds"] = DEFAULT_RETRY_AFTER_SECONDS * max(1, queued + 1)

    return {
        "ok": True,
        "counts": counts,
        "by_job_type": by_job_type,
        "provider_limits": provider_limits,
        "providers": providers,
        "active_total": sum(int(p.get("active") or 0) for p in providers.values()),
        "queued_total": sum(int(p.get("queued") or 0) for p in providers.values()),
    }


def reserve_direct_execution_slot(job: Job, provider: str) -> dict[str, Any]:
    store = JobStore()
    policy = load_execution_policy()
    max_concurrent = int(dict(policy.get("provider_limits", {})).get(provider, 1))
    with StoreLock(store.lock_path(f"capacity-{provider}")):
        capacity = queue_capacity()
        provider_capacity = dict(capacity.get("providers", {}).get(provider, {}))
        active = int(provider_capacity.get("active") or 0)
        queued = int(provider_capacity.get("queued") or 0)
        if max_concurrent > 0 and active >= max_concurrent:
            retry_after = int(provider_capacity.get("expected_wait_seconds") or DEFAULT_RETRY_AFTER_SECONDS)
            return {
                "ok": False,
                "error": "provider concurrency limit reached",
                "provider": provider,
                "max_concurrent": max_concurrent,
                "active": active,
                "queued": queued,
                "retry_after_seconds": retry_after,
            }
        job.provider = provider
        job.status = "starting"
        job.started_at = job.started_at or now_unix()
        job.metadata["selected_provider"] = provider
        store.save(job)
        return {
            "ok": True,
            "provider": provider,
            "max_concurrent": max_concurrent,
            "active_before": active,
            "queued_before": queued,
        }


def _clip(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}...<truncated chars={len(value)}>"
