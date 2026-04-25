from __future__ import annotations

from typing import Any

from .caller_contract import (
    caller_request_schema,
    compile_caller_request,
    is_caller_request,
    operation_catalog_snapshot,
    prompt_asset_snapshot,
)
from .contracts import artifact_manifest_schema, contract_schemas
from .execution_record import execution_record_schema
from .lanes import get_lane, list_lanes, resolve_lane_id
from .models import Job
from .plan_quote import plan_quote_schema
from .provider_catalog import load_provider_catalog
from .provider_contract_probe import list_contract_probes, provider_contract_probe_schema
from .provider_module_contracts import provider_module_validation, provider_module_contract_schema
from .provider_probe import recent_probe_summary
from .requirements import load_requirement_registry
from .router import route_job
from .runner import submit_job
from .workspace_registry import workspace_registry_schema


def catalog_snapshot() -> dict[str, Any]:
    return {
        "ok": True,
        "providers": load_provider_catalog(),
        "requirements": load_requirement_registry(),
        "probes": recent_probe_summary(),
        "contract_probes": list_contract_probes(),
        "lanes": list_lanes(),
        "operations": operation_catalog_snapshot(),
        "caller_prompt": prompt_asset_snapshot(),
    }


def schema_snapshot() -> dict[str, Any]:
    return {
        "ok": True,
        "artifact_manifest": artifact_manifest_schema(),
        "contracts": contract_schemas(),
        "plan_quote": plan_quote_schema(),
        "execution_record": execution_record_schema(),
        "provider_workspace": workspace_registry_schema(),
        "provider_module": provider_module_contract_schema(),
        "provider_contract_probe": provider_contract_probe_schema(),
        "caller_request": caller_request_schema(),
    }


def build_job(job_data: dict[str, Any]) -> Job:
    if is_caller_request(job_data):
        compiled = compile_caller_request(job_data)
        if not compiled.get("ok"):
            raise ValueError("; ".join(compiled.get("errors") or [str(compiled.get("error") or "caller request compilation failed")]))
        job_data = dict(compiled["job"])
    return Job.from_dict(job_data)


def route_public_job(job_data: dict[str, Any]) -> dict[str, Any]:
    job = build_job(job_data)
    routed = route_job(job)
    lane_id = resolve_lane_id(routed["selected_provider"], job.metadata)
    return {**routed, "selected_lane_id": lane_id}


def validate_public_job(job_data: dict[str, Any], provider: str = "") -> dict[str, Any]:
    try:
        job = build_job(job_data)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "errors": [str(exc)]}
    catalog = load_provider_catalog()
    catalog_providers = catalog.get("providers") if isinstance(catalog, dict) else {}
    providers = [provider] if provider else sorted(str(name) for name in dict(catalog_providers).keys())
    validations = {}
    lane_ids = {item["lane_id"] for item in list_lanes()}
    for provider_name in providers:
        lane_id = resolve_lane_id(provider_name, job.metadata) if provider_name in {"modal", "runpod", "vast"} else ""
        if lane_id and lane_id not in lane_ids:
            lane_id = ""
        metadata = dict(job.metadata)
        if lane_id:
            metadata["provider_module_id"] = lane_id
        validations[provider_name] = {
            "lane_id": lane_id,
            "provider_module_validation": provider_module_validation(metadata, provider_name),
        }
    return {"ok": True, "job": job.to_dict(), "providers": validations}


def plan_public_job(job_data: dict[str, Any], provider: str = "auto") -> dict[str, Any]:
    job = build_job(job_data)
    routed = route_job(job)
    selected_provider = routed["selected_provider"] if provider == "auto" else provider
    lane_id = resolve_lane_id(selected_provider, job.metadata)
    lane = get_lane(lane_id)
    plan = lane.plan(job)
    return {
        "ok": bool(plan.get("ok", True)),
        "selected_provider": selected_provider,
        "selected_lane_id": lane_id,
        "route_result": routed,
        "plan": plan,
    }


def submit_public_job(job_data: dict[str, Any], provider: str = "auto", *, execute: bool = False) -> dict[str, Any]:
    job = build_job(job_data)
    routed = route_job(job)
    selected_provider = routed["selected_provider"] if provider == "auto" else provider
    lane_id = resolve_lane_id(selected_provider, job.metadata)
    result = submit_job(job, provider_name=selected_provider, execute=execute)
    return {**result, "selected_lane_id": lane_id, "route_result": routed}
