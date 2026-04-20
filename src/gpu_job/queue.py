from __future__ import annotations

from typing import Any
import json
import time

from .capacity import ACTIVE_STATUSES, compact_job, queue_capacity
from .concurrency import flatten_provider_limits, provider_profile_key, provider_profile_limit
from .intake import plan_intake_groups
from .drain import drain_status
from .models import Job, now_unix
from .policy import load_execution_policy
from .remediation import apply_remediation
from .runner import submit_job
from .router import route_job
from .store import JobStore, StoreLock
from .timeout import timeout_contract
from .wal import wal_recovery_status


def enqueue_job(job: Job, provider_name: str = "auto") -> dict[str, Any]:
    store = JobStore()
    if job.status not in {"created", "planned", "queued"}:
        raise ValueError(f"cannot enqueue job in status: {job.status}")
    job.status = "queued"
    job.provider = provider_name
    job.metadata["requested_provider"] = provider_name
    path = store.save(job)
    return {"ok": True, "job": job.to_dict(), "path": str(path)}


def queue_status(limit: int = 100, compact: bool = False) -> dict[str, Any]:
    store = JobStore()
    jobs = store.list_jobs(limit=limit)
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.status] = counts.get(job.status, 0) + 1
    capacity = queue_capacity(limit=max(limit, 1000))
    return {
        "ok": True,
        "counts": counts,
        "capacity": capacity,
        "policy": load_execution_policy(),
        "jobs": [compact_job(job) if compact else job.to_dict() for job in jobs],
    }


def cancel_job(job_id: str, *, force: bool = False, reason: str = "") -> dict[str, Any]:
    store = JobStore()
    job = store.load(job_id)
    if job.status not in {"created", "buffered", "planned", "queued"}:
        if not force or job.status not in ACTIVE_STATUSES:
            raise ValueError(f"cannot cancel job in status: {job.status}")
        timeout = timeout_contract(job)
        elapsed = _active_elapsed_seconds(job)
        limit = int(timeout.get("max_runtime_seconds") or 0)
        if limit > 0 and elapsed < limit:
            raise ValueError(f"cannot force-cancel active job before timeout: elapsed={elapsed}s limit={limit}s")
        old_status = job.status
        job.status = "failed"
        job.finished_at = now_unix()
        job.runtime_seconds = elapsed
        job.exit_code = 124
        job.error = reason or f"force-cancelled stale {old_status} job after {elapsed}s"
        job.metadata["force_cancelled"] = {
            "old_status": old_status,
            "elapsed_seconds": elapsed,
            "timeout_contract": timeout,
            "reason": reason,
            "cancelled_at": job.finished_at,
        }
        store.save(job)
        return {"ok": True, "forced": True, "job": job.to_dict()}
    job.status = "cancelled"
    job.finished_at = now_unix()
    job.error = reason or job.error
    store.save(job)
    return {"ok": True, "job": job.to_dict()}


def cancel_group(source_system: str = "", workflow_id: str = "", task_family: str = "") -> dict[str, Any]:
    store = JobStore()
    cancelled = []
    for job in store.list_jobs(limit=5000):
        if job.status not in {"created", "buffered", "planned", "queued"}:
            continue
        metadata = job.metadata
        if source_system and str(metadata.get("source_system") or "") != source_system:
            continue
        if workflow_id and str(metadata.get("workflow_id") or "") != workflow_id:
            continue
        routing = metadata.get("routing")
        routing = routing if isinstance(routing, dict) else {}
        if task_family and str(routing.get("task_family") or metadata.get("task_family") or "") != task_family:
            continue
        job.status = "cancelled"
        job.finished_at = now_unix()
        job.metadata["group_cancelled"] = {
            "source_system": source_system,
            "workflow_id": workflow_id,
            "task_family": task_family,
        }
        store.save(job)
        cancelled.append(job.job_id)
    return {"ok": True, "cancelled_count": len(cancelled), "cancelled_job_ids": cancelled}


