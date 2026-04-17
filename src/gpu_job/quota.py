from __future__ import annotations

from typing import Any

from .models import Job
from .policy import load_execution_policy
from .store import JobStore


QUOTA_VERSION = "gpu-job-quota-v1"


def estimate_job_cost(job: Job) -> float:
    cost = job.metadata.get("estimated_cost_usd")
    if cost is not None:
        return float(cost)
    routing = job.metadata.get("routing")
    if isinstance(routing, dict) and routing.get("estimated_cost_usd") is not None:
        return float(routing["estimated_cost_usd"])
    return 0.0


def source_system(job: Job) -> str:
    return str(job.metadata.get("source_system") or "default")


def committed_costs(store: JobStore | None = None) -> dict[str, float]:
    store = store or JobStore()
    costs: dict[str, float] = {}
    for item in store.list_jobs(limit=5000):
        if item.status in {"cancelled"}:
            continue
        source = source_system(item)
        costs[source] = costs.get(source, 0.0) + estimate_job_cost(item)
    return costs


def quota_check(job: Job, store: JobStore | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_execution_policy()
    quota_policy = dict(policy.get("quota", {}))
    default_budget = float(quota_policy.get("default_source_budget_usd") or 0)
    global_budget = float(quota_policy.get("global_budget_usd") or 0)
    source_budgets = {str(k): float(v) for k, v in dict(quota_policy.get("source_budgets_usd", {})).items()}
    source = source_system(job)
    estimate = estimate_job_cost(job)
    used = committed_costs(store)
    source_used = used.get(source, 0.0)
    global_used = sum(used.values())
    source_budget = source_budgets.get(source, default_budget)
    source_ok = not source_budget or source_used + estimate <= source_budget
    global_ok = not global_budget or global_used + estimate <= global_budget
    return {
        "ok": source_ok and global_ok,
        "quota_version": QUOTA_VERSION,
        "source_system": source,
        "estimated_cost_usd": round(estimate, 6),
        "source_used_usd": round(source_used, 6),
        "source_budget_usd": source_budget,
        "source_remaining_usd": round(source_budget - source_used, 6) if source_budget else None,
        "global_used_usd": round(global_used, 6),
        "global_budget_usd": global_budget,
        "global_remaining_usd": round(global_budget - global_used, 6) if global_budget else None,
        "source_ok": source_ok,
        "global_ok": global_ok,
    }
