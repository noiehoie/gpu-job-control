from __future__ import annotations

from typing import Any
import re

from .destructive import destructive_preflight
from .models import now_unix
from .providers.vast import VastProvider
from .store import JobStore
from .timing import timing_summary


GPU_JOB_LABEL_RE = re.compile(r"^gpu-job:(.+)$")
TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled"}
INACTIVE_VAST_STATES = {"deleted", "destroyed", "terminated", "stopped", "exited"}
VAST_PROVISIONING_STATES = {"pending", "provisioning", "starting", "loading", "unloaded"}
VAST_RUNNING_STATES = {"running", "loaded"}
VAST_STOPPING_STATES = {"stopping", "exiting"}
VAST_TERMINAL_STATES = INACTIVE_VAST_STATES
REAPER_DESTROYABLE_LIFECYCLE_PHASES = {"running"}
REAPER_MODES = {"conservative"}
SUPPORTED_ORPHAN_PROVIDERS = {"modal", "runpod", "vast"}
REASON_CATEGORY = {
    "job_missing": "ghost",
    "job_unreadable": "job_unreadable",
    "terminal_job_active_instance": "zombie",
    "provider_job_id_mismatch": "id_mismatch",
}


def vast_orphan_inventory(
    *,
    store: JobStore | None = None,
    provider: VastProvider | None = None,
    instances: list[dict[str, Any]] | None = None,
    checked_at: int | None = None,
) -> dict[str, Any]:
    """Task 0 dry-run inventory only; never destroys provider resources."""
    store = store or JobStore()
    provider = provider or VastProvider()
    remote_instances = instances if instances is not None else provider._instances()
    candidates: list[dict[str, Any]] = []
    summary = {
        "job_missing": 0,
        "job_unreadable": 0,
        "terminal_job_active_instance": 0,
        "provider_job_id_mismatch": 0,
    }

    for item in remote_instances:
        label = str(item.get("label") or "")
        match = GPU_JOB_LABEL_RE.fullmatch(label)
        if not match:
            continue
        job_id = match.group(1)
        if not job_id:
            continue
        instance_id = _string_or_empty(item.get("id"))
        if not _is_vast_instance_active(item):
            continue
        job = None
        job_load_error = ""
        if store.job_path(job_id).is_file():
            try:
                job = store.load(job_id)
            except Exception:
                job_load_error = "job_file_parse_failed"
                job = None

        reason = ""
        if job_load_error:
            reason = "job_unreadable"
        elif job is None:
            reason = "job_missing"
        elif job.status in TERMINAL_JOB_STATUSES:
            reason = "terminal_job_active_instance"
        elif job.provider_job_id and instance_id and str(job.provider_job_id) != instance_id:
            reason = "provider_job_id_mismatch"

        if not reason:
            continue
        summary[reason] += 1
        candidates.append(_candidate(item, job_id=job_id, reason=reason, job=job, instance_id=instance_id, job_load_error=job_load_error))

    return {
        "ok": True,
        "dry_run": True,
        "provider": "vast",
        "checked_at": checked_at if checked_at is not None else now_unix(),
        "instances_seen": len(remote_instances),
        "candidates": candidates,
        "summary": summary,
    }