def retry_job(job_id: str) -> dict[str, Any]:
    store = JobStore()
    job = store.load(job_id)
    if job.status not in {"failed", "cancelled"}:
        raise ValueError(f"cannot retry job in status: {job.status}")
    job.status = "queued"
    job.error = ""
    job.exit_code = None
    job.started_at = None
    job.finished_at = None
    job.runtime_seconds = None
    job.metadata.pop("retry_after", None)
    store.save(job)
    return {"ok": True, "job": job.to_dict()}


def recover_stale_jobs(policy: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    policy = policy or load_execution_policy()
    stale_seconds = dict(policy.get("stale_seconds", {}))
    store = JobStore()
    now = now_unix()
    recovered = []
    for job in store.list_jobs(limit=1000):
        if job.status not in ACTIVE_STATUSES:
            continue
        timeout = timeout_contract(job, policy=policy)
        threshold = _effective_stale_threshold(job, stale_seconds, timeout)
        if threshold <= 0:
            continue
        base = _active_base_time(job)
        elapsed = now - base
        if elapsed < threshold:
            continue
        old_status = job.status
        job.status = "failed"
        job.finished_at = now
        job.exit_code = 124
        job.runtime_seconds = max(0, elapsed)
        job.error = f"stale {old_status} job exceeded {threshold}s"
        job.metadata["stale_recovered_at"] = now
        job.metadata["stale_recovery"] = {
            "old_status": old_status,
            "elapsed_seconds": elapsed,
            "threshold_seconds": threshold,
            "timeout_contract": timeout,
        }
        store.save(job)
        recovered.append(job.to_dict())
    return recovered


def _active_base_time(job: Job) -> int:
    if job.status == "starting":
        startup_started = job.metadata.get("startup_started_at")
        if startup_started is not None:
            return int(startup_started)
    return int(job.started_at or job.updated_at or job.created_at)


def _active_elapsed_seconds(job: Job) -> int:
    return max(0, now_unix() - _active_base_time(job))


def _effective_stale_threshold(job: Job, stale_seconds: dict[str, Any], timeout: dict[str, Any]) -> int:
    configured = int(stale_seconds.get(job.status, 0) or 0)
    timeout_limit = int(timeout.get("max_runtime_seconds") or 0)
    candidates = [value for value in (configured, timeout_limit) if value > 0]
    return min(candidates) if candidates else 0


def replan_queued_jobs(limit: int = 1000) -> dict[str, Any]:
    store = JobStore()
    replanned = []
    errors = []
    for job in store.list_jobs(status="queued", limit=limit):
        if job.provider and job.provider != "auto" and job.metadata.get("requested_provider") != "auto":
            continue
        old_provider = str(job.metadata.get("selected_provider") or job.provider or "")
        try:
            route = route_job(job)
        except Exception as exc:
            error = {
                "job_id": job.job_id,
                "error": str(exc),
                "replan_error_at": now_unix(),
            }
            job.metadata["replan_error"] = error
            store.save(job)
            errors.append(error)
            continue
        new_provider = str(route["selected_provider"])
        if new_provider != old_provider:
            job.metadata["selected_provider"] = new_provider
            job.metadata["replan"] = {
                "old_provider": old_provider,
                "new_provider": new_provider,
                "reason": route.get("decision", {}).get("reason"),
                "replanned_at": now_unix(),
            }
            store.save(job)
            replanned.append({"job_id": job.job_id, "old_provider": old_provider, "new_provider": new_provider})
    return {
        "ok": not errors,
        "replanned_count": len(replanned),
        "replanned": replanned,
        "error_count": len(errors),
        "errors": errors,
    }


def _requested_or_selected_provider(job: Job) -> str:
    selected = str(job.metadata.get("selected_provider") or "").strip()
    if selected:
        return selected
    requested = str(job.metadata.get("requested_provider") or job.provider or "auto")
    if requested == "auto":
        return str(route_job(job)["selected_provider"])
    return requested


def _active_counts(store: JobStore) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in store.list_jobs(limit=1000):
        if job.status not in ACTIVE_STATUSES:
            continue
        provider = str(job.metadata.get("selected_provider") or job.metadata.get("requested_provider") or job.provider or "unknown")
        key = provider_profile_key(provider, job.gpu_profile)
        counts[key] = counts.get(key, 0) + 1
    return counts


def next_runnable_job(store: JobStore, policy: dict[str, Any]) -> tuple[Job | None, dict[str, Any]]:
    queued = store.list_jobs(status="queued", limit=1000)
    queued.sort(key=lambda job: (job.created_at, job.job_id))
    limits = dict(policy.get("provider_limits", {}))
    active = _active_counts(store)
    skipped = []
    now = now_unix()
    for job in queued:
        retry_after = int(job.metadata.get("retry_after") or 0)
        if retry_after and retry_after > now:
            skipped.append(
                {"job_id": job.job_id, "provider": job.provider, "reason": "retry_after not reached", "retry_after": retry_after}
            )
            continue
        provider = _requested_or_selected_provider(job)
        profile_key = provider_profile_key(provider, job.gpu_profile)
        limit = provider_profile_limit(limits, provider, job.gpu_profile)
        if active.get(profile_key, 0) >= limit:
            skipped.append(
                {
                    "job_id": job.job_id,
                    "provider": provider,
                    "gpu_profile": job.gpu_profile,
                    "profile_key": profile_key,
                    "reason": "provider/profile concurrency limit reached",
                }
            )
            continue
        job.metadata["selected_provider"] = provider
        return job, {
            "active": active,
            "provider_limits": limits,
            "flattened_provider_limits": flatten_provider_limits(limits),
            "skipped": skipped,
        }
    return None, {
        "active": active,
        "provider_limits": limits,
        "flattened_provider_limits": flatten_provider_limits(limits),
        "skipped": skipped,
    }


def work_once() -> dict[str, Any]:
    store = JobStore()
    with StoreLock(store.lock_path("worker")):
        policy = load_execution_policy()
        recovered = recover_stale_jobs(policy)
        drain = drain_status(store=store)
        if drain.get("drain", {}).get("draining"):
            return {
                "ok": True,
                "worked": False,
                "reason": "worker is draining",
                "recovered": recovered,
                "drain": drain,
            }
        wal_recovery = wal_recovery_status(store=store)
        if not wal_recovery["ok"]:
            return {
                "ok": False,
                "worked": False,
                "reason": "wal recovery has ambiguous provider commits; dispatch blocked",
                "recovered": recovered,
                "wal_recovery": wal_recovery,
            }
        replan = replan_queued_jobs()
        intake = plan_intake_groups(policy)
        from .workflow import advance_workflows, enforce_workflow_budget_drains

        workflow_budget_drains = enforce_workflow_budget_drains()
        workflow_advance = advance_workflows()
        job, scheduling = next_runnable_job(store, policy)
        if not job:
            return {
                "ok": True,
                "worked": False,
                "reason": "no runnable queued job",
                "recovered": recovered,
                "replan": replan,
                "intake": intake,
                "workflow_budget_drains": workflow_budget_drains,
                "workflow_advance": workflow_advance,
                "scheduling": scheduling,
            }
        provider_name = str(job.metadata.get("selected_provider") or job.metadata.get("requested_provider") or job.provider or "auto")
        job.status = "starting"
        store.save(job)
        result = submit_job(job, provider_name=provider_name, execute=True, enforce_capacity=False)
        remediation = None
        if not result.get("ok"):
            result_job = result.get("job")
            if isinstance(result_job, dict):
                failed_job = Job.from_dict(result_job)
                remediation = apply_remediation(failed_job, policy=policy, store=store)
        return {
            "ok": bool(result.get("ok")) or bool(remediation and remediation.get("ok")),
            "worked": True,
            "recovered": recovered,
            "replan": replan,
            "intake": intake,
            "workflow_budget_drains": workflow_budget_drains,
            "workflow_advance": workflow_advance,
            "scheduling": scheduling,
            "result": result,
            "remediation": remediation,
        }


def work_loop(poll_interval: float = 5.0, once: bool = False) -> int:
    while True:
        result = work_once()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if once:
            return 0 if result.get("ok") else 1
        time.sleep(poll_interval)
