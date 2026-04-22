from __future__ import annotations

from typing import Any


ERROR_CLASS_VERSION = "gpu-job-error-class-v1"
PROVIDER_NATIVE_RULES: dict[str, list[tuple[str, str, bool, str]]] = {
    "modal": [
        (
            "qwen/qwen2.5-0.5b-instruct",
            "model_contract_mismatch",
            False,
            "Modal returned a forbidden small model for a heavy-model contract",
        ),
        ("repositorynotfounderror", "model_unavailable", False, "Modal worker could not resolve requested model"),
        (
            "loading an awq quantized model requires gptqmodel",
            "image_missing_dependency",
            False,
            "Modal image missing quantization dependency",
        ),
        ("torchcodec is required", "image_missing_dependency", False, "Modal ASR image missing torchcodec dependency"),
        ("libavutil.so", "image_missing_dependency", False, "Modal ASR image missing FFmpeg shared libraries required by torchcodec"),
        ("torchaudio' has no attribute 'info", "image_missing_dependency", False, "Modal ASR image missing torchaudio compatibility shim"),
        ("no module named 'matplotlib'", "image_missing_dependency", False, "ASR diarization image missing pyannote lazy dependency"),
        ("fetching", "cold_start_timeout", True, "Modal model cache miss or cold-start download observed"),
        ("prompt exceeds model context", "context_overflow", False, "Prompt exceeds model context limit"),
        ("concurrency", "backpressure", True, "Modal concurrency pressure"),
    ],
    "runpod": [
        ("in_queue", "provider_backpressure", True, "RunPod endpoint queue backpressure"),
        ("no worker", "provider_backpressure", True, "RunPod has no active worker"),
        ("endpoint not found", "endpoint_unreachable", True, "RunPod endpoint is not reachable"),
        ("502", "provider_transient", True, "RunPod gateway transient failure"),
        ("504", "provider_transient", True, "RunPod gateway timeout"),
        ("pod not found", "provider_transient", True, "RunPod pod unavailable"),
    ],
    "vast": [
        ("no offers", "provider_backpressure", True, "Vast has no eligible offers"),
        ("unauthorized", "endpoint_unreachable", False, "Vast endpoint authorization failed"),
        ("endpoint not found", "endpoint_unreachable", True, "Vast endpoint is not reachable"),
        ("requires registry credentials", "secret_block", False, "Vast private image registry credentials are missing"),
        ("requires hf_token", "secret_block", False, "Vast speaker diarization Hugging Face token is missing"),
        ("no module named 'matplotlib'", "image_missing_dependency", False, "ASR diarization image missing pyannote lazy dependency"),
        ("ssh", "provider_transient", True, "Vast SSH/bootstrap transient failure"),
    ],
}


RETRYABLE_CLASSES = {
    "backpressure",
    "provider_timeout",
    "provider_rate_limit",
    "provider_transient",
    "network_transient",
    "cold_start_timeout",
    "cache_contract_missing",
    "endpoint_unreachable",
}

PERMANENT_CLASSES = {
    "policy_block",
    "compliance_block",
    "provenance_block",
    "validation_error",
    "unsupported_job_type",
    "capability_block",
    "artifact_integrity_failed",
    "artifact_contract_failure",
    "approval_required",
    "quota_block",
    "cost_block",
    "secret_block",
    "placement_block",
    "preemption_block",
    "model_contract_mismatch",
    "image_contract_mismatch",
    "gpu_contract_mismatch",
    "image_missing_dependency",
    "model_unavailable",
    "context_overflow",
    "empty_output_success",
    "verification_failed",
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

    native = _provider_native_classification(text, provider)
    if native is not None:
        error_class = native["class"]
        retryable = native["retryable"]
        reason = native["reason"]
    elif status_code == 429 or "backpressure" in text or "concurrency limit" in text:
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


def _provider_native_classification(text: str, provider: str) -> dict[str, Any] | None:
    for needle, klass, retryable, reason in PROVIDER_NATIVE_RULES.get(str(provider or "").lower(), []):
        if needle in text:
            return {"class": klass, "retryable": retryable, "reason": reason}
    return None