def vast_orphan_reaper(
    *,
    apply: bool = False,
    principal: str = "",
    max_instances: int = 10,
    mode_policy: str = "conservative",
    store: JobStore | None = None,
    provider: VastProvider | None = None,
    checked_at: int | None = None,
) -> dict[str, Any]:
    store = store or JobStore()
    provider = provider or VastProvider()
    checked = checked_at if checked_at is not None else now_unix()
    if mode_policy not in REAPER_MODES:
        return {
            "ok": False,
            "reaper_policy_version": "vast-orphan-reaper-policy-v1",
            "provider": "vast",
            "mode_policy": mode_policy,
            "mode": "apply" if apply else "dry_run",
            "dry_run": not apply,
            "checked_at": checked,
            "error": "unsupported_reaper_mode_policy",
            "supported_mode_policies": sorted(REAPER_MODES),
            "inventory": None,
            "actions": [],
            "summary": {"candidate_count": 0, "eligible_count": 0, "destroyed_count": 0, "skipped_count": 0},
        }
    inventory = vast_orphan_inventory(store=store, provider=provider, checked_at=checked)
    actions: list[dict[str, Any]] = []
    destroyed = 0
    skipped = 0
    limit = max(0, int(max_instances))

    for candidate in inventory["candidates"]:
        action = _reaper_plan_for_candidate(candidate, store=store)
        if apply and action["eligible"] and destroyed >= limit:
            action["eligible"] = False
            action["would_destroy"] = False
            action["skip_reason"] = "max_instances_exceeded"
        if apply and action["eligible"]:
            _apply_reaper_action(action, store=store, provider=provider, principal=principal)
            destroy_result = action.get("destroy_result")
            if isinstance(destroy_result, dict) and destroy_result.get("ok"):
                destroyed += 1
            else:
                skipped += 1
        elif not action["eligible"]:
            skipped += 1
        actions.append(action)

    ok = not apply or all(not item.get("eligible") or item.get("destroy_result", {}).get("ok") for item in actions)
    return {
        "ok": ok,
        "reaper_policy_version": "vast-orphan-reaper-policy-v1",
        "provider": "vast",
        "mode_policy": mode_policy,
        "mode": "apply" if apply else "dry_run",
        "dry_run": not apply,
        "checked_at": checked,
        "inventory": inventory,
        "actions": actions,
        "summary": {
            "candidate_count": len(inventory["candidates"]),
            "eligible_count": sum(1 for item in actions if item.get("eligible")),
            "destroyed_count": destroyed,
            "skipped_count": skipped,
        },
    }


def orphan_inventory(
    *,
    providers: list[str] | None = None,
    store: JobStore | None = None,
    provider_objects: dict[str, Any] | None = None,
    checked_at: int | None = None,
) -> dict[str, Any]:
    """Provider-neutral orphan inventory.

    This is intentionally conservative. Vast keeps its lifecycle-aware
    inventory. Other providers are inventory/report-only unless an exact stored
    terminal job -> provider resource id match exists.
    """
    checked = checked_at if checked_at is not None else now_unix()
    store = store or JobStore()
    selected = providers or sorted(SUPPORTED_ORPHAN_PROVIDERS)
    inventories: dict[str, Any] = {}
    for name in selected:
        if name not in SUPPORTED_ORPHAN_PROVIDERS:
            inventories[name] = {
                "ok": False,
                "provider": name,
                "error": "unsupported_orphan_provider",
                "supported_providers": sorted(SUPPORTED_ORPHAN_PROVIDERS),
            }
            continue
        provider = (provider_objects or {}).get(name)
        if name == "vast":
            inventories[name] = vast_orphan_inventory(store=store, provider=provider, checked_at=checked)
        else:
            inventories[name] = _generic_orphan_inventory(name, store=store, provider=provider, checked_at=checked)
    return {
        "ok": all(item.get("ok", False) for item in inventories.values()),
        "inventory_version": "gpu-job-provider-orphan-inventory-v1",
        "checked_at": checked,
        "providers": inventories,
        "summary": {
            "provider_count": len(inventories),
            "candidate_count": sum(len(item.get("candidates") or []) for item in inventories.values() if isinstance(item, dict)),
        },
    }


def orphan_reaper(
    *,
    providers: list[str] | None = None,
    apply: bool = False,
    principal: str = "",
    max_resources: int = 10,
    mode_policy: str = "conservative",
    store: JobStore | None = None,
    provider_objects: dict[str, Any] | None = None,
    checked_at: int | None = None,
) -> dict[str, Any]:
    checked = checked_at if checked_at is not None else now_unix()
    store = store or JobStore()
    selected = providers or sorted(SUPPORTED_ORPHAN_PROVIDERS)
    results: dict[str, Any] = {}
    for name in selected:
        provider = (provider_objects or {}).get(name)
        if name == "vast":
            results[name] = vast_orphan_reaper(
                apply=apply,
                principal=principal,
                max_instances=max_resources,
                mode_policy=mode_policy,
                store=store,
                provider=provider,
                checked_at=checked,
            )
        elif name in SUPPORTED_ORPHAN_PROVIDERS:
            results[name] = _generic_orphan_reaper(
                name,
                apply=apply,
                principal=principal,
                max_resources=max_resources,
                mode_policy=mode_policy,
                store=store,
                provider=provider,
                checked_at=checked,
            )
        else:
            results[name] = {
                "ok": False,
                "provider": name,
                "error": "unsupported_orphan_provider",
                "supported_providers": sorted(SUPPORTED_ORPHAN_PROVIDERS),
            }
    return {
        "ok": all(item.get("ok", False) for item in results.values()),
        "reaper_version": "gpu-job-provider-orphan-reaper-v1",
        "mode": "apply" if apply else "dry_run",
        "dry_run": not apply,
        "checked_at": checked,
        "providers": results,
        "summary": {
            "provider_count": len(results),
            "candidate_count": sum(int((item.get("summary") or {}).get("candidate_count") or 0) for item in results.values()),
            "destroyed_count": sum(int((item.get("summary") or {}).get("destroyed_count") or 0) for item in results.values()),
            "skipped_count": sum(int((item.get("summary") or {}).get("skipped_count") or 0) for item in results.values()),
        },
    }


