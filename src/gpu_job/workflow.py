from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import uuid

from .cost import cost_estimate
from .models import Job, app_data_dir, now_unix
from .policy import load_execution_policy
from .plan_quote import build_plan_quote
from .requirements import evaluate_workflow_requirements
from .stats import collect_stats
from .store import JobStore
from .workspace_registry import provider_workspace_plan


WORKFLOW_VERSION = "gpu-job-workflow-v2"
WORKFLOW_STATUSES = {
    "created",
    "planned",
    "pending_approval",
    "requires_action",
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "rejected",
    "draining",
}
DEFAULT_PROVIDER_PRICE_USD_PER_SECOND = {
    "local": 0.0,
    "ollama": 0.0,
    "modal": 0.0009,
    "runpod": 0.0008,
    "vast": 0.0005,
}
DEFAULT_STRATEGIES = {
    "json_array_chunker": {
        "kind": "splitter",
        "media": "json",
        "runs_in_api": True,
        "worker_job_type": None,
    },
    "json_array_merger": {
        "kind": "reducer",
        "media": "json",
        "runs_in_api": True,
        "worker_job_type": None,
    },
    "llm_reduce": {
        "kind": "reducer",
        "media": "json",
        "runs_in_api": False,
        "worker_job_type": "llm_heavy",
    },
    "token_estimator": {
        "kind": "estimator",
        "media": "text",
        "runs_in_api": True,
        "worker_job_type": None,
    },
    "ffprobe_estimator": {
        "kind": "estimator",
        "media": "video",
        "runs_in_api": False,
        "worker_job_type": "cpu_workflow_helper",
    },
    "ffmpeg_time_splitter": {
        "kind": "splitter",
        "media": "video",
        "runs_in_api": False,
        "worker_job_type": "cpu_workflow_helper",
    },
    "timeline_reducer": {
        "kind": "reducer",
        "media": "video",
        "runs_in_api": False,
        "worker_job_type": "cpu_workflow_helper",
    },
    "pdf_page_estimator": {
        "kind": "estimator",
        "media": "pdf",
        "runs_in_api": False,
        "worker_job_type": "cpu_workflow_helper",
    },
    "pdf_page_splitter": {
        "kind": "splitter",
        "media": "pdf",
        "runs_in_api": False,
        "worker_job_type": "cpu_workflow_helper",
    },
    "page_result_merger": {
        "kind": "reducer",
        "media": "pdf",
        "runs_in_api": False,
        "worker_job_type": "cpu_workflow_helper",
    },
}


def workflow_dir() -> Path:
    path = app_data_dir() / "workflows"
    path.mkdir(parents=True, exist_ok=True)
    return path


def workflow_path(workflow_id: str) -> Path:
    return workflow_dir() / f"{workflow_id}.json"


def workflow_events_path() -> Path:
    logs_dir = app_data_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir / "workflow-budget.jsonl"


def make_workflow_id() -> str:
    return f"wf-{now_unix()}-{uuid.uuid4().hex[:8]}"


