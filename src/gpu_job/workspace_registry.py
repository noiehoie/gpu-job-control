from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json

from .execution_plan import build_execution_plan
from .models import Job, app_data_dir, now_unix
from .provider_catalog import load_provider_catalog, provider_capability
from .provider_contract_probe import recent_contract_probe_summary
from .requirements import load_requirement_registry


WORKSPACE_REGISTRY_VERSION = "gpu-job-provider-workspace-registry-v1"

PROVIDER_WORKSPACES: dict[str, dict[str, Any]] = {
    "modal": {
        "workspace_kind": "function",
        "workspace_root": "/workspace",
        "artifact_dir": "/workspace/artifacts",
        "input_staging": "provider_native",
        "cleanup_contract": "modal_function_lifecycle",
        "official_docs": [
            "https://modal.com/docs/guide/images",
            "https://modal.com/docs/guide/volumes",
            "https://modal.com/docs/guide/timeouts",
            "https://modal.com/docs/guide/retries",
        ],
        "workspace_modes": {
            "function": {
                "resource_model": "modal_app_function",
                "volume_mount_default": "/mnt",
                "consistency": "volume changes require commit/reload semantics for cross-container visibility",
                "timing_fields": ["startup_timeout_seconds", "execution_timeout_seconds", "retry_attempts"],
            }
        },
    },
    "runpod": {
        "workspace_kind": "pod_or_serverless_endpoint",
        "workspace_root": "/workspace/gpu-job",
        "artifact_dir": "/workspace/gpu-job/out",
        "input_staging": "network_volume_or_http_payload",
        "cleanup_contract": "terminate_pod_or_scale_to_zero_endpoint",
        "official_docs": [
            "https://docs.runpod.io/serverless/overview",
            "https://docs.runpod.io/serverless/endpoints/endpoint-configurations",
            "https://docs.runpod.io/storage/network-volumes",
        ],
        "workspace_modes": {
            "serverless": {
                "resource_model": "endpoint_worker",
                "network_volume_mount": "/runpod-volume",
                "status_model": "queued_request_then_worker_status",
                "timing_fields": ["queue_seconds", "cold_start_seconds", "execution_timeout_seconds", "job_ttl_seconds"],
                "cost_fields": ["compute_seconds", "active_worker_idle_seconds", "network_volume_gb_month"],
            },
            "pod": {
                "resource_model": "pod",
                "network_volume_mount": "/workspace",
                "volume_attach_rule": (
                    "network volume must be selected at pod deployment and cannot be attached later without deleting the pod"
                ),
                "timing_fields": ["pod_create_seconds", "image_pull_seconds", "worker_ready_seconds", "execution_seconds"],
            },
        },
    },
    "vast": {
        "workspace_kind": "direct_instance_or_serverless",
        "workspace_root": "/workspace/gpu-job-asr",
        "artifact_dir": "/workspace/gpu-job-asr/out",
        "input_staging": "scp_or_provider_payload",
        "cleanup_contract": "destroy_instance_or_serverless_scale_to_zero",
        "official_docs": [
            "https://docs.vast.ai/documentation/instances/docker-environment",
        ],
        "workspace_modes": {
            "direct_instance": {
                "resource_model": "market_instance",
                "launch_modes": ["entrypoint", "ssh", "jupyter"],
                "entrypoint_warning": "ssh and jupyter launch modes inject setup scripts and replace the original image entrypoint",
                "disk_rule": "disk allocation is static at creation time",
                "timing_fields": [
                    "offer_select_seconds",
                    "reserve_seconds",
                    "image_materialization_seconds",
                    "ssh_ready_seconds",
                    "execution_seconds",
                    "cleanup_seconds",
                ],
                "residue_check": "post-cleanup provider read must show no active matching instance",
            }
        },
    },
    "local": {
        "workspace_kind": "local_process",
        "workspace_root": "<local-artifact-dir>",
        "artifact_dir": "<local-artifact-dir>",
        "input_staging": "local_path",
        "cleanup_contract": "process_exit",
    },
    "ollama": {
        "workspace_kind": "resident_local_service",
        "workspace_root": "<local-artifact-dir>",
        "artifact_dir": "<local-artifact-dir>",
        "input_staging": "local_payload",
        "cleanup_contract": "no_cloud_resource",
    },
}