def _reaper_plan_for_candidate(candidate: dict[str, Any], *, store: JobStore) -> dict[str, Any]:
    reason = str(candidate.get("reason") or "")
    instance_id = _string_or_empty(candidate.get("instance_id"))
    job_id = str(candidate.get("job_id") or "")
    action = {
        **candidate,
        "eligible": False,
        "would_destroy": False,
        "skip_reason": "",
        "preflight": None,
        "fresh_instance": None,
        "destroy_result": None,
    }
    if reason != "terminal_job_active_instance":
        action["skip_reason"] = "report_only_reason"
        return action
    lifecycle_phase = str((candidate.get("evidence") or {}).get("provider_state", {}).get("lifecycle_phase") or "")
    if lifecycle_phase not in REAPER_DESTROYABLE_LIFECYCLE_PHASES:
        action["skip_reason"] = f"instance_lifecycle_not_destroyable:{lifecycle_phase or 'unknown'}"
        return action
    if not instance_id or str(candidate.get("provider_job_id") or "") != instance_id:
        action["skip_reason"] = "provider_job_id_not_exact_match"
        return action
    if not store.job_path(job_id).is_file():
        action["skip_reason"] = "job_missing_on_reaper_plan"
        return action
    try:
        job = store.load(job_id)
    except Exception:
        action["skip_reason"] = "job_load_failed"
        return action
    if job.status not in TERMINAL_JOB_STATUSES:
        action["skip_reason"] = "job_not_terminal"
        return action
    if not _has_cleanup_evidence(job):
        action["skip_reason"] = "missing_cleanup_evidence"
        return action
    action["eligible"] = True
    action["would_destroy"] = True
    return action


def _generic_orphan_inventory(
    provider_name: str,
    *,
    store: JobStore,
    provider: Any | None,
    checked_at: int,
) -> dict[str, Any]:
    provider = provider or _provider_by_name(provider_name)
    try:
        guard = provider.cost_guard() if provider is not None and hasattr(provider, "cost_guard") else {}
    except Exception as exc:
        return {
            "ok": False,
            "dry_run": True,
            "provider": provider_name,
            "checked_at": checked_at,
            "error": "cost_guard_failed",
            "reason": str(exc),
            "resources_seen": 0,
            "candidates": [],
            "summary": {"terminal_job_active_resource": 0, "unmatched_billable_resource": 0},
            "guard": {"ok": False, "error": "cost_guard_failed", "reason": str(exc), "billable_resources": []},
        }
    resources = list(guard.get("billable_resources") or []) if isinstance(guard, dict) else []
    jobs_by_provider_id: dict[str, Any] = {}
    for job in store.list_jobs(limit=5000):
        if str(job.provider or job.metadata.get("selected_provider") or "") != provider_name:
            continue
        if job.provider_job_id:
            jobs_by_provider_id[str(job.provider_job_id)] = job
    candidates = []
    summary = {"terminal_job_active_resource": 0, "unmatched_billable_resource": 0}
    for resource in resources:
        resource_id = _string_or_empty(resource.get("id") if isinstance(resource, dict) else "")
        job = jobs_by_provider_id.get(resource_id)
        if job and job.status in TERMINAL_JOB_STATUSES:
            reason = "terminal_job_active_resource"
            summary[reason] += 1
            candidates.append(_generic_candidate(provider_name, resource, job=job, reason=reason, resource_id=resource_id))
        else:
            reason = "unmatched_billable_resource"
            summary[reason] += 1
            candidates.append(_generic_candidate(provider_name, resource, job=job, reason=reason, resource_id=resource_id))
    return {
        "ok": True,
        "dry_run": True,
        "provider": provider_name,
        "checked_at": checked_at,
        "resources_seen": len(resources),
        "candidates": candidates,
        "summary": summary,
        "guard": guard,
    }


