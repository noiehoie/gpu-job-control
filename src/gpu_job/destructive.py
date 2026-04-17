from __future__ import annotations

from typing import Any

from .authz import approval_ok, authorize


DESTRUCTIVE_VERSION = "gpu-job-destructive-v1"


def destructive_preflight(action: str, principal: str, target: str = "", scope: str = "") -> dict[str, Any]:
    authz = authorize(principal, "approve", scope=scope or target)
    approval = approval_ok(action, principal)
    ok = bool(authz.get("ok")) and bool(approval.get("ok"))
    return {
        "ok": ok,
        "destructive_version": DESTRUCTIVE_VERSION,
        "action": action,
        "principal": principal,
        "target": target,
        "scope": scope or target,
        "authorization": authz,
        "approval": approval,
        "reason": "authorized and approved" if ok else "destructive action blocked by authorization or approval gate",
    }
