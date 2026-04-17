from __future__ import annotations

from typing import Any

from .models import Job
from .policy import load_execution_policy


SECRET_POLICY_VERSION = "gpu-job-secret-policy-v1"


def secret_check(job: Job, provider: str = "", policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_execution_policy()
    provider = provider or str(job.metadata.get("selected_provider") or job.provider or "")
    source = str(job.metadata.get("source_system") or "default")
    requested = [str(item) for item in job.metadata.get("secret_refs", [])] if isinstance(job.metadata.get("secret_refs"), list) else []
    secret_policy = dict(policy.get("secret_policy", {}))
    scopes = dict(secret_policy.get("allowed_refs", {}))
    scope_key = f"{provider}:{source}:{job.job_type}"
    allowed = set(str(item) for item in scopes.get(scope_key, []))
    allowed.update(str(item) for item in scopes.get(f"{provider}:*:{job.job_type}", []))
    allowed.update(str(item) for item in scopes.get("*:*:*", []))
    denied = [item for item in requested if item not in allowed]
    return {
        "ok": not denied,
        "secret_policy_version": SECRET_POLICY_VERSION,
        "scope": scope_key,
        "requested_secret_refs": requested,
        "allowed_secret_refs": sorted(allowed),
        "denied_secret_refs": denied,
    }