def _generic_orphan_reaper(
    provider_name: str,
    *,
    apply: bool,
    principal: str,
    max_resources: int,
    mode_policy: str,
    store: JobStore,
    provider: Any | None,
    checked_at: int,
) -> dict[str, Any]:
    if mode_policy not in REAPER_MODES:
        return {
            "ok": False,
            "reaper_policy_version": "gpu-job-provider-orphan-reaper-policy-v1",
            "provider": provider_name,
            "mode_policy": mode_policy,
            "mode": "apply" if apply else "dry_run",
            "dry_run": not apply,
            "checked_at": checked_at,
            "error": "unsupported_reaper_mode_policy",
            "supported_mode_policies": sorted(REAPER_MODES),
            "inventory": None,
            "actions": [],
            "summary": {"candidate_count": 0, "eligible_count": 0, "destroyed_count": 0, "skipped_count": 0},
        }
    provider = provider or _provider_by_name(provider_name)
    inventory = _generic_orphan_inventory(provider_name, store=store, provider=provider, checked_at=checked_at)
    actions = [_generic_reaper_plan_for_candidate(item, provider_name=provider_name) for item in inventory["candidates"]]
    destroyed = 0
    skipped = 0
    limit = max(0, int(max_resources))
    for action in actions:
        if apply and action["eligible"] and destroyed >= limit:
            action["eligible"] = False
            action["would_destroy"] = False
            action["skip_reason"] = "max_resources_exceeded"
        if apply and action["eligible"]:
            _apply_generic_reaper_action(action, provider=provider, principal=principal)
            if isinstance(action.get("destroy_result"), dict) and action["destroy_result"].get("ok"):
                destroyed += 1
            else:
                skipped += 1
        elif not action["eligible"]:
            skipped += 1
    return {
        "ok": not apply or all(not item.get("eligible") or item.get("destroy_result", {}).get("ok") for item in actions),
        "reaper_policy_version": "gpu-job-provider-orphan-reaper-policy-v1",
        "provider": provider_name,
        "mode_policy": mode_policy,
        "mode": "apply" if apply else "dry_run",
        "dry_run": not apply,
        "checked_at": checked_at,
        "inventory": inventory,
        "actions": actions,
        "summary": {
            "candidate_count": len(actions),
            "eligible_count": sum(1 for item in actions if item.get("eligible")),
            "destroyed_count": destroyed,
            "skipped_count": skipped,
        },
    }


def _generic_candidate(provider_name: str, resource: Any, *, job: Any, reason: str, resource_id: str) -> dict[str, Any]:
    resource_type = str(resource.get("type") or "resource") if isinstance(resource, dict) else "resource"
    job_status = getattr(job, "status", None)
    return {
        "provider": provider_name,
        "resource_id": resource_id,
        "resource_type": resource_type,
        "reason": reason,
        "category": "zombie" if reason == "terminal_job_active_resource" else "unmatched_resource",
        "job_id": getattr(job, "job_id", ""),
        "job_status": job_status,
        "provider_job_id": getattr(job, "provider_job_id", ""),
        "resource": resource,
        "evidence": {
            "evidence_version": "gpu-job-provider-orphan-evidence-v1",
            "provider": provider_name,
            "resource_id": resource_id,
            "resource_type": resource_type,
            "job_exists": job is not None,
            "job_terminal": job_status in TERMINAL_JOB_STATUSES,
            "provider_job_id_exact_match": bool(job and resource_id and str(getattr(job, "provider_job_id", "")) == resource_id),
            "job_lifecycle": _job_lifecycle_evidence(job),
            "cleanup": _cleanup_evidence(job),
        },
        "would_destroy": False,
    }


