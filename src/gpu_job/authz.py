from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import uuid

from .audit import append_audit
from .models import now_unix
from .store import JobStore

AUTHZ_VERSION = "gpu-job-authz-v1"
DESTRUCTIVE_ACTIONS = {"purge", "delete", "terminate", "destroy", "cancel_provider_job", "policy_relax", "budget_increase"}


def authorize(principal: str, action: str, scope: str = "", policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or {}
    roles = dict(policy.get("roles", {}))
    role = str(roles.get(principal) or "anonymous")
    allowed = set(policy.get("role_actions", {}).get(role, []))
    if not allowed and role == "operator":
        allowed = {"read", "submit", "cancel", "approve"}
    ok = action in allowed or "*" in allowed
    return {
        "ok": ok,
        "authz_version": AUTHZ_VERSION,
        "principal": principal,
        "role": role,
        "action": action,
        "scope": scope,
    }


def approval_required(action: str) -> bool:
    return action in DESTRUCTIVE_ACTIONS


def approvals_path(store: JobStore | None = None) -> Path:
    store = store or JobStore()
    store.ensure()
    return store.logs_dir / "approvals.jsonl"


def approval_record(action: str, principal: str, approved: bool, expires_at: int | None = None, reason: str = "") -> dict[str, Any]:
    state = "approved" if approved else "denied"
    return {
        "approval_version": AUTHZ_VERSION,
        "approval_id": uuid.uuid4().hex,
        "action": action,
        "principal": principal,
        "approval_state": state,
        "approval_expires_at": expires_at,
        "approval_created_at": now_unix(),
        "reason": reason,
    }


def save_approval(record: dict[str, Any], store: JobStore | None = None) -> dict[str, Any]:
    store = store or JobStore()
    with approvals_path(store).open("a") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    append_audit("approval.record", record, store=store)
    return {"ok": True, "approval": record, "path": str(approvals_path(store))}


def list_approvals(store: JobStore | None = None, limit: int = 100) -> dict[str, Any]:
    path = approvals_path(store)
    records: list[dict[str, Any]] = []
    if path.is_file():
        lines = [line for line in path.read_text().splitlines() if line.strip()]
        for line in lines[-limit:]:
            records.append(json.loads(line))
    return {"ok": True, "path": str(path), "count": len(records), "approvals": records}


def approval_ok(action: str, principal: str, store: JobStore | None = None, now: int | None = None) -> dict[str, Any]:
    if not approval_required(action):
        return {"ok": True, "required": False, "action": action, "principal": principal}
    now = now or now_unix()
    approvals = list_approvals(store=store, limit=1000)["approvals"]
    for record in reversed(approvals):
        if str(record.get("action")) != action:
            continue
        if str(record.get("principal")) != principal:
            continue
        if record.get("approval_state") != "approved":
            continue
        expires_at = record.get("approval_expires_at")
        if expires_at is not None and int(expires_at) < now:
            continue
        return {"ok": True, "required": True, "action": action, "principal": principal, "approval": record}
    return {"ok": False, "required": True, "action": action, "principal": principal, "error": "valid approval record not found"}
