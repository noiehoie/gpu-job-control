from __future__ import annotations

from typing import Any

from .models import Job
from .policy import load_execution_policy


TIMEOUT_VERSION = "gpu-job-timeout-v1"


def timeout_contract(job: Job, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_execution_policy()
    explicit = job.limits.get("max_runtime_seconds")
    if explicit is None and job.limits.get("max_runtime_minutes") is not None:
        explicit = float(job.limits["max_runtime_minutes"]) * 60
    defaults = dict(policy.get("job_type_timeouts_seconds", {}))
    profile_defaults = dict(policy.get("gpu_profile_timeouts_seconds", {}))
    value = explicit
    source = "job.limits"
    if value is None:
        value = defaults.get(job.job_type)
        source = "policy.job_type_timeouts_seconds"
    if value is None:
        value = profile_defaults.get(job.gpu_profile)
        source = "policy.gpu_profile_timeouts_seconds"
    if value is None:
        value = int(policy.get("default_job_timeout_seconds") or 14400)
        source = "policy.default_job_timeout_seconds"
    max_runtime_seconds = max(1, int(float(value)))
    return {
        "ok": True,
        "timeout_version": TIMEOUT_VERSION,
        "max_runtime_seconds": max_runtime_seconds,
        "source": source,
    }


def runtime_within_timeout(job: Job, contract: dict[str, Any]) -> dict[str, Any]:
    runtime = int(job.runtime_seconds or 0)
    limit = int(contract.get("max_runtime_seconds") or 0)
    ok = limit <= 0 or runtime <= limit
    return {
        "ok": ok,
        "timeout_version": TIMEOUT_VERSION,
        "runtime_seconds": runtime,
        "max_runtime_seconds": limit,
        "error": "" if ok else "job runtime exceeded timeout contract",
    }