def _generic_reaper_plan_for_candidate(candidate: dict[str, Any], *, provider_name: str) -> dict[str, Any]:
    action = {
        **candidate,
        "eligible": False,
        "would_destroy": False,
        "skip_reason": "",
        "preflight": None,
        "fresh_resource": None,
        "post_guard": None,
        "destroy_result": None,
    }
    if candidate.get("reason") != "terminal_job_active_resource":
        action["skip_reason"] = "report_only_reason"
        return action
    if provider_name == "runpod" and candidate.get("resource_type") in {"pod", "resource"}:
        if not candidate.get("resource_id") or not candidate.get("evidence", {}).get("provider_job_id_exact_match"):
            action["skip_reason"] = "provider_job_id_not_exact_match"
            return action
        if not candidate.get("evidence", {}).get("cleanup", {}).get("exit_seen"):
            action["skip_reason"] = "missing_cleanup_evidence"
            return action
        action["eligible"] = True
        action["would_destroy"] = True
        return action
    action["skip_reason"] = "provider_resource_type_not_destroyable_by_generic_reaper"
    return action


def _apply_generic_reaper_action(action: dict[str, Any], *, provider: Any, principal: str) -> None:
    if not principal:
        action["eligible"] = False
        action["would_destroy"] = False
        action["skip_reason"] = "principal_required"
        return
    provider_name = str(action.get("provider") or "")
    resource_id = str(action.get("resource_id") or "")
    resource_type = str(action.get("resource_type") or "")
    preflight = destructive_preflight(
        "destroy",
        principal,
        target=f"{provider_name}:{resource_type}:{resource_id}",
        scope="provider-orphan-reaper",
    )
    action["preflight"] = preflight
    if not preflight.get("ok"):
        action["eligible"] = False
        action["would_destroy"] = False
        action["skip_reason"] = "destructive_preflight_failed"
        return
    fresh = _fresh_billable_resource(provider, resource_id=resource_id, resource_type=resource_type)
    if isinstance(fresh, dict) and fresh.get("__cost_guard_error"):
        action["eligible"] = False
        action["would_destroy"] = False
        action["skip_reason"] = "fresh_resource_guard_failed"
        action["fresh_resource"] = fresh
        return
    if fresh is None:
        action["eligible"] = False
        action["would_destroy"] = False
        action["skip_reason"] = "fresh_resource_not_found"
        return
    action["fresh_resource"] = fresh
    if provider_name == "runpod" and hasattr(provider, "_terminate_pod"):
        action["destroy_result"] = provider._terminate_pod(resource_id)
        try:
            post_guard = provider.cost_guard() if hasattr(provider, "cost_guard") else {}
        except Exception as exc:
            post_guard = {"ok": False, "error": "cost_guard_failed", "reason": str(exc), "billable_resources": []}
        action["post_guard"] = post_guard
        if (
            post_guard.get("error") == "cost_guard_failed"
            and isinstance(action["destroy_result"], dict)
            and action["destroy_result"].get("ok")
        ):
            action["destroy_result"] = {
                **action["destroy_result"],
                "ok": False,
                "error": "post_destroy_cost_guard_failed",
                "reason": post_guard.get("reason") or "",
            }
            return
        residue = _fresh_billable_resource(provider, resource_id=resource_id, resource_type=resource_type, guard=post_guard)
        if residue and isinstance(action["destroy_result"], dict) and action["destroy_result"].get("ok"):
            action["destroy_result"] = {
                **action["destroy_result"],
                "ok": False,
                "error": "post_destroy_resource_still_billable",
                "residue": residue,
            }
        return
    action["eligible"] = False
    action["would_destroy"] = False
    action["skip_reason"] = "provider_destroy_not_supported"


