from __future__ import annotations

from typing import Any

from .models import Job, now_unix
from .policy import load_execution_policy
from .store import JobStore


CIRCUIT_VERSION = "gpu-job-circuit-v1"


def _provider_for(job: Job) -> str:
    return str(job.metadata.get("selected_provider") or job.provider or job.metadata.get("requested_provider") or "unknown")


def provider_circuit_state(provider: str, store: JobStore | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    store = store or JobStore()
    policy = policy or load_execution_policy()
    circuit_policy = dict(policy.get("circuit_breaker", {}))
    window_seconds = int(circuit_policy.get("window_seconds") or 900)
    min_samples = int(circuit_policy.get("min_samples") or 5)
    open_failure_rate = float(circuit_policy.get("open_failure_rate") or 0.8)
    now = now_unix()
    samples = []
    for job in store.list_jobs(limit=5000):
        if _provider_for(job) != provider:
            continue
        if now - int(job.updated_at or job.created_at) > window_seconds:
            continue
        if job.status in {"succeeded", "failed"}:
            samples.append(job)
    failures = [job for job in samples if job.status == "failed"]
    error_classes: dict[str, int] = {}
    retryable_failures = 0
    rate_limit_failures = 0
    for job in failures:
        error_class = job.metadata.get("error_class")
        klass = "unknown"
        retryable = False
        if isinstance(error_class, dict):
            klass = str(error_class.get("class") or "unknown")
            retryable = bool(error_class.get("retryable"))
        error_classes[klass] = error_classes.get(klass, 0) + 1
        if retryable:
            retryable_failures += 1
        if klass in {"backpressure", "provider_rate_limit"}:
            rate_limit_failures += 1
    failure_rate = len(failures) / len(samples) if samples else 0.0
    state = "closed"
    if len(samples) >= min_samples and failure_rate >= open_failure_rate:
        state = "open"
    elif rate_limit_failures:
        state = "degraded"
    latest_sample = max(samples, key=lambda item: int(item.updated_at or item.created_at), default=None)
    latest_failure = max(failures, key=lambda item: int(item.updated_at or item.created_at), default=None)
    latest_success_after_failure = (
        latest_sample is not None
        and latest_failure is not None
        and latest_sample.status == "succeeded"
        and int(latest_sample.updated_at or latest_sample.created_at) >= int(latest_failure.updated_at or latest_failure.created_at)
    )
    if state == "open" and latest_success_after_failure:
        state = "closed"
    return {
        "ok": state not in {"open"},
        "circuit_version": CIRCUIT_VERSION,
        "provider": provider,
        "state": state,
        "sample_count": len(samples),
        "failure_count": len(failures),
        "retryable_failure_count": retryable_failures,
        "rate_limit_failure_count": rate_limit_failures,
        "error_classes": error_classes,
        "failure_rate": round(failure_rate, 3),
        "window_seconds": window_seconds,
        "half_open_probe_allowed": state == "open" and len(samples) >= min_samples,
        "latest_sample_status": latest_sample.status if latest_sample else None,
        "latest_success_after_failure": latest_success_after_failure,
    }


def all_circuits(store: JobStore | None = None) -> dict[str, Any]:
    providers = {"local", "modal", "ollama", "runpod", "vast"}
    states = {provider: provider_circuit_state(provider, store=store) for provider in sorted(providers)}
    return {"ok": all(item["ok"] for item in states.values()), "providers": states}
