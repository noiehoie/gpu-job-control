from __future__ import annotations

from typing import Any
import hashlib
import json

from .models import now_unix


PLAN_QUOTE_VERSION = "gpu-job-plan-quote-v1"


def quote_hash(payload: dict[str, Any]) -> str:
    stable = _stable_quote_payload(payload)
    blob = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def build_plan_quote(plan: dict[str, Any]) -> dict[str, Any]:
    basis = {
        "quote_version": PLAN_QUOTE_VERSION,
        "contract_version": plan.get("contract_version"),
        "request": plan.get("request"),
        "catalog_version": plan.get("catalog_version"),
        "catalog_snapshot_id": plan.get("catalog_snapshot_id"),
        "gpu_profile": plan.get("gpu_profile"),
        "selected_option": plan.get("selected_option"),
        "options": plan.get("options") or [],
        "refusals": plan.get("refusals") or [],
        "estimate": plan.get("estimate") or {},
        "approval": plan.get("approval") or {},
        "can_run_now": bool(plan.get("can_run_now")),
        "action_requirements": plan.get("action_requirements") or {},
    }
    digest = quote_hash(basis)
    selected = basis.get("selected_option") if isinstance(basis.get("selected_option"), dict) else {}
    quote = {
        **basis,
        "quote_id": f"quote-{digest[:16]}",
        "quote_hash": digest,
        "created_at": int(plan.get("created_at") or now_unix()),
        "explanation": _quote_explanation(basis, selected),
    }
    return quote


def plan_quote_schema() -> dict[str, Any]:
    return {
        "quote_version": PLAN_QUOTE_VERSION,
        "required_fields": [
            "quote_id",
            "quote_hash",
            "request",
            "catalog_snapshot_id",
            "selected_option",
            "options",
            "refusals",
            "estimate",
            "approval",
            "can_run_now",
            "action_requirements",
            "explanation",
        ],
        "invariants": [
            "quote_hash is computed from request, catalog snapshot id, options, refusals, estimate, approval, and action requirements",
            "created_at is excluded from quote_hash",
            "routing explanation is deterministic and contains no raw provider logs or secrets",
        ],
        "approval_decisions": [
            "auto_execute",
            "pending_approval",
            "requires_action",
            "requires_backend_registration",
            "unsupported",
            "reject",
        ],
        "requires_action": {
            "meaning": "caller-visible prerequisite must be satisfied before execution",
            "required_action_types": ["build_image", "run_contract_probe", "provide_secret", "register_backend", "approve_cost"],
            "execution_rule": "can_run_now must be false when approval.decision is requires_action",
        },
    }


def _stable_quote_payload(payload: dict[str, Any]) -> dict[str, Any]:
    stable = dict(payload)
    stable.pop("created_at", None)
    stable.pop("quote_id", None)
    stable.pop("quote_hash", None)
    stable.pop("explanation", None)
    return stable


def _quote_explanation(basis: dict[str, Any], selected: dict[str, Any]) -> dict[str, Any]:
    approval = basis.get("approval") if isinstance(basis.get("approval"), dict) else {}
    if not selected:
        return {
            "decision": str(approval.get("decision") or "reject"),
            "reason": str(approval.get("reason") or "no selectable provider option"),
            "selected_provider": "",
            "selected_gpu_profile": str(basis.get("gpu_profile") or ""),
            "selection_rule": "no eligible provider after support, budget, and requirement filters",
        }
    return {
        "decision": str(approval.get("decision") or ""),
        "reason": str(approval.get("reason") or "selected lowest p95 cost among supported and allowed options"),
        "selected_provider": str(selected.get("provider") or ""),
        "selected_gpu_profile": str(selected.get("gpu_profile") or basis.get("gpu_profile") or ""),
        "selection_rule": "sort by estimated_total_cost_usd_p95, estimated_total_seconds_p95, provider",
        "estimated_seconds_p50": selected.get("estimated_total_seconds_p50"),
        "estimated_seconds_p95": selected.get("estimated_total_seconds_p95"),
        "estimated_cost_usd_p50": selected.get("estimated_total_cost_usd_p50"),
        "estimated_cost_usd_p95": selected.get("estimated_total_cost_usd_p95"),
    }