def _fresh_billable_resource(
    provider: Any,
    *,
    resource_id: str,
    resource_type: str,
    guard: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not resource_id or provider is None or not hasattr(provider, "cost_guard"):
        return None
    try:
        current_guard = guard if isinstance(guard, dict) else provider.cost_guard()
    except Exception as exc:
        return {"__cost_guard_error": str(exc)}
    resources = current_guard.get("billable_resources") if isinstance(current_guard, dict) else []
    for resource in resources or []:
        if not isinstance(resource, dict):
            continue
        if _string_or_empty(resource.get("id")) != resource_id:
            continue
        current_type = str(resource.get("type") or "resource")
        if resource_type and current_type != resource_type:
            continue
        return dict(resource)
    return None


def _provider_by_name(provider_name: str) -> Any | None:
    try:
        from .providers import get_provider

        return get_provider(provider_name)
    except Exception:
        return None


def _apply_reaper_action(action: dict[str, Any], *, store: JobStore, provider: VastProvider, principal: str) -> None:
    if not principal:
        action["eligible"] = False
        action["would_destroy"] = False
        action["skip_reason"] = "principal_required"
        return
    instance_id = _string_or_empty(action.get("instance_id"))
    job_id = str(action.get("job_id") or "")
    label = f"gpu-job:{job_id}"
    preflight = destructive_preflight("destroy", principal, target=f"vast:instance:{instance_id}", scope="vast-orphan-reaper")
    action["preflight"] = preflight
    if not preflight.get("ok"):
        action["eligible"] = False
        action["would_destroy"] = False
        action["skip_reason"] = "destructive_preflight_failed"
        return

    fresh = [item for item in provider._instances_by_label(label) if _string_or_empty(item.get("id")) == instance_id]
    if len(fresh) != 1:
        action["eligible"] = False
        action["would_destroy"] = False
        action["skip_reason"] = "fresh_instance_not_found"
        return
    fresh_instance = fresh[0]
    action["fresh_instance"] = {
        "instance_id": instance_id,
        "label": str(fresh_instance.get("label") or ""),
        "actual_status": fresh_instance.get("actual_status"),
        "cur_state": fresh_instance.get("cur_state"),
        "duration": fresh_instance.get("duration"),
        "dph_total": fresh_instance.get("dph_total"),
    }
    if not _is_vast_instance_active(fresh_instance):
        action["eligible"] = False
        action["would_destroy"] = False
        action["skip_reason"] = "fresh_instance_inactive"
        return
    refreshed = vast_orphan_inventory(store=store, provider=provider, instances=[fresh_instance], checked_at=now_unix())
    refreshed_candidates = refreshed.get("candidates") or []
    if len(refreshed_candidates) != 1 or refreshed_candidates[0].get("reason") != "terminal_job_active_instance":
        action["eligible"] = False
        action["would_destroy"] = False
        action["skip_reason"] = "fresh_classification_changed"
        return

    action["destroy_result"] = provider.destroy_instance(instance_id)


def _candidate(
    item: dict[str, Any],
    *,
    job_id: str,
    reason: str,
    job: Any,
    instance_id: str,
    job_load_error: str = "",
) -> dict[str, Any]:
    cleanup = _cleanup_evidence(job)
    provider_job_id = getattr(job, "provider_job_id", None)
    job_status = getattr(job, "status", None)
    job_file_exists = job is not None or bool(job_load_error)
    provider_job_id_exact_match = bool(provider_job_id and instance_id and str(provider_job_id) == instance_id)
    provider_job_id_mismatch = bool(provider_job_id and instance_id and str(provider_job_id) != instance_id)
    return {
        "instance_id": instance_id,
        "label": str(item.get("label") or ""),
        "job_id": job_id,
        "reason": reason,
        "category": REASON_CATEGORY.get(reason, "unknown"),
        "job_status": job_status,
        "provider_job_id": provider_job_id,
        "actual_status": item.get("actual_status"),
        "cur_state": item.get("cur_state"),
        "duration": item.get("duration"),
        "dph_total": item.get("dph_total"),
        "evidence": {
            "evidence_version": "vast-orphan-evidence-v1",
            "category": REASON_CATEGORY.get(reason, "unknown"),
            "reason": reason,
            "job_load_error": job_load_error,
            "job_file_exists": job_file_exists,
            "job_exists": job is not None,
            "job_file_unreadable": bool(job_load_error),
            "job_terminal": job_status in TERMINAL_JOB_STATUSES,
            "provider_job_id_exact_match": provider_job_id_exact_match,
            "provider_job_id_mismatch": provider_job_id_mismatch,
            "provider_state": {
                "instance_id": instance_id,
                "label": str(item.get("label") or ""),
                "actual_status": item.get("actual_status"),
                "cur_state": item.get("cur_state"),
                "active": _is_vast_instance_active(item),
                "lifecycle_phase": _vast_lifecycle_phase(item),
            },
            "job_lifecycle": _job_lifecycle_evidence(job),
            "cleanup": cleanup,
        },
        "would_destroy": False,
    }


def _is_vast_instance_active(item: dict[str, Any]) -> bool:
    states = [
        str(item.get("actual_status") or "").strip().lower(),
        str(item.get("cur_state") or "").strip().lower(),
    ]
    populated = [state for state in states if state]
    if not populated:
        return True
    return not any(state in INACTIVE_VAST_STATES for state in populated)


def _vast_lifecycle_phase(item: dict[str, Any]) -> str:
    states = [
        str(item.get("actual_status") or "").strip().lower(),
        str(item.get("cur_state") or "").strip().lower(),
    ]
    populated = [state for state in states if state]
    if not populated:
        return "unknown"
    if any(state in VAST_TERMINAL_STATES for state in populated):
        return "terminal"
    if any(state in VAST_STOPPING_STATES for state in populated):
        return "stopping"
    if any(state in VAST_PROVISIONING_STATES for state in populated):
        return "provisioning"
    if any(state in VAST_RUNNING_STATES for state in populated):
        return "running"
    return "unknown"


def _string_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _has_cleanup_evidence(job: Any) -> bool:
    evidence = _cleanup_evidence(job)
    return bool(evidence.get("enter_seen") and evidence.get("exit_seen"))


def _cleanup_evidence(job: Any) -> dict[str, Any]:
    timing = getattr(job, "metadata", {}).get("timing_v2")
    if not isinstance(timing, dict):
        return {
            "enter_seen": False,
            "exit_seen": False,
            "exit_status": "",
            "exit_error_class": "",
            "event_count": 0,
        }
    events = timing.get("events")
    if not isinstance(events, list):
        return {
            "enter_seen": False,
            "exit_seen": False,
            "exit_status": "",
            "exit_error_class": "",
            "event_count": 0,
        }
    cleanup_events = [item for item in events if isinstance(item, dict) and str(item.get("phase") or "") == "cleaning_up"]
    exit_events = [item for item in cleanup_events if str(item.get("event") or "") == "exit"]
    last_exit = exit_events[-1] if exit_events else {}
    return {
        "enter_seen": any(str(item.get("event") or "") == "enter" for item in cleanup_events),
        "exit_seen": bool(exit_events),
        "exit_status": str(last_exit.get("status") or ""),
        "exit_error_class": str(last_exit.get("error_class") or ""),
        "event_count": len(cleanup_events),
    }


def _job_lifecycle_evidence(job: Any) -> dict[str, Any]:
    if job is None:
        return {
            "timing_present": False,
            "event_count": 0,
            "open_phases": [],
            "last_closed_phase": "",
            "terminal_phases": [],
            "cleaning_up_spans": [],
        }
    try:
        summary = timing_summary(job)
    except Exception:
        return {
            "timing_present": False,
            "event_count": 0,
            "open_phases": [],
            "last_closed_phase": "",
            "terminal_phases": [],
            "cleaning_up_spans": [],
        }
    phases = [item for item in summary.get("phases") or [] if isinstance(item, dict)]
    closed = [item for item in phases if not item.get("open")]
    last_closed = closed[-1] if closed else {}
    return {
        "timing_present": bool(summary.get("event_count")),
        "event_count": int(summary.get("event_count") or 0),
        "open_phases": [
            {"phase": str(item.get("phase") or ""), "attempt": int(item.get("attempt") or 1)} for item in phases if item.get("open")
        ],
        "last_closed_phase": str(last_closed.get("phase") or ""),
        "terminal_phases": [
            {"phase": str(item.get("phase") or ""), "attempt": int(item.get("attempt") or 1), "status": str(item.get("status") or "")}
            for item in closed
            if str(item.get("phase") or "") in TERMINAL_JOB_STATUSES
        ],
        "cleaning_up_spans": [
            {
                "attempt": int(item.get("attempt") or 1),
                "duration_seconds": item.get("duration_seconds"),
                "status": str(item.get("status") or ""),
                "error_class": str(item.get("error_class") or ""),
            }
            for item in closed
            if str(item.get("phase") or "") == "cleaning_up"
        ],
    }
