from __future__ import annotations

from typing import Any

from .models import Job
from .policy import load_execution_policy


COST_VERSION = "gpu-job-cost-v1"


def cost_estimate(job: Job, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_execution_policy()
    pricing = dict(policy.get("cost_model", {}))
    egress_per_gb = float(pricing.get("egress_usd_per_gb") or 0)
    storage_per_gb_month = float(pricing.get("storage_usd_per_gb_month") or 0)
    routing = job.metadata.get("routing")
    routing = routing if isinstance(routing, dict) else {}
    artifact_gb = float(routing.get("estimated_artifact_gb") or job.metadata.get("estimated_artifact_gb") or 0)
    cross_region = bool(routing.get("cross_region_transfer") or job.metadata.get("cross_region_transfer"))
    runtime_cost = float(
        routing.get("estimated_runtime_cost_usd")
        or job.metadata.get("estimated_runtime_cost_usd")
        or job.metadata.get("estimated_cost_usd")
        or 0
    )
    egress_cost = artifact_gb * egress_per_gb if cross_region else 0.0
    storage_cost = artifact_gb * storage_per_gb_month
    total = runtime_cost + egress_cost + storage_cost
    budget = float(routing.get("max_total_cost_usd") or job.limits.get("max_cost_usd") or 0)
    return {
        "ok": not budget or total <= budget,
        "cost_version": COST_VERSION,
        "estimated_runtime_cost_usd": round(runtime_cost, 6),
        "estimated_egress_cost_usd": round(egress_cost, 6),
        "estimated_storage_month_cost_usd": round(storage_cost, 6),
        "estimated_total_cost_usd": round(total, 6),
        "budget_usd": budget or None,
        "cross_region_transfer": cross_region,
        "estimated_artifact_gb": artifact_gb,
    }
