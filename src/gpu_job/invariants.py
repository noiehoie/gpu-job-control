from __future__ import annotations

from typing import Any

from .compliance import evaluate_compliance
from .capabilities import evaluate_model_capability
from .cost import cost_estimate
from .guard import collect_cost_guard
from .models import Job
from .policy_engine import validate_policy
from .placement import placement_check
from .preemption import preemption_check
from .provenance import evaluate_provenance
from .quota import quota_check
from .router import route_job
from .secrets_policy import secret_check
from .timeout import timeout_contract


INVARIANT_VERSION = "gpu-job-invariants-v1"


def evaluate_invariants(job: Job, provider_name: str = "auto") -> dict[str, Any]:
    route = route_job(job) if provider_name == "auto" else {"selected_provider": provider_name, "provider_decisions": {}}
    selected = str(route["selected_provider"])
    job.metadata["selected_provider"] = selected
    guard = collect_cost_guard([selected])
    policy = validate_policy()
    provenance = evaluate_provenance(job)
    compliance = evaluate_compliance(job)
    capability = evaluate_model_capability(job, selected)
    quota = quota_check(job)
    cost = cost_estimate(job)
    secrets = secret_check(job, selected)
    placement = placement_check(job, selected)
    preemption = preemption_check(job)
    timeout = timeout_contract(job)
    provider_decision = {}
    if isinstance(route.get("provider_decisions"), dict):
        provider_decision = dict(route["provider_decisions"].get(selected) or {})
    workload_policy = provider_decision.get("workload_policy") if isinstance(provider_decision.get("workload_policy"), dict) else {}
    startup_policy = provider_decision.get("startup_policy") if isinstance(provider_decision.get("startup_policy"), dict) else {}
    capability_policy = provider_decision.get("capability_policy") if isinstance(provider_decision.get("capability_policy"), dict) else {}
    invariants = {
        "billing_ok": bool(guard.get("ok")),
        "resource_ok": bool(guard.get("resource", {}).get("ok", guard.get("ok")))
        if isinstance(guard.get("resource", {}), dict)
        else bool(guard.get("ok")),
        "quality_ok": bool(capability_policy.get("ok", True)) and bool(workload_policy.get("ok", True)) and bool(capability.get("ok")),
        "wait_ok": bool(startup_policy.get("ok", True)) and bool(workload_policy.get("ok", True)),
        "policy_ok": bool(policy.get("ok")),
        "provenance_ok": bool(provenance.get("ok")),
        "compliance_ok": bool(compliance.get("ok")),
        "timeout_ok": bool(timeout.get("ok")),
        "quota_ok": bool(quota.get("ok")),
        "cost_ok": bool(cost.get("ok")),
        "secret_ok": bool(secrets.get("ok")),
        "placement_ok": bool(placement.get("ok")),
        "preemption_ok": bool(preemption.get("ok")),
    }
    return {
        "ok": all(invariants.values()),
        "invariant_version": INVARIANT_VERSION,
        "job_id": job.job_id,
        "selected_provider": selected,
        "invariants": invariants,
        "route": route,
        "guard": _compact_guard(guard),
        "policy": policy,
        "provenance": provenance,
        "compliance": compliance,
        "timeout": timeout,
        "capability": capability,
        "quota": quota,
        "cost": cost,
        "secrets": secrets,
        "placement": placement,
        "preemption": preemption,
    }


def _compact_guard(guard: dict[str, Any]) -> dict[str, Any]:
    out = dict(guard)
    providers = out.get("providers")
    if isinstance(providers, dict):
        out["providers"] = {
            name: {
                "ok": item.get("ok"),
                "reason": item.get("reason"),
                "billable_count": len(item.get("billable_resources") or []),
                "estimated_hourly_usd": item.get("estimated_hourly_usd"),
            }
            for name, item in providers.items()
            if isinstance(item, dict)
        }
    return out
