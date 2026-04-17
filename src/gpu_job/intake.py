from __future__ import annotations

from collections import defaultdict
from typing import Any
import hashlib
import time

from .capacity import compact_job
from .audit import append_audit
from .idempotency import apply_idempotency
from .models import Job, now_unix
from .policy import load_execution_policy
from .router import route_job
from .store import JobStore
from .telemetry import ensure_trace


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int_value(*values: Any, default: int = 0) -> int:
    for value in values:
        try:
            if value is not None and value != "":
                return int(float(value))
        except (TypeError, ValueError):
            continue
    return default


def _source_system(job: Job) -> str:
    return str(job.metadata.get("source_system") or "unknown").strip() or "unknown"


def _task_family(job: Job) -> str:
    return (
        str(
            job.metadata.get("task_family")
            or job.metadata.get("purpose")
            or _as_dict(job.metadata.get("routing")).get("task_family")
            or "default"
        ).strip()
        or "default"
    )


def intake_group_key(job: Job) -> str:
    parts = [
        _source_system(job),
        job.job_type,
        job.gpu_profile,
        _task_family(job),
    ]
    return "|".join(parts)


def _group_id(group_key: str, first_created_at: int) -> str:
    digest = hashlib.sha1(group_key.encode()).hexdigest()[:10]
    return f"group-{time.strftime('%Y%m%d-%H%M%S', time.localtime(first_created_at))}-{digest}"


def intake_job(job: Job, provider_name: str = "auto") -> dict[str, Any]:
    if job.status not in {"created", "planned", "queued", "buffered"}:
        raise ValueError(f"cannot intake job in status: {job.status}")
    store = JobStore()
    ensure_trace(job.metadata)
    idempotency = apply_idempotency(job, store=store)
    if not idempotency["ok"]:
        duplicate = store.load(str(idempotency["duplicate_job_id"]))
        append_audit("intake.duplicate", {"job_id": job.job_id, **idempotency}, store=store)
        return {
            "ok": True,
            "duplicate": True,
            "idempotency": idempotency,
            "job": compact_job(duplicate),
            "path": str(store.job_path(duplicate.job_id)),
        }
    now = now_unix()
    group_key = intake_group_key(job)
    intake = _as_dict(job.metadata.get("intake"))
    intake.update(
        {
            "group_key": group_key,
            "received_at": int(intake.get("received_at") or now),
            "state": "buffered",
        }
    )
    job.metadata["intake"] = intake
    job.metadata["requested_provider"] = provider_name
    job.provider = provider_name
    job.status = "buffered"
    path = store.save(job)
    append_audit("intake.buffered", {"job_id": job.job_id, "group_key": group_key, "idempotency": idempotency}, store=store)
    return {"ok": True, "job": compact_job(job), "path": str(path)}


def intake_status(limit: int = 100, compact: bool = True) -> dict[str, Any]:
    store = JobStore()
    buffered = store.list_jobs(status="buffered", limit=limit)
    groups = _group_buffered(buffered)
    return {
        "ok": True,
        "groups": [_group_summary(group_key, jobs) for group_key, jobs in sorted(groups.items())],
        "jobs": [compact_job(job) if compact else job.to_dict() for job in buffered],
    }


def _group_buffered(jobs: list[Job]) -> dict[str, list[Job]]:
    groups: dict[str, list[Job]] = defaultdict(list)
    for job in jobs:
        group_key = _as_dict(job.metadata.get("intake")).get("group_key") or intake_group_key(job)
        groups[str(group_key)].append(job)
    for group_jobs in groups.values():
        group_jobs.sort(key=lambda item: (item.created_at, item.job_id))
    return dict(groups)


def _group_summary(group_key: str, jobs: list[Job]) -> dict[str, Any]:
    if not jobs:
        return {"group_key": group_key, "size": 0}
    first_created_at = min(job.created_at for job in jobs)
    return {
        "group_key": group_key,
        "group_id": _group_id(group_key, first_created_at),
        "size": len(jobs),
        "first_created_at": first_created_at,
        "oldest_age_seconds": max(0, now_unix() - first_created_at),
        "job_type": jobs[0].job_type,
        "gpu_profile": jobs[0].gpu_profile,
        "source_system": _source_system(jobs[0]),
        "task_family": _task_family(jobs[0]),
        "job_ids": [job.job_id for job in jobs[:50]],
    }


