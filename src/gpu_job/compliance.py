from __future__ import annotations

from typing import Any

from .models import Job
from .policy import load_execution_policy


COMPLIANCE_VERSION = "gpu-job-compliance-v1"


def evaluate_compliance(job: Job, provider_region: str = "", policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_execution_policy()
    compliance = job.metadata.get("compliance", {})
    if not isinstance(compliance, dict):
        compliance = {}
    data_class = str(compliance.get("data_classification") or "unspecified")
    allowed_regions = [str(item) for item in compliance.get("allowed_regions", [])]
    prohibited_providers = [str(item) for item in compliance.get("prohibited_providers", [])]
    provider = str(job.metadata.get("selected_provider") or job.provider or "")
    if not provider_region and provider:
        provider_region = str(dict(policy.get("provider_regions", {})).get(provider) or "")
    residency_ok = True
    if allowed_regions and provider_region:
        residency_ok = provider_region in allowed_regions
    provider_ok = provider not in prohibited_providers
    strict = data_class not in {"", "unspecified", "public"}
    ok = (
        provider_ok
        and residency_ok
        and (not strict or bool(provider_region and allowed_regions or compliance.get("allow_unspecified_region")))
    )
    return {
        "ok": ok,
        "compliance_version": COMPLIANCE_VERSION,
        "data_classification": data_class,
        "provider": provider,
        "provider_region": provider_region or None,
        "allowed_regions": allowed_regions,
        "prohibited_providers": prohibited_providers,
        "residency_ok": residency_ok,
        "provider_ok": provider_ok,
    }
