from __future__ import annotations

from typing import Any


ERROR_CLASS_VERSION = "gpu-job-error-class-v1"


RETRYABLE_CLASSES = {
    "backpressure",
    "provider_timeout",
    "provider_rate_limit",
    "provider_transient",
    "network_transient",
}

PERMANENT_CLASSES = {
    "policy_block",
    "compliance_block",
    "provenance_block",
    "validation_error",
    "unsupported_job_type",
    "capability_block",
    "artifact_integrity_failed",
    "approval_required",
    "quota_block",
    "cost_block",
    "secret_block",
    "placement_block",
    "preemption_block",
}


def classify_error(
    error: str = "",
    *,
    status_code: int | None = None,
    provider: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = str(error or "").lower()
    context = context or {}
    error_class = "unknown"
    retryable = False
    reason = "unclassified error"

    if status_code == 429 or "backpressure" in text or "concurrency limit" in text:
        error_class = "backpressure"
        retryable = True
        reason = "capacity or concurrency pressure"
    elif "timed out" in text or "timeout" in text or "runtime_seconds=601" in text:
        error_class = "provider_timeout"
        retryable = True
        reason = "provider execution timed out"
    elif "rate limit" in text or status_code == rate_limit_status_code():
        error_class = "provider_rate_limit"
        retryable = True
        reason = "provider rate limit"
    elif "policy validation failed" in text or "policy" in context.get("gate", ""):
        error_class = "policy_block"
        retryable = False
        reason = "policy hard gate blocked execution"
    elif "compliance" in text:
        error_class = "compliance_block"
        retryable = False
        reason = "compliance hard gate blocked execution"
    elif "provenance" in text:
        error_class = "provenance_block"
        retryable = False
        reason = "provenance hard gate blocked execution"
    elif "capability" in text:
        error_class = "capability_block"
        retryable = False
        reason = "model or worker capability hard gate blocked execution"
    elif "quota" in text:
        error_class = "quota_block"
        retryable = False
        reason = "quota hard gate blocked execution"
    elif "cost" in text:
        error_class = "cost_block"
        retryable = False
        reason = "cost hard gate blocked execution"
    elif "secret" in text:
        error_class = "secret_block"
        retryable = False
        reason = "secret policy hard gate blocked execution"
    elif "placement" in text:
        error_class = "placement_block"
        retryable = False
        reason = "placement hard gate blocked execution"
    elif "preemption" in text:
        error_class = "preemption_block"
        retryable = False
        reason = "preemption/checkpoint hard gate blocked execution"
    elif "approval" in text:
        error_class = "approval_required"
        retryable = False
        reason = "operator approval is required"
    elif "unsupported" in text or "supports" in text and "only" in text:
        error_class = "unsupported_job_type"
        retryable = False
        reason = "provider does not support this job contract"
    elif "not valid json" in text or "missing required" in text or "artifact" in text and "missing" in text:
        error_class = "artifact_integrity_failed"
        retryable = False
        reason = "artifact contract or integrity verification failed"
    elif status_code and 500 <= status_code < 600:
        error_class = "provider_transient"
        retryable = True
        reason = "provider server-side failure"
    elif status_code and 400 <= status_code < 500:
        error_class = "validation_error"
        retryable = False
        reason = "client-side validation or request error"

    return {
        "ok": True,
        "error_class_version": ERROR_CLASS_VERSION,
        "class": error_class,
        "retryable": retryable,
        "permanent": error_class in PERMANENT_CLASSES,
        "provider": provider,
        "status_code": status_code,
        "reason": reason,
    }


def rate_limit_status_code() -> int:
    return 429
