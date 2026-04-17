from __future__ import annotations

from typing import Any

from .dlq import dead_letter
from .error_class import classify_error
from .models import Job, now_unix
from .policy import load_execution_policy
from .store import JobStore


REMEDIATION_VERSION = "gpu-job-remediation-v1"


def remediation_decision(job: Job, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_execution_policy()
    retry_policy = dict(policy.get("retry_policy", {}))
    max_attempts = int(retry_policy.get("max_attempts") or 2)
    base_delay = int(retry_policy.get("base_delay_seconds") or 30)
    max_delay = int(retry_policy.get("max_delay_seconds") or 300)
    class_overrides = dict(retry_policy.get("class_overrides", {}))
    error_class = job.metadata.get("error_class")
    if not isinstance(error_class, dict):
        error_class = classify_error(job.error, provider=str(job.metadata.get("selected_provider") or job.provider or ""))
    klass = str(error_class.get("class") or "unknown")
    override = dict(class_overrides.get(klass, {}))
    if "max_attempts" in override:
        max_attempts = int(override["max_attempts"])
    if "delay_seconds" in override:
        base_delay = int(override["delay_seconds"])
    attempts = int(job.metadata.get("retry_attempts") or 0)
    retryable = bool(error_class.get("retryable"))
    permanent = bool(error_class.get("permanent"))
    if permanent or not retryable:
        return {
            "ok": True,
            "remediation_version": REMEDIATION_VERSION,
            "action": "dead_letter",
            "reason": "permanent or non-retryable error",
            "error_class": error_class,
            "retry_attempts": attempts,
            "max_attempts": max_attempts,
        }
    if attempts >= max_attempts:
        return {
            "ok": True,
            "remediation_version": REMEDIATION_VERSION,
            "action": "dead_letter",
            "reason": "retry attempts exhausted",
            "error_class": error_class,
            "retry_attempts": attempts,
            "max_attempts": max_attempts,
        }
    delay = min(max_delay, base_delay * (2**attempts))
    return {
        "ok": True,
        "remediation_version": REMEDIATION_VERSION,
        "action": "retry_later",
        "reason": "retryable error class",
        "error_class": error_class,
        "retry_attempts": attempts,
        "next_retry_attempt": attempts + 1,
        "retry_delay_seconds": delay,
        "retry_after": now_unix() + delay,
        "max_attempts": max_attempts,
    }


def apply_remediation(job: Job, policy: dict[str, Any] | None = None, store: JobStore | None = None) -> dict[str, Any]:
    store = store or JobStore()
    decision = remediation_decision(job, policy)
    action = decision["action"]
    job.metadata["remediation"] = decision
    if action == "retry_later":
        job.status = "queued"
        job.error = ""
        job.exit_code = None
        job.started_at = None
        job.finished_at = None
        job.runtime_seconds = None
        job.metadata["retry_attempts"] = int(decision["next_retry_attempt"])
        job.metadata["retry_after"] = int(decision["retry_after"])
        store.save(job)
        return {"ok": True, "action": action, "job": job.to_dict(), "decision": decision}
    result = dead_letter(
        job,
        reason=decision["reason"],
        error_class=str(decision.get("error_class", {}).get("class") or "unknown"),
        store=store,
    )
    result["decision"] = decision
    return result