def provider_workspace_plan(job: Job, provider: str) -> dict[str, Any]:
    execution_plan = build_execution_plan(job, provider)
    base = dict(PROVIDER_WORKSPACES.get(provider) or PROVIDER_WORKSPACES["local"])
    image_contract = dict(execution_plan.get("image_contract") or {})
    required_backends = list(execution_plan.get("required_backends") or [])
    try:
        catalog = load_provider_catalog()
    except Exception:
        catalog = {}
    capability = provider_capability(provider, catalog if catalog else None)
    runtime = _provider_runtime(provider, job.gpu_profile)
    contract_probe = str(runtime.get("contract_probe") or "")
    is_contract_probe_job = _is_matching_contract_probe_job(job, contract_probe)
    runtime_probe_ok = True if is_contract_probe_job else _runtime_contract_probe_ok(contract_probe) if contract_probe else True
    action_required = bool(required_backends) and (not bool(image_contract.get("ok")) or not runtime_probe_ok)
    required_actions = []
    if bool(required_backends) and not bool(image_contract.get("ok")):
        required_actions.append(
            {
                "type": "build_image",
                "contract_id": image_contract.get("contract_id") or "",
                "status": image_contract.get("status") or "missing_image_contract",
                "reason": image_contract.get("reason") or "required backend is not backed by a verified image contract",
            }
        )
    if contract_probe and not runtime_probe_ok:
        required_actions.append(
            {
                "type": "run_contract_probe",
                "contract_probe": contract_probe,
                "status": "unverified",
                "reason": "provider runtime contract probe has not passed for this workspace",
            }
        )
    plan = {
        "workspace_registry_version": WORKSPACE_REGISTRY_VERSION,
        "workspace_plan_id": "",
        "provider": provider,
        "job_id": job.job_id,
        "job_type": job.job_type,
        "gpu_profile": job.gpu_profile,
        "catalog_version": catalog.get("catalog_version") if isinstance(catalog, dict) else "",
        "catalog_snapshot_id": catalog.get("catalog_snapshot_id") if isinstance(catalog, dict) else "",
        "workspace": base,
        "execution_plan": execution_plan,
        "image_contract": image_contract,
        "required_backends": required_backends,
        "provider_capability": {
            "provider": capability.get("provider"),
            "supported_job_types": capability.get("supported_job_types"),
            "estimated_startup_seconds": capability.get("estimated_startup_seconds"),
            "cache_state": capability.get("cache_state"),
            "warm_state": capability.get("warm_state"),
        },
        "provider_runtime": runtime,
        "runtime_contract_probe": {
            "contract_probe": contract_probe,
            "ok": runtime_probe_ok,
            "required": bool(contract_probe),
            "self_probe": is_contract_probe_job,
        },
        "decision": "requires_action" if action_required else "ready",
        "required_actions": required_actions,
        "created_at": now_unix(),
    }
    plan["workspace_plan_id"] = f"workspace-{_workspace_hash(plan)[:16]}"
    return plan


def workspace_registry_schema() -> dict[str, Any]:
    return {
        "workspace_registry_version": WORKSPACE_REGISTRY_VERSION,
        "providers": sorted(PROVIDER_WORKSPACES),
        "required_fields": [
            "workspace_plan_id",
            "provider",
            "job_id",
            "job_type",
            "gpu_profile",
            "workspace",
            "execution_plan",
            "image_contract",
            "decision",
            "required_actions",
        ],
        "decisions": ["ready", "requires_action"],
        "required_action_types": ["build_image", "run_contract_probe", "provide_secret", "register_backend"],
        "terminal_rule": (
            "provider adapters receive a workspace_plan; production execution must not allocate cloud GPU when decision=requires_action"
        ),
    }


def workspace_records_dir() -> Path:
    path = app_data_dir() / "workspaces"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _provider_runtime(provider: str, gpu_profile: str) -> dict[str, Any]:
    try:
        runtimes = dict(load_requirement_registry().get("provider_runtimes") or {})
    except Exception:
        return {}
    runtime = runtimes.get(f"{provider}:{gpu_profile}")
    return dict(runtime) if isinstance(runtime, dict) else {}


def _runtime_contract_probe_ok(contract_probe: str) -> bool:
    try:
        latest = dict(recent_contract_probe_summary().get("latest") or {})
    except Exception:
        return False
    row = latest.get(contract_probe)
    return isinstance(row, dict) and bool(row.get("ok")) and str(row.get("verdict") or "pass") == "pass"


def _is_matching_contract_probe_job(job: Job, contract_probe: str) -> bool:
    if not contract_probe:
        return False
    probe = job.metadata.get("contract_probe") if isinstance(job.metadata.get("contract_probe"), dict) else {}
    return str(probe.get("probe_name") or "") == contract_probe


def workspace_record_path(workspace_plan_id: str) -> Path:
    return workspace_records_dir() / f"{workspace_plan_id}.json"


def record_workspace_state(job: Job, workspace_plan: dict[str, Any], *, state: str, status: str = "ok") -> dict[str, Any]:
    record = {
        "workspace_registry_version": WORKSPACE_REGISTRY_VERSION,
        "workspace_plan_id": workspace_plan.get("workspace_plan_id") or "",
        "job_id": job.job_id,
        "provider": workspace_plan.get("provider") or job.provider,
        "gpu_profile": job.gpu_profile,
        "state": state,
        "status": status,
        "workspace": workspace_plan.get("workspace") or {},
        "image_contract": workspace_plan.get("image_contract") or {},
        "required_actions": workspace_plan.get("required_actions") or [],
        "provider_job_id": job.provider_job_id,
        "updated_at": now_unix(),
    }
    path = workspace_record_path(str(record["workspace_plan_id"] or f"job-{job.job_id}"))
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {**record, "path": str(path)}


def _workspace_hash(plan: dict[str, Any]) -> str:
    stable = dict(plan)
    stable.pop("created_at", None)
    stable.pop("workspace_plan_id", None)
    blob = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()