def workflow_strategies(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_execution_policy()
    configured = policy.get("workflow_strategies")
    if isinstance(configured, dict):
        merged = dict(DEFAULT_STRATEGIES)
        merged.update(configured)
        return merged
    return dict(DEFAULT_STRATEGIES)


def budget_policy(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_execution_policy()
    configured = policy.get("workflow_budget_policy")
    if isinstance(configured, dict):
        return configured
    return {
        "default_budget_class": "standard",
        "budget_classes": {
            "standard": {
                "auto_approve_cap_usd": 1.0,
                "hard_cap_usd": 3.0,
                "allowed_providers": ["local", "ollama", "modal", "runpod", "vast"],
                "retry_multiplier": 1.2,
                "safety_margin": 1.3,
            },
            "batch_low_cost": {
                "auto_approve_cap_usd": 0.5,
                "hard_cap_usd": 2.0,
                "allowed_providers": ["ollama", "vast"],
                "retry_multiplier": 1.1,
                "safety_margin": 1.2,
            },
            "critical": {
                "auto_approve_cap_usd": 10.0,
                "hard_cap_usd": 25.0,
                "allowed_providers": ["modal", "runpod", "ollama"],
                "retry_multiplier": 1.5,
                "safety_margin": 1.5,
            },
        },
    }


def resolve_budget_class(business_context: dict[str, Any] | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    business_context = business_context if isinstance(business_context, dict) else {}
    budgets = budget_policy(policy)
    classes = dict(budgets.get("budget_classes", {}))
    budget_class = str(business_context.get("budget_class") or budgets.get("default_budget_class") or "standard")
    item = dict(classes.get(budget_class) or classes.get("standard") or {})
    if not item:
        item = {
            "auto_approve_cap_usd": 0.0,
            "hard_cap_usd": 0.0,
            "allowed_providers": [],
            "retry_multiplier": 1.0,
            "safety_margin": 1.0,
        }
    item["budget_class"] = budget_class
    item["ok"] = budget_class in classes
    if not item["ok"]:
        item["reason"] = f"unknown budget_class: {budget_class}"
    else:
        item["reason"] = "budget class resolved"
    return item


def validate_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    if "nodes" not in workflow and "jobs" not in workflow and "job_template" not in workflow:
        return {"ok": False, "workflow_version": WORKFLOW_VERSION, "errors": ["workflow must contain nodes, jobs, or job_template"]}
    if "nodes" not in workflow:
        return {"ok": True, "workflow_version": WORKFLOW_VERSION, "errors": []}
    nodes = workflow.get("nodes", [])
    edges = workflow.get("edges", [])
    errors = []
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes must be a non-empty list")
        nodes = []
    node_ids = [str(node.get("node_id") or "") for node in nodes if isinstance(node, dict)]
    if len(node_ids) != len(set(node_ids)):
        errors.append("node_id values must be unique")
    node_set = set(node_ids)
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_set}
    for edge in edges if isinstance(edges, list) else []:
        src = str(edge.get("from") or "")
        dst = str(edge.get("to") or "")
        if src not in node_set or dst not in node_set:
            errors.append(f"edge references missing node: {src}->{dst}")
            continue
        adjacency[src].append(dst)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            errors.append("workflow graph must be acyclic")
            return
        if node_id in visited:
            return
        visiting.add(node_id)
        for child in adjacency.get(node_id, []):
            visit(child)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in node_set:
        visit(node_id)
    return {"ok": not errors, "workflow_version": WORKFLOW_VERSION, "errors": errors}


def save_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    validation = validate_workflow(workflow)
    workflow_id = str(workflow.get("workflow_id") or make_workflow_id())
    manifest = _base_manifest(workflow_id, workflow, validation=validation)
    manifest["status"] = "created" if validation["ok"] else "failed"
    return _save_manifest(manifest)


def load_workflow(workflow_id: str) -> dict[str, Any]:
    path = workflow_path(workflow_id)
    if not path.is_file():
        return {"ok": False, "error": "workflow not found", "workflow_id": workflow_id}
    manifest = json.loads(path.read_text())
    return {"ok": True, "workflow": workflow_status(manifest), "path": str(path)}


def list_workflows(limit: int = 100) -> dict[str, Any]:
    rows = []
    for path in sorted(workflow_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            manifest = json.loads(path.read_text())
        except Exception:
            continue
        rows.append(_compact_workflow(workflow_status(manifest)))
    return {"ok": True, "count": len(rows), "workflows": rows}


def submit_bulk_workflow(payload: dict[str, Any], *, execute: bool = False, enqueue: bool = True) -> dict[str, Any]:
    from .queue import enqueue_job
    from .runner import submit_job

    workflow = _normalize_workflow_payload(payload)
    validation = validate_workflow(workflow)
    workflow_id = str(workflow.get("workflow_id") or make_workflow_id())
    business_context = _business_context(workflow)
    budget = resolve_budget_class(business_context)
    jobs_payload = workflow.get("jobs")
    if not isinstance(jobs_payload, list) or not jobs_payload:
        raise ValueError("bulk workflow requires non-empty jobs list")
    manifest = _base_manifest(workflow_id, workflow, validation=validation)
    manifest["business_context"] = business_context
    manifest["budget_policy"] = budget
    manifest["plan_quote"] = dict(workflow.get("plan_quote") or workflow.get("workflow_plan_quote") or {})
    manifest["status"] = "queued" if validation["ok"] else "failed"
    child_results = []
    for index, raw_job in enumerate(jobs_payload):
        job = Job.from_dict(dict(raw_job))
        _attach_workflow_metadata(
            job,
            workflow_id,
            stage=str(raw_job.get("workflow_stage") or "map"),
            index=index,
            business_context=business_context,
            workflow_plan_quote=manifest["plan_quote"],
        )
        result: dict[str, Any]
        provider = str(raw_job.get("provider") or workflow.get("provider") or "auto")
        _attach_workflow_workspace_plan(job, provider)
        if execute:
            result = submit_job(job, provider_name=provider, execute=True)
        elif enqueue:
            result = enqueue_job(job, provider_name=provider)
        else:
            store = JobStore()
            store.save(job)
            result = {"ok": True, "job": job.to_dict(), "path": str(store.job_path(job.job_id))}
        child_results.append(_child_from_job_dict(_result_job_dict(result), stage=job.metadata["workflow_stage"], index=index))
    manifest["children"] = child_results
    manifest["total_jobs"] = len(child_results)
    manifest["summary"] = aggregate_workflow(workflow_id)
    saved = _save_manifest(manifest)
    record_workflow_budget_event("bulk_submitted", workflow_status(manifest))
    return {**saved, "workflow": workflow_status(manifest)}


def plan_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    workflow = _normalize_workflow_payload(payload)
    workflow_type = str(workflow.get("workflow_type") or "scatter_gather")
    strategy = dict(workflow.get("strategy") or {})
    splitter = str(strategy.get("splitter") or "json_array_chunker")
    reducer = str(strategy.get("reducer") or "json_array_merger")
    strategies = workflow_strategies()
    if splitter not in strategies:
        raise ValueError(f"unknown splitter: {splitter}")
    if reducer not in strategies:
        raise ValueError(f"unknown reducer: {reducer}")
    business_context = _business_context(workflow)
    budget = resolve_budget_class(business_context)
    job_template = dict(workflow.get("job_template") or {})
    chunks = _plan_chunks(workflow, job_template, splitter)
    reduce_job_count = 1 if chunks else 0
    estimate = estimate_workflow_cost(workflow, chunks=chunks, reduce_job_count=reduce_job_count, budget=budget)
    decision = approval_decision(estimate, budget, workflow.get("limits"))
    action = workflow_action_requirements(workflow, estimate=estimate)
    if action["decision"] == "requires_action":
        decision = {
            "decision": "requires_action",
            "reason": action["reason"],
            "effective_hard_cap_usd": estimate["hard_cap_usd"],
            "required_actions": action["required_actions"],
        }
    elif action["decision"] in {"requires_backend_registration", "unsupported"}:
        decision = {
            "decision": action["decision"],
            "reason": action["reason"],
            "effective_hard_cap_usd": estimate["hard_cap_usd"],
            "required_actions": action.get("required_actions", []),
        }
    plan = {
        "workflow_type": workflow_type,
        "strategy": {"splitter": splitter, "reducer": reducer},
        "chunk_count": len(chunks),
        "map_job_count": len(chunks),
        "reduce_job_count": reduce_job_count,
        "chunks": [_public_chunk(item) for item in chunks],
        "can_run_now": decision["decision"] not in {"reject", "requires_action", "requires_backend_registration", "unsupported"},
        "action_requirements": action,
    }
    planned = {
        "workflow_version": WORKFLOW_VERSION,
        "business_context": business_context,
        "budget_policy": budget,
        "plan": plan,
        "estimate": estimate,
        "approval": decision,
    }
    plan_quote = _workflow_plan_quote(workflow, planned)
    return {
        "ok": decision["decision"] not in {"reject", "requires_backend_registration", "unsupported"},
        "workflow_version": WORKFLOW_VERSION,
        "business_context": business_context,
        "budget_policy": budget,
        "plan": plan,
        "estimate": estimate,
        "approval": decision,
        "plan_quote": plan_quote,
    }


def execute_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    from .queue import enqueue_job
    from .runner import submit_job

    workflow = _normalize_workflow_payload(payload)
    if "jobs" in workflow:
        return submit_bulk_workflow(workflow, execute=bool(workflow.get("execute")), enqueue=not bool(workflow.get("execute")))
    planned = plan_workflow(workflow)
    workflow_id = str(workflow.get("workflow_id") or make_workflow_id())
    manifest = _base_manifest(workflow_id, workflow, validation=validate_workflow(workflow))
    manifest["business_context"] = planned["business_context"]
    manifest["budget_policy"] = planned["budget_policy"]
    manifest["plan"] = planned["plan"]
    manifest["estimate"] = planned["estimate"]
    manifest["approval"] = planned["approval"]
    manifest["plan_quote"] = planned.get("plan_quote") or {}
    decision = planned["approval"]["decision"]
    if decision in {"reject", "requires_backend_registration", "unsupported"}:
        manifest["status"] = "rejected"
        saved = _save_manifest(manifest)
        record_workflow_budget_event("rejected", workflow_status(manifest))
        return {**saved, "ok": False, "workflow": workflow_status(manifest)}
    if decision == "requires_action":
        manifest["status"] = "requires_action"
        manifest["action_requirements"] = planned["plan"].get("action_requirements")
        saved = _save_manifest(manifest)
        record_workflow_budget_event("requires_action", workflow_status(manifest))
        return {**saved, "ok": True, "workflow": workflow_status(manifest)}
    if decision == "pending_approval" and not bool(workflow.get("force_approve")):
        manifest["status"] = "pending_approval"
        saved = _save_manifest(manifest)
        record_workflow_budget_event("pending_approval", workflow_status(manifest))
        return {**saved, "ok": True, "workflow": workflow_status(manifest)}
    manifest["status"] = "queued"
    child_results = []
    job_template = dict(workflow.get("job_template") or {})
    splitter = manifest["plan"]["strategy"]["splitter"]
    if _strategy_runs_in_api(splitter):
        for index, chunk in enumerate(_plan_chunks(workflow, job_template, splitter)):
            job = _job_from_chunk(
                job_template,
                chunk,
                workflow_id=workflow_id,
                index=index,
                business_context=manifest["business_context"],
                workflow_plan_quote=manifest["plan_quote"],
            )
            provider = str(workflow.get("provider") or "auto")
            _attach_workflow_workspace_plan(job, provider)
            result = (
                submit_job(job, provider_name=provider, execute=True)
                if bool(workflow.get("execute"))
                else enqueue_job(job, provider_name=provider)
            )
            child_results.append(_child_from_job_dict(_result_job_dict(result), stage="map", index=index))
    else:
        split_job = _helper_job_from_strategy(
            workflow,
            splitter,
            workflow_id=workflow_id,
            stage="split",
            index=0,
            business_context=manifest["business_context"],
            workflow_plan_quote=manifest["plan_quote"],
        )
        split_result = enqueue_job(split_job, provider_name="local")
        child_results.append(_child_from_job_dict(_result_job_dict(split_result), stage="split", index=0))
    manifest["children"] = child_results
    manifest["total_jobs"] = len(child_results)
    saved = _save_manifest(manifest)
    record_workflow_budget_event("queued", workflow_status(manifest))
    return {**saved, "workflow": workflow_status(manifest)}


def approve_workflow(workflow_id: str, *, principal: str = "", reason: str = "", execute: bool = False) -> dict[str, Any]:
    loaded = load_workflow(workflow_id)
    if not loaded["ok"]:
        return loaded
    manifest = loaded["workflow"]
    manifest["approved"] = {"principal": principal, "reason": reason, "approved_at": now_unix()}
    if manifest.get("status") == "pending_approval":
        manifest["status"] = "approved"
    _save_manifest(manifest)
    record_workflow_budget_event("approved", workflow_status(manifest))
    if execute:
        workflow = dict(manifest.get("workflow") or {})
        workflow["workflow_id"] = workflow_id
        workflow["force_approve"] = True
        return execute_workflow(workflow)
    return {"ok": True, "workflow": workflow_status(manifest)}


def drain_workflow(workflow_id: str, *, reason: str = "") -> dict[str, Any]:
    from .queue import cancel_group

    result = cancel_group(workflow_id=workflow_id)
    loaded = load_workflow(workflow_id)
    if loaded["ok"]:
        manifest = loaded["workflow"]
        manifest["status"] = "draining"
        manifest["drain"] = {"reason": reason, "drained_at": now_unix(), "cancel_result": result}
        _save_manifest(manifest)
        record_workflow_budget_event("draining", workflow_status(manifest), extra={"reason": reason})
    return {"ok": True, "workflow_id": workflow_id, "cancel_result": result}


def enforce_workflow_budget_drains(limit: int = 1000) -> dict[str, Any]:
    drained = []
    checked = 0
    for path in sorted(workflow_dir().glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        try:
            manifest = json.loads(path.read_text())
        except Exception:
            continue
        status = str(manifest.get("status") or "")
        if status in {"succeeded", "failed", "cancelled", "rejected", "pending_approval", "requires_action", "draining"}:
            continue
        checked += 1
        current = workflow_status(manifest)
        drift = current.get("cost_drift") if isinstance(current.get("cost_drift"), dict) else {}
        if drift.get("action") != "drain":
            continue
        result = drain_workflow(str(current.get("workflow_id")), reason="workflow projected cost exceeds hard cap")
        drained.append({"workflow_id": current.get("workflow_id"), "cost_drift": drift, "drain": result})
    return {"ok": True, "checked": checked, "drained_count": len(drained), "drained": drained}


def workflow_status(manifest: dict[str, Any]) -> dict[str, Any]:
    workflow_id = str(manifest.get("workflow_id") or "")
    summary = aggregate_workflow(workflow_id) if workflow_id else _empty_summary()
    out = dict(manifest)
    out["summary"] = summary
    out["cost_drift"] = cost_drift_decision(out, summary)
    if out.get("status") not in {"rejected", "pending_approval", "requires_action", "cancelled", "failed", "succeeded", "draining"}:
        out["status"] = _status_from_summary(out.get("status", "created"), summary)
    return out


def workflow_budget_monitor(limit: int = 1000) -> dict[str, Any]:
    workflows = []
    totals = {
        "workflow_count": 0,
        "running_or_queued": 0,
        "draining": 0,
        "projected_cost_usd": 0.0,
        "actual_cost_usd": 0.0,
        "estimated_cost_usd": 0.0,
    }
    for row in list_workflows(limit=limit).get("workflows", []):
        loaded = load_workflow(str(row.get("workflow_id") or ""))
        if not loaded.get("ok"):
            continue
        workflow = loaded["workflow"]
        summary = workflow.get("summary") if isinstance(workflow.get("summary"), dict) else {}
        drift = workflow.get("cost_drift") if isinstance(workflow.get("cost_drift"), dict) else {}
        status = str(workflow.get("status") or "")
        compact = {
            "workflow_id": workflow.get("workflow_id"),
            "status": status,
            "workflow_type": workflow.get("workflow_type"),
            "business_context": workflow.get("business_context"),
            "actual_cost_usd": summary.get("actual_cost_usd", 0.0),
            "estimated_cost_usd": summary.get("estimated_cost_usd", 0.0),
            "projected_cost_usd": drift.get("projected_cost_usd", 0.0),
            "hard_cap_usd": drift.get("hard_cap_usd"),
            "cost_action": drift.get("action"),
            "counts": summary.get("counts", {}),
        }
        workflows.append(compact)
        totals["workflow_count"] += 1
        if status in {"queued", "running", "approved"}:
            totals["running_or_queued"] += 1
        if status == "draining":
            totals["draining"] += 1
        totals["projected_cost_usd"] += float(compact["projected_cost_usd"] or 0)
        totals["actual_cost_usd"] += float(compact["actual_cost_usd"] or 0)
        totals["estimated_cost_usd"] += float(compact["estimated_cost_usd"] or 0)
    for key in ("projected_cost_usd", "actual_cost_usd", "estimated_cost_usd"):
        totals[key] = round(float(totals[key]), 6)
    return {"ok": True, "totals": totals, "workflows": workflows, "events_path": str(workflow_events_path())}


def advance_workflows(limit: int = 1000) -> dict[str, Any]:
    advanced = []
    for path in sorted(workflow_dir().glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        try:
            manifest = json.loads(path.read_text())
        except Exception:
            continue
        if str(manifest.get("status") or "") in {
            "rejected",
            "pending_approval",
            "requires_action",
            "failed",
            "cancelled",
            "succeeded",
            "draining",
        }:
            continue
        result = _advance_workflow_manifest(manifest)
        if result.get("advanced"):
            advanced.append(result)
    return {"ok": True, "advanced_count": len(advanced), "advanced": advanced}


def _advance_workflow_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    from .queue import enqueue_job

    workflow_id = str(manifest.get("workflow_id") or "")
    workflow = dict(manifest.get("workflow") or {})
    plan = dict(manifest.get("plan") or {})
    strategy = dict(plan.get("strategy") or workflow.get("strategy") or {})
    splitter = str(strategy.get("splitter") or "")
    reducer = str(strategy.get("reducer") or "")
    if not workflow_id:
        return {"ok": True, "workflow_id": workflow_id, "advanced": False, "reason": "missing workflow_id"}
    store = JobStore()
    jobs = [job for job in store.list_jobs(limit=10000) if str(job.metadata.get("workflow_id") or "") == workflow_id]
    stages: dict[str, list[Job]] = {}
    for job in jobs:
        stages.setdefault(str(job.metadata.get("workflow_stage") or ""), []).append(job)
    created = []
    split_jobs = [job for job in stages.get("split", []) if job.status == "succeeded"]
    if not _strategy_runs_in_api(splitter) and split_jobs and not stages.get("map"):
        split_result = _job_result_payload(store, split_jobs[0])
        segments = split_result.get("segments") if isinstance(split_result.get("segments"), list) else []
        for index, segment in enumerate(segments):
            map_job = _job_from_segment(
                dict(workflow.get("job_template") or {}),
                segment,
                workflow_id=workflow_id,
                index=index,
                business_context=dict(manifest.get("business_context") or {}),
                workflow_plan_quote=dict(manifest.get("plan_quote") or {}),
            )
            provider = str(workflow.get("provider") or "auto")
            _attach_workflow_workspace_plan(map_job, provider)
            result = enqueue_job(map_job, provider_name=provider)
            created.append(_child_from_job_dict(_result_job_dict(result), stage="map", index=index))
    map_jobs = stages.get("map") or []
    if map_jobs and all(job.status == "succeeded" for job in map_jobs) and not stages.get("reduce") and not _strategy_runs_in_api(reducer):
        reduce_job = _helper_job_from_strategy(
            workflow,
            reducer,
            workflow_id=workflow_id,
            stage="reduce",
            index=0,
            business_context=dict(manifest.get("business_context") or {}),
            workflow_plan_quote=dict(manifest.get("plan_quote") or {}),
        )
        reduce_job.metadata.setdefault("input", {})["items"] = [
            _asr_reduce_item(store, job)
            for job in sorted(map_jobs, key=lambda item: (int(item.metadata.get("workflow_chunk_index") or 0), item.job_id))
        ]
        result = enqueue_job(reduce_job, provider_name="local")
        created.append(_child_from_job_dict(_result_job_dict(result), stage="reduce", index=0))
    if created:
        manifest["children"] = list(manifest.get("children") or []) + created
        manifest["total_jobs"] = len(manifest["children"])
        manifest["status"] = "queued"
        _save_manifest(manifest)
        record_workflow_budget_event("advanced", workflow_status(manifest), extra={"created": created})
    return {"ok": True, "workflow_id": workflow_id, "advanced": bool(created), "created": created}


def record_workflow_budget_event(event: str, workflow: dict[str, Any], *, extra: dict[str, Any] | None = None) -> None:
    summary = workflow.get("summary") if isinstance(workflow.get("summary"), dict) else {}
    drift = workflow.get("cost_drift") if isinstance(workflow.get("cost_drift"), dict) else {}
    payload = {
        "event": event,
        "recorded_at": now_unix(),
        "workflow_id": workflow.get("workflow_id"),
        "status": workflow.get("status"),
        "workflow_type": workflow.get("workflow_type"),
        "business_context": workflow.get("business_context"),
        "actual_cost_usd": summary.get("actual_cost_usd"),
        "estimated_cost_usd": summary.get("estimated_cost_usd"),
        "projected_cost_usd": drift.get("projected_cost_usd"),
        "hard_cap_usd": drift.get("hard_cap_usd"),
        "cost_action": drift.get("action"),
    }
    if extra:
        payload["extra"] = dict(extra)
    with workflow_events_path().open("a") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def workflow_action_requirements(workflow: dict[str, Any], *, estimate: dict[str, Any] | None = None) -> dict[str, Any]:
    return evaluate_workflow_requirements(workflow, estimate=estimate)


def _job_result_payload(store: JobStore, job: Job) -> dict[str, Any]:
    path = store.artifact_dir(job.job_id) / "result.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text())
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _asr_reduce_item(store: JobStore, job: Job) -> dict[str, Any]:
    payload = _job_result_payload(store, job)
    if not payload:
        return {"job_id": job.job_id, "text": ""}
    text = payload.get("text")
    if text is None:
        text = payload.get("transcript")
    if text is None:
        text = payload.get("answer")
    segment_meta = job.metadata.get("input") if isinstance(job.metadata.get("input"), dict) else {}
    segment_info = segment_meta.get("segment") if isinstance(segment_meta.get("segment"), dict) else {}
    offset = float(segment_info.get("start_seconds") or 0)
    result = dict(payload)
    if offset:
        result["segments"] = [_offset_timed_row(item, offset) for item in payload.get("segments") or [] if isinstance(item, dict)]
        result["speaker_segments"] = [
            _offset_timed_row(item, offset) for item in payload.get("speaker_segments") or [] if isinstance(item, dict)
        ]
    return {
        "job_id": job.job_id,
        "chunk_index": job.metadata.get("workflow_chunk_index"),
        "start_seconds": offset,
        "end_seconds": segment_info.get("end_seconds"),
        "text": str(text or ""),
        "result": result,
    }


def _offset_timed_row(row: dict[str, Any], offset: float) -> dict[str, Any]:
    data = dict(row)
    for key in ("start", "end"):
        if data.get(key) is not None:
            try:
                data[key] = round(float(data[key]) + offset, 3)
            except (TypeError, ValueError):
                pass
    return data


def aggregate_workflow(workflow_id: str, store: JobStore | None = None) -> dict[str, Any]:
    store = store or JobStore()
    if not workflow_id:
        return _empty_summary()
    jobs = []
    for job in store.list_jobs(limit=10000):
        if str(job.metadata.get("workflow_id") or "") == workflow_id:
            jobs.append(job)
    counts: dict[str, int] = {}
    runtime = 0
    estimated_cost = 0.0
    actual_cost = 0.0
    children = []
    for job in sorted(jobs, key=lambda item: (item.created_at, item.job_id)):
        counts[job.status] = counts.get(job.status, 0) + 1
        runtime += int(job.runtime_seconds or 0)
        estimated_cost += _job_estimated_cost(job)
        actual_cost += _job_actual_cost(job)
        children.append(
            {
                "job_id": job.job_id,
                "status": job.status,
                "provider": job.provider or job.metadata.get("selected_provider") or job.metadata.get("requested_provider"),
                "stage": job.metadata.get("workflow_stage"),
                "chunk_index": job.metadata.get("workflow_chunk_index"),
                "runtime_seconds": job.runtime_seconds,
                "estimated_cost_usd": round(_job_estimated_cost(job), 6),
                "actual_cost_usd": round(_job_actual_cost(job), 6),
                "error": job.error,
            }
        )
    return {
        "workflow_id": workflow_id,
        "total_jobs": len(jobs),
        "counts": counts,
        "runtime_seconds_sum": runtime,
        "estimated_cost_usd": round(estimated_cost, 6),
        "actual_cost_usd": round(actual_cost, 6),
        "children": children,
    }


def cost_drift_decision(manifest: dict[str, Any], summary: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = summary or aggregate_workflow(str(manifest.get("workflow_id") or ""))
    estimate = manifest.get("estimate") if isinstance(manifest.get("estimate"), dict) else {}
    approval = manifest.get("approval") if isinstance(manifest.get("approval"), dict) else {}
    hard_cap = float(approval.get("effective_hard_cap_usd") or estimate.get("hard_cap_usd") or 0)
    actual = float(summary.get("actual_cost_usd") or summary.get("estimated_cost_usd") or 0)
    remaining_estimate = _remaining_estimated_cost(manifest, summary)
    projected = actual + remaining_estimate
    should_drain = bool(hard_cap and projected > hard_cap)
    return {
        "ok": not should_drain,
        "actual_cost_usd": round(actual, 6),
        "remaining_estimated_cost_usd": round(remaining_estimate, 6),
        "projected_cost_usd": round(projected, 6),
        "hard_cap_usd": hard_cap or None,
        "action": "drain" if should_drain else "continue",
    }


def merge_json_array_results(items: list[Any]) -> dict[str, Any]:
    merged = []
    for item in items:
        if isinstance(item, list):
            merged.extend(item)
        elif isinstance(item, dict) and isinstance(item.get("items"), list):
            merged.extend(item["items"])
        else:
            merged.append(item)
    return {"ok": True, "reducer": "json_array_merger", "items": merged, "count": len(merged)}


def _normalize_workflow_payload(payload: dict[str, Any]) -> dict[str, Any]:
    workflow = payload.get("workflow") if isinstance(payload.get("workflow"), dict) else payload
    return dict(workflow)


def _business_context(workflow: dict[str, Any]) -> dict[str, Any]:
    value = workflow.get("business_context")
    return dict(value) if isinstance(value, dict) else {}


def _base_manifest(workflow_id: str, workflow: dict[str, Any], *, validation: dict[str, Any]) -> dict[str, Any]:
    business_context = _business_context(workflow)
    return {
        "workflow_version": WORKFLOW_VERSION,
        "workflow_id": workflow_id,
        "created_at": now_unix(),
        "updated_at": now_unix(),
        "status": "created",
        "workflow_type": str(workflow.get("workflow_type") or "workflow"),
        "workflow": workflow,
        "business_context": business_context,
        "expected_budget_class": business_context.get("budget_class"),
        "validation": validation,
        "total_jobs": 0,
        "children": [],
    }


def _save_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    manifest = dict(manifest)
    manifest["updated_at"] = now_unix()
    workflow_id = str(manifest.get("workflow_id") or make_workflow_id())
    manifest["workflow_id"] = workflow_id
    path = workflow_path(workflow_id)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"ok": manifest.get("status") not in {"failed", "rejected"}, "workflow_id": workflow_id, "path": str(path)}


def _compact_workflow(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "workflow_id": manifest.get("workflow_id"),
        "status": manifest.get("status"),
        "workflow_type": manifest.get("workflow_type"),
        "business_context": manifest.get("business_context"),
        "summary": {k: v for k, v in dict(manifest.get("summary") or {}).items() if k != "children"},
        "approval": manifest.get("approval"),
        "created_at": manifest.get("created_at"),
        "updated_at": manifest.get("updated_at"),
    }


def _attach_workflow_metadata(
    job: Job,
    workflow_id: str,
    *,
    stage: str,
    index: int,
    business_context: dict[str, Any],
    workflow_plan_quote: dict[str, Any] | None = None,
) -> None:
    job.metadata["workflow_id"] = workflow_id
    job.metadata["workflow_stage"] = stage
    job.metadata["workflow_chunk_index"] = index
    job.metadata["business_context"] = dict(business_context)
    if workflow_plan_quote:
        job.metadata["workflow_plan_quote"] = dict(workflow_plan_quote)
    source_system = str(job.metadata.get("source_system") or business_context.get("app_id") or "").strip()
    if source_system:
        job.metadata["source_system"] = source_system


def _attach_workflow_workspace_plan(job: Job, provider_name: str) -> None:
    provider = str(provider_name or "auto")
    if provider == "auto":
        workflow_quote = job.metadata.get("workflow_plan_quote") if isinstance(job.metadata.get("workflow_plan_quote"), dict) else {}
        child_quote = job.metadata.get("plan_quote") if isinstance(job.metadata.get("plan_quote"), dict) else {}
        quote = child_quote if job.job_type == "cpu_workflow_helper" else (workflow_quote or child_quote)
        selected = quote.get("selected_option") if isinstance(quote.get("selected_option"), dict) else {}
        provider = str(selected.get("provider") or "auto")
    if provider == "auto":
        return
    job.metadata["workspace_plan"] = provider_workspace_plan(job, provider)


def _result_job_dict(result: dict[str, Any]) -> dict[str, Any]:
    job_data = result.get("job")
    return job_data if isinstance(job_data, dict) else {}


def _child_from_job_dict(job_data: dict[str, Any], *, stage: str, index: int) -> dict[str, Any]:
    metadata = job_data.get("metadata") if isinstance(job_data.get("metadata"), dict) else {}
    return {
        "job_id": job_data.get("job_id"),
        "status": job_data.get("status"),
        "stage": metadata.get("workflow_stage") or stage,
        "chunk_index": metadata.get("workflow_chunk_index", index),
        "provider": job_data.get("provider") or metadata.get("selected_provider") or metadata.get("requested_provider"),
        "estimated_cost_usd": round(
            float(
                ((metadata.get("cost_result") or {}) if isinstance(metadata.get("cost_result"), dict) else {}).get(
                    "estimated_total_cost_usd"
                )
                or 0
            ),
            6,
        ),
        "runtime_seconds": job_data.get("runtime_seconds"),
        "error": job_data.get("error", ""),
    }


def _plan_chunks(workflow: dict[str, Any], job_template: dict[str, Any], splitter: str) -> list[dict[str, Any]]:
    if splitter != "json_array_chunker":
        strategies = workflow_strategies()
        strategy = dict(strategies.get(splitter) or {})
        return [
            {
                "chunk_index": 0,
                "items": [],
                "estimated_tokens": _input_size_hint(workflow).get("estimated_tokens", 0),
                "splitter": splitter,
                "external": not bool(strategy.get("runs_in_api")),
                "worker_job_type": strategy.get("worker_job_type"),
            }
        ]
    items = _json_items(workflow)
    max_tokens = _chunk_token_budget(workflow, job_template)
    chunks = []
    current: list[Any] = []
    current_tokens = 0
    for item in items:
        item_tokens = max(1, _estimated_json_tokens(item))
        if current and current_tokens + item_tokens > max_tokens:
            chunks.append({"chunk_index": len(chunks), "items": current, "estimated_tokens": current_tokens, "splitter": splitter})
            current = []
            current_tokens = 0
        current.append(item)
        current_tokens += item_tokens
    if current or not chunks:
        chunks.append({"chunk_index": len(chunks), "items": current, "estimated_tokens": current_tokens, "splitter": splitter})
    return chunks


def _json_items(workflow: dict[str, Any]) -> list[Any]:
    payload = workflow.get("input_payload") if isinstance(workflow.get("input_payload"), dict) else {}
    for key in ("items", "json_array"):
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, list):
            return value
    input_uri = str(workflow.get("input_uri") or payload.get("input_uri") or payload.get("path") or "")
    if input_uri.startswith("file://"):
        input_uri = input_uri.removeprefix("file://")
    if input_uri and not input_uri.startswith(("s3://", "http://", "https://")):
        path = Path(input_uri).expanduser()
        if path.is_file():
            value = json.loads(path.read_text())
            if isinstance(value, list):
                return value
            if isinstance(value, dict) and isinstance(value.get("items"), list):
                return value["items"]
    size = _input_size_hint(workflow)
    count = int(size.get("item_count") or size.get("article_count") or 0)
    if count > 0:
        return [{"item_id": index, "estimated_tokens": int(size.get("estimated_tokens_per_item") or 500)} for index in range(count)]
    return []


def _input_size_hint(workflow: dict[str, Any]) -> dict[str, Any]:
    payload = workflow.get("input_payload") if isinstance(workflow.get("input_payload"), dict) else {}
    size = workflow.get("input_size") if isinstance(workflow.get("input_size"), dict) else {}
    payload_size = payload.get("input_size") if isinstance(payload.get("input_size"), dict) else {}
    out = dict(size)
    out.update(payload_size)
    for key in ("article_count", "item_count", "estimated_tokens", "duration_seconds", "page_count"):
        if key in payload and key not in out:
            out[key] = payload[key]
    return out


def _strategy_runs_in_api(name: str) -> bool:
    return bool(dict(workflow_strategies().get(name) or {}).get("runs_in_api"))


def _helper_job_from_strategy(
    workflow: dict[str, Any],
    strategy_name: str,
    *,
    workflow_id: str,
    stage: str,
    index: int,
    business_context: dict[str, Any],
    workflow_plan_quote: dict[str, Any] | None = None,
) -> Job:
    strategy = dict(workflow_strategies().get(strategy_name) or {})
    worker_job_type = str(strategy.get("worker_job_type") or "cpu_workflow_helper")
    if worker_job_type != "cpu_workflow_helper":
        worker_job_type = "cpu_workflow_helper"
    payload = workflow.get("input_payload") if isinstance(workflow.get("input_payload"), dict) else {}
    input_uri = str(workflow.get("input_uri") or payload.get("input_uri") or payload.get("path") or f"workflow://{workflow_id}/{stage}")
    data = {
        "job_id": f"{worker_job_type}-{workflow_id}-{stage}-{index}",
        "job_type": worker_job_type,
        "input_uri": input_uri,
        "output_uri": f"workflow://{workflow_id}/{stage}/{index}/out",
        "worker_image": "local:cpu-workflow-helper",
        "gpu_profile": "cpu",
        "provider": "local",
        "metadata": {
            "input": {
                "action": strategy_name,
                "workflow_id": workflow_id,
                "stage": stage,
                "input_uri": input_uri,
                "items": payload.get("items", []),
                "input_size": _input_size_hint(workflow),
            }
        },
    }
    job = Job.from_dict(data)
    _attach_workflow_metadata(
        job,
        workflow_id,
        stage=stage,
        index=index,
        business_context=business_context,
        workflow_plan_quote=workflow_plan_quote,
    )
    job.metadata["plan_quote"] = _local_helper_plan_quote(job, workflow, strategy_name)
    _attach_workflow_workspace_plan(job, "local")
    return job


def _chunk_token_budget(workflow: dict[str, Any], job_template: dict[str, Any]) -> int:
    strategy = workflow.get("strategy") if isinstance(workflow.get("strategy"), dict) else {}
    requested = int(strategy.get("max_chunk_tokens") or strategy.get("target_chunk_tokens") or 0)
    if requested:
        return max(1, requested)
    routing = (job_template.get("metadata") or {}).get("routing") if isinstance(job_template.get("metadata"), dict) else {}
    routing = routing if isinstance(routing, dict) else {}
    model_limit = int(routing.get("max_input_tokens") or workflow.get("max_input_tokens") or 32768)
    reserved = int(strategy.get("reserved_output_tokens") or 4096)
    return max(1024, model_limit - reserved)


def _estimated_json_tokens(item: Any) -> int:
    if isinstance(item, dict) and item.get("estimated_tokens") is not None:
        try:
            return int(item["estimated_tokens"])
        except (TypeError, ValueError):
            pass
    return max(1, len(json.dumps(item, ensure_ascii=False)) // 4)


def _job_from_chunk(
    job_template: dict[str, Any],
    chunk: dict[str, Any],
    *,
    workflow_id: str,
    index: int,
    business_context: dict[str, Any],
    workflow_plan_quote: dict[str, Any] | None = None,
) -> Job:
    data = dict(job_template)
    metadata = dict(data.get("metadata") or {})
    input_data = dict(metadata.get("input") or {})
    input_data["items"] = chunk.get("items", [])
    input_data["estimated_input_tokens"] = chunk.get("estimated_tokens", 0)
    metadata["input"] = input_data
    routing = dict(metadata.get("routing") or {})
    routing["estimated_input_tokens"] = chunk.get("estimated_tokens", 0)
    metadata["routing"] = routing
    data["metadata"] = metadata
    data.setdefault("input_uri", f"workflow://{workflow_id}/chunks/{index}")
    data.setdefault("output_uri", f"workflow://{workflow_id}/outputs/{index}")
    job = Job.from_dict(data)
    _attach_workflow_metadata(
        job,
        workflow_id,
        stage="map",
        index=index,
        business_context=business_context,
        workflow_plan_quote=workflow_plan_quote,
    )
    return job


def _job_from_segment(
    job_template: dict[str, Any],
    segment: dict[str, Any],
    *,
    workflow_id: str,
    index: int,
    business_context: dict[str, Any],
    workflow_plan_quote: dict[str, Any] | None = None,
) -> Job:
    data = dict(job_template)
    segment_uri = str(segment.get("uri") or segment.get("path") or f"workflow://{workflow_id}/segments/{index}")
    data["input_uri"] = segment_uri
    data.setdefault("output_uri", f"workflow://{workflow_id}/outputs/{index}")
    metadata = dict(data.get("metadata") or {})
    input_data = dict(metadata.get("input") or {})
    input_data["segment"] = dict(segment)
    input_data["input_uri"] = segment_uri
    metadata["input"] = input_data
    data["metadata"] = metadata
    job = Job.from_dict(data)
    if not job.job_id:
        job.job_id = f"{job.job_type}-{workflow_id}-map-{index}"
    _attach_workflow_metadata(
        job,
        workflow_id,
        stage="map",
        index=index,
        business_context=business_context,
        workflow_plan_quote=workflow_plan_quote,
    )
    return job


def _public_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_index": chunk.get("chunk_index"),
        "item_count": len(chunk.get("items") or []),
        "estimated_tokens": chunk.get("estimated_tokens"),
        "splitter": chunk.get("splitter"),
        "external": bool(chunk.get("external")),
    }


def estimate_workflow_cost(
    workflow: dict[str, Any],
    *,
    chunks: list[dict[str, Any]],
    reduce_job_count: int,
    budget: dict[str, Any],
) -> dict[str, Any]:
    provider = str(workflow.get("provider") or _first_allowed_provider(budget) or "modal")
    price = _provider_price(provider)
    runtime = _estimated_runtime_seconds(workflow, provider)
    map_count = len(chunks)
    reducer_runtime = float(
        ((workflow.get("strategy") or {}) if isinstance(workflow.get("strategy"), dict) else {}).get("estimated_reduce_seconds")
        or max(10.0, runtime * 0.5)
    )
    raw = (map_count * runtime + reduce_job_count * reducer_runtime) * price
    retry_multiplier = float(budget.get("retry_multiplier") or 1.0)
    safety_margin = float(budget.get("safety_margin") or 1.0)
    p50 = raw * retry_multiplier
    p95 = p50 * safety_margin
    return {
        "provider": provider,
        "provider_price_usd_per_second": price,
        "map_job_count": map_count,
        "reduce_job_count": reduce_job_count,
        "estimated_runtime_seconds_per_map": round(runtime, 3),
        "estimated_runtime_seconds_reduce": round(reducer_runtime, 3),
        "retry_multiplier": retry_multiplier,
        "safety_margin": safety_margin,
        "estimated_cost_p50_usd": round(p50, 6),
        "estimated_cost_p95_usd": round(p95, 6),
        "auto_approve_cap_usd": float(budget.get("auto_approve_cap_usd") or 0),
        "hard_cap_usd": float(budget.get("hard_cap_usd") or 0),
    }


def _workflow_plan_quote(workflow: dict[str, Any], planned: dict[str, Any]) -> dict[str, Any]:
    job_template = dict(workflow.get("job_template") or {})
    estimate = dict(planned.get("estimate") or {})
    provider = str(estimate.get("provider") or workflow.get("provider") or "")
    selected = {
        "provider": provider,
        "gpu_profile": str(job_template.get("gpu_profile") or workflow.get("gpu_profile") or ""),
        "job_type": str(job_template.get("job_type") or ""),
        "estimated_total_seconds_p50": estimate.get("estimated_runtime_seconds_per_map"),
        "estimated_total_seconds_p95": estimate.get("estimated_runtime_seconds_per_map"),
        "estimated_total_cost_usd_p50": estimate.get("estimated_cost_p50_usd"),
        "estimated_total_cost_usd_p95": estimate.get("estimated_cost_p95_usd"),
    }
    plan = {
        "contract_version": WORKFLOW_VERSION,
        "request": _workflow_quote_request(workflow),
        "catalog_version": WORKFLOW_VERSION,
        "catalog_snapshot_id": f"workflow:{planned.get('workflow_version') or WORKFLOW_VERSION}",
        "gpu_profile": selected["gpu_profile"],
        "selected_option": selected,
        "options": [selected] if provider else [],
        "refusals": [],
        "estimate": estimate,
        "approval": planned.get("approval") or {},
        "can_run_now": bool((planned.get("plan") or {}).get("can_run_now")),
        "action_requirements": (planned.get("plan") or {}).get("action_requirements") or {},
        "created_at": now_unix(),
    }
    return build_plan_quote(plan)


def _workflow_quote_request(workflow: dict[str, Any]) -> dict[str, Any]:
    payload = workflow.get("input_payload") if isinstance(workflow.get("input_payload"), dict) else {}
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    return {
        "workflow_type": str(workflow.get("workflow_type") or ""),
        "strategy": dict(workflow.get("strategy") or {}),
        "provider": str(workflow.get("provider") or ""),
        "business_context": dict(workflow.get("business_context") or {}),
        "limits": dict(workflow.get("limits") or {}),
        "job_template": _workflow_quote_job_template(dict(workflow.get("job_template") or {})),
        "input_size": {
            **_input_size_hint(workflow),
            "item_count": len(items) if items else _input_size_hint(workflow).get("item_count", 0),
        },
    }


def _workflow_quote_job_template(job_template: dict[str, Any]) -> dict[str, Any]:
    metadata = job_template.get("metadata") if isinstance(job_template.get("metadata"), dict) else {}
    return {
        "job_type": job_template.get("job_type"),
        "gpu_profile": job_template.get("gpu_profile"),
        "worker_image": job_template.get("worker_image"),
        "model": job_template.get("model"),
        "limits": dict(job_template.get("limits") or {}),
        "routing": dict(metadata.get("routing") or {}),
        "model_requirements": dict(metadata.get("model_requirements") or {}),
    }


def _local_helper_plan_quote(job: Job, workflow: dict[str, Any], strategy_name: str) -> dict[str, Any]:
    selected = {
        "provider": "local",
        "gpu_profile": "cpu",
        "job_type": job.job_type,
        "estimated_total_seconds_p50": 0,
        "estimated_total_seconds_p95": 0,
        "estimated_total_cost_usd_p50": 0.0,
        "estimated_total_cost_usd_p95": 0.0,
    }
    return build_plan_quote(
        {
            "contract_version": WORKFLOW_VERSION,
            "request": {
                "workflow_type": str(workflow.get("workflow_type") or ""),
                "strategy": strategy_name,
                "workflow_stage": job.metadata.get("workflow_stage"),
                "workflow_chunk_index": job.metadata.get("workflow_chunk_index"),
            },
            "catalog_version": WORKFLOW_VERSION,
            "catalog_snapshot_id": "workflow:local-cpu-helper",
            "gpu_profile": "cpu",
            "selected_option": selected,
            "options": [selected],
            "refusals": [],
            "estimate": {"estimated_cost_p50_usd": 0.0, "estimated_cost_p95_usd": 0.0},
            "approval": {"decision": "auto_execute", "reason": "local CPU workflow helper"},
            "can_run_now": True,
            "action_requirements": {"decision": "ready", "required_actions": []},
            "created_at": now_unix(),
        }
    )


def approval_decision(estimate: dict[str, Any], budget: dict[str, Any], limits: Any = None) -> dict[str, Any]:
    limits = limits if isinstance(limits, dict) else {}
    hard_cap = float(budget.get("hard_cap_usd") or 0)
    if limits.get("max_cost_usd") is not None:
        hard_cap = min(hard_cap, float(limits.get("max_cost_usd"))) if hard_cap else float(limits.get("max_cost_usd"))
    auto_cap = float(budget.get("auto_approve_cap_usd") or 0)
    p95 = float(estimate.get("estimated_cost_p95_usd") or 0)
    if hard_cap and p95 > hard_cap:
        decision = "reject"
        reason = "estimated p95 cost exceeds hard cap"
    elif auto_cap and p95 > auto_cap:
        decision = "pending_approval"
        reason = "estimated p95 cost exceeds auto approval cap"
    else:
        decision = "auto_execute"
        reason = "estimated p95 cost within auto approval cap"
    return {
        "decision": decision,
        "reason": reason,
        "estimated_cost_p95_usd": p95,
        "auto_approve_cap_usd": auto_cap,
        "effective_hard_cap_usd": hard_cap or None,
    }


def _provider_price(provider: str) -> float:
    policy = load_execution_policy()
    table = policy.get("provider_price_usd_per_second")
    if isinstance(table, dict) and table.get(provider) is not None:
        return float(table[provider])
    cost_model = policy.get("workflow_cost_model")
    if isinstance(cost_model, dict):
        provider_prices = cost_model.get("provider_price_usd_per_second")
        if isinstance(provider_prices, dict) and provider_prices.get(provider) is not None:
            return float(provider_prices[provider])
    return float(DEFAULT_PROVIDER_PRICE_USD_PER_SECOND.get(provider, 0.001))


def _estimated_runtime_seconds(workflow: dict[str, Any], provider: str) -> float:
    strategy = workflow.get("strategy") if isinstance(workflow.get("strategy"), dict) else {}
    if strategy.get("estimated_map_seconds") is not None:
        return float(strategy["estimated_map_seconds"])
    template = workflow.get("job_template") if isinstance(workflow.get("job_template"), dict) else {}
    routing = (template.get("metadata") or {}).get("routing") if isinstance(template.get("metadata"), dict) else {}
    if isinstance(routing, dict) and routing.get("estimated_gpu_runtime_seconds") is not None:
        return float(routing["estimated_gpu_runtime_seconds"])
    stats = collect_stats()
    key = f"{provider}:{template.get('job_type') or 'llm_heavy'}:{template.get('gpu_profile') or 'llm_heavy'}"
    group = (stats.get("groups") or {}).get(key)
    if isinstance(group, dict):
        remote = group.get("remote_runtime_seconds") if isinstance(group.get("remote_runtime_seconds"), dict) else {}
        if remote.get("p50") is not None:
            return float(remote["p50"])
    return 60.0


def _first_allowed_provider(budget: dict[str, Any]) -> str:
    allowed = budget.get("allowed_providers")
    if isinstance(allowed, list) and allowed:
        return str(allowed[0])
    return "modal"


def _status_from_summary(current: str, summary: dict[str, Any]) -> str:
    total = int(summary.get("total_jobs") or 0)
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    if not total:
        return current
    if counts.get("running") or counts.get("starting"):
        return "running"
    if counts.get("queued"):
        return "queued"
    if counts.get("failed"):
        return "failed"
    if counts.get("cancelled"):
        return "cancelled"
    if counts.get("succeeded") == total:
        return "succeeded"
    return current


def _remaining_estimated_cost(manifest: dict[str, Any], summary: dict[str, Any]) -> float:
    estimate = manifest.get("estimate") if isinstance(manifest.get("estimate"), dict) else {}
    total = int((manifest.get("plan") or {}).get("map_job_count") or manifest.get("total_jobs") or summary.get("total_jobs") or 0)
    done = 0
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    for status in ("succeeded", "failed", "cancelled"):
        done += int(counts.get(status) or 0)
    remaining = max(0, total - done)
    if not total:
        return 0.0
    p95 = float(estimate.get("estimated_cost_p95_usd") or 0)
    return (p95 / total) * remaining if p95 else 0.0


def _empty_summary() -> dict[str, Any]:
    return {
        "workflow_id": "",
        "total_jobs": 0,
        "counts": {},
        "runtime_seconds_sum": 0,
        "estimated_cost_usd": 0.0,
        "actual_cost_usd": 0.0,
        "children": [],
    }


def _job_estimated_cost(job: Job) -> float:
    cost_result = job.metadata.get("cost_result")
    if isinstance(cost_result, dict):
        return float(cost_result.get("estimated_total_cost_usd") or 0)
    return float(cost_estimate(job).get("estimated_total_cost_usd") or 0)


def _job_actual_cost(job: Job) -> float:
    actual = job.metadata.get("actual_cost_usd")
    if actual is not None:
        try:
            return float(actual)
        except (TypeError, ValueError):
            return 0.0
    return _job_estimated_cost(job)