def plan_intake_groups(policy: dict[str, Any] | None = None, now: int | None = None) -> dict[str, Any]:
    policy = policy or load_execution_policy()
    intake_policy = dict(policy.get("intake", {}))
    hold_seconds = int(intake_policy.get("hold_seconds") or 3)
    now = int(now or now_unix())
    store = JobStore()
    groups = _group_buffered(store.list_jobs(status="buffered", limit=1000))
    planned = []
    waiting = []
    errors = []
    for group_key, jobs in sorted(groups.items()):
        if not jobs:
            continue
        first_created_at = min(job.created_at for job in jobs)
        age = now - first_created_at
        if age < hold_seconds:
            waiting.append(
                {
                    "group_key": group_key,
                    "size": len(jobs),
                    "age_seconds": age,
                    "hold_seconds": hold_seconds,
                }
            )
            continue
        try:
            planned.append(_promote_group(store, group_key, jobs, now))
        except Exception as exc:
            errors.append({"group_key": group_key, "size": len(jobs), "error": str(exc)})
    return {"ok": not errors, "planned": planned, "waiting": waiting, "errors": errors}


def _promote_group(store: JobStore, group_key: str, jobs: list[Job], now: int) -> dict[str, Any]:
    jobs.sort(key=lambda item: (item.created_at, item.job_id))
    group_size = len(jobs)
    first_created_at = min(job.created_at for job in jobs)
    group_id = _group_id(group_key, first_created_at)
    representative = Job.from_dict(jobs[0].to_dict())
    representative.metadata = dict(representative.metadata)
    representative.metadata["routing"] = _aggregate_routing(jobs)
    route = route_job(representative)
    selected_provider = str(route["selected_provider"])
    group_plan = {
        "group_id": group_id,
        "group_key": group_key,
        "group_size": group_size,
        "planned_at": now,
        "selected_provider": selected_provider,
        "eligible_ranked": route.get("eligible_ranked", []),
        "decision": route.get("decision", {}),
        "observed_burst_size": group_size,
    }
    for job in jobs:
        routing = _as_dict(job.metadata.get("routing"))
        declared_burst = _int_value(routing.get("burst_size"), default=1)
        declared_batch = _int_value(routing.get("batch_size"), default=1)
        routing["observed_burst_size"] = group_size
        routing["effective_burst_size"] = max(declared_burst, group_size)
        routing["burst_size"] = max(declared_burst, group_size)
        routing["batch_size"] = max(declared_batch, group_size)
        job.metadata["routing"] = routing
        intake = _as_dict(job.metadata.get("intake"))
        intake.update(
            {
                "state": "planned",
                "group_id": group_id,
                "group_key": group_key,
                "group_size": group_size,
                "planned_at": now,
                "selected_provider": selected_provider,
            }
        )
        job.metadata["intake"] = intake
        job.metadata["selected_provider"] = selected_provider
        job.provider = selected_provider
        job.status = "queued"
        store.save(job)
    return group_plan


def _aggregate_routing(jobs: list[Job]) -> dict[str, Any]:
    routings = [_as_dict(job.metadata.get("routing")) for job in jobs]
    inputs = [_as_dict(job.metadata.get("input")) for job in jobs]
    group_size = len(jobs)
    tokens = sum(
        _int_value(routing.get("estimated_input_tokens"), input_data.get("estimated_input_tokens"), default=0)
        for routing, input_data in zip(routings, inputs)
    )
    gpu_runtime = max(
        _int_value(routing.get("estimated_gpu_runtime_seconds"), input_data.get("estimated_gpu_runtime_seconds"), default=0)
        for routing, input_data in zip(routings, inputs)
    )
    cpu_runtime = sum(
        _int_value(routing.get("estimated_cpu_runtime_seconds"), input_data.get("estimated_cpu_runtime_seconds"), default=0)
        for routing, input_data in zip(routings, inputs)
    )
    deadlines = [
        value
        for value in (
            _int_value(routing.get("deadline_seconds"), input_data.get("deadline_seconds"), default=0)
            for routing, input_data in zip(routings, inputs)
        )
        if value > 0
    ]
    latency_order = {"interactive": 0, "batch": 1, "bulk": 2}
    latency_class = "batch"
    for routing, input_data in zip(routings, inputs):
        candidate = str(routing.get("latency_class") or input_data.get("latency_class") or "")
        if candidate in latency_order and latency_order[candidate] < latency_order[latency_class]:
            latency_class = candidate
    return {
        "estimated_input_tokens": tokens
        or max(
            (
                _int_value(routing.get("estimated_input_tokens"), input_data.get("estimated_input_tokens"), default=0)
                for routing, input_data in zip(routings, inputs)
            ),
            default=0,
        ),
        "estimated_cpu_runtime_seconds": cpu_runtime or None,
        "estimated_gpu_runtime_seconds": gpu_runtime or None,
        "batch_size": group_size,
        "burst_size": group_size,
        "observed_burst_size": group_size,
        "effective_burst_size": group_size,
        "deadline_seconds": min(deadlines) if deadlines else None,
        "latency_class": latency_class,
        "quality_requires_gpu": any(
            bool(routing.get("quality_requires_gpu") or input_data.get("quality_requires_gpu"))
            for routing, input_data in zip(routings, inputs)
        ),
    }
