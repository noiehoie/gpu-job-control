from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json

from .execution_plan import build_execution_plan
from .models import Job, now_unix
from .plan_quote import build_plan_quote
from .store import JobStore
from .timing import public_timing


EXECUTION_RECORD_VERSION = "gpu-job-execution-record-v1"


def build_execution_record(job: Job, *, store: JobStore | None = None) -> dict[str, Any]:
    store = store or JobStore()
    artifact_dir = store.artifact_dir(job.job_id)
    provider = str(job.metadata.get("selected_provider") or job.provider or "")
    provider_plan = job.metadata.get("provider_plan") if isinstance(job.metadata.get("provider_plan"), dict) else {}
    execution_plan = provider_plan.get("execution_plan") if isinstance(provider_plan.get("execution_plan"), dict) else None
    if execution_plan is None and provider:
        execution_plan = build_execution_plan(job, provider)
    record = {
        "execution_record_version": EXECUTION_RECORD_VERSION,
        "record_id": "",
        "record_hash": "",
        "recorded_at": now_unix(),
        "job": _public_job(job),
        "provider": provider,
        "provider_job_id": job.provider_job_id,
        "plan_quote": _plan_quote_from_job(job),
        "workspace_plan": job.metadata.get("workspace_plan") if isinstance(job.metadata.get("workspace_plan"), dict) else {},
        "execution_plan": execution_plan or {},
        "timing_v2": public_timing(job),
        "artifact_manifest": _load_json(artifact_dir / "manifest.json"),
        "final_artifact_verify": job.metadata.get("final_artifact_verify")
        if isinstance(job.metadata.get("final_artifact_verify"), dict)
        else {},
        "cost": _cost_payload(job),
        "terminal": {
            "status": job.status,
            "exit_code": job.exit_code,
            "runtime_seconds": job.runtime_seconds,
            "artifact_count": job.artifact_count,
            "artifact_bytes": job.artifact_bytes,
            "error_class": job.metadata.get("error_class") or "",
            "has_error": bool(job.error),
        },
    }
    digest = execution_record_hash(record)
    record["record_id"] = f"exec-{digest[:16]}"
    record["record_hash"] = digest
    return record


def write_execution_record(job: Job, *, store: JobStore | None = None) -> dict[str, Any]:
    store = store or JobStore()
    record = build_execution_record(job, store=store)
    artifact_dir = store.artifact_dir(job.job_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / "execution_record.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    job.metadata["execution_record"] = {
        "record_id": record["record_id"],
        "record_hash": record["record_hash"],
        "path": str(path),
    }
    store.save(job)
    return record


def execution_record_schema() -> dict[str, Any]:
    return {
        "execution_record_version": EXECUTION_RECORD_VERSION,
        "required_fields": [
            "record_id",
            "record_hash",
            "job",
            "provider",
            "plan_quote",
            "workspace_plan",
            "execution_plan",
            "timing_v2",
            "artifact_manifest",
            "final_artifact_verify",
            "cost",
            "terminal",
        ],
        "invariants": [
            "record_hash excludes recorded_at, record_id, and record_hash",
            "raw secrets are not included",
            "terminal.has_error records only presence of error text, not the raw error",
        ],
    }


def execution_record_hash(record: dict[str, Any]) -> str:
    stable = dict(record)
    stable.pop("recorded_at", None)
    stable.pop("record_id", None)
    stable.pop("record_hash", None)
    blob = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def _public_job(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "gpu_profile": job.gpu_profile,
        "model": job.model,
        "provider": job.provider,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def _plan_quote_from_job(job: Job) -> dict[str, Any]:
    workflow_quote = job.metadata.get("workflow_plan_quote") if isinstance(job.metadata.get("workflow_plan_quote"), dict) else {}
    child_quote = job.metadata.get("plan_quote") if isinstance(job.metadata.get("plan_quote"), dict) else {}
    if workflow_quote and job.job_type != "cpu_workflow_helper":
        return workflow_quote
    if child_quote or workflow_quote:
        return child_quote or workflow_quote
    workspace_plan = job.metadata.get("workspace_plan") if isinstance(job.metadata.get("workspace_plan"), dict) else {}
    if not workspace_plan:
        return {}
    provider = str(workspace_plan.get("provider") or job.provider or "")
    capability = workspace_plan.get("provider_capability") if isinstance(workspace_plan.get("provider_capability"), dict) else {}
    runtime = workspace_plan.get("provider_runtime") if isinstance(workspace_plan.get("provider_runtime"), dict) else {}
    required_actions = workspace_plan.get("required_actions") if isinstance(workspace_plan.get("required_actions"), list) else []
    decision = "requires_action" if workspace_plan.get("decision") == "requires_action" else "auto_execute"
    selected = {
        "provider": provider,
        "gpu_profile": job.gpu_profile,
        "workspace_plan_id": workspace_plan.get("workspace_plan_id") or "",
        "workspace_registry_version": workspace_plan.get("workspace_registry_version") or "",
        "catalog_capability": capability,
        "provider_runtime": runtime,
        "estimated_total_seconds_p50": capability.get("estimated_startup_seconds"),
        "estimated_total_seconds_p95": capability.get("estimated_startup_seconds"),
        "estimated_total_cost_usd_p50": None,
        "estimated_total_cost_usd_p95": None,
    }
    return build_plan_quote(
        {
            "contract_version": "gpu-job-execution-record-derived-quote-v1",
            "request": {
                "job_id": job.job_id,
                "job_type": job.job_type,
                "gpu_profile": job.gpu_profile,
                "model": job.model,
            },
            "catalog_version": workspace_plan.get("catalog_version") or "",
            "catalog_snapshot_id": workspace_plan.get("catalog_snapshot_id") or "",
            "gpu_profile": job.gpu_profile,
            "selected_option": selected,
            "options": [selected],
            "refusals": [],
            "estimate": {
                "source": "workspace_plan",
                "workspace_plan_id": workspace_plan.get("workspace_plan_id") or "",
            },
            "approval": {
                "decision": decision,
                "reason": "derived from provider workspace plan at execution-record time",
            },
            "can_run_now": decision != "requires_action",
            "action_requirements": {
                "decision": workspace_plan.get("decision") or "",
                "required_actions": required_actions,
            },
            "created_at": job.created_at,
        }
    )


def _cost_payload(job: Job) -> dict[str, Any]:
    for key in ("vast_runtime_cost", "runpod_runtime_cost", "modal_runtime_cost"):
        value = job.metadata.get(key)
        if isinstance(value, dict):
            return {"source": key, **value}
    return {}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
