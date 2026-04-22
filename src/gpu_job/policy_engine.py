from __future__ import annotations

from typing import Any

from .canonical import canonical_hash
from .concurrency import provider_profile_key
from .models import now_unix
from .policy import load_execution_policy


POLICY_ENGINE_VERSION = "gpu-job-policy-v1"


def validate_policy(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_execution_policy()
    errors = []
    provider_limits = policy.get("provider_limits")
    if not isinstance(provider_limits, dict) or not provider_limits:
        errors.append("provider_limits must be a non-empty object")
    else:
        for provider, limit in provider_limits.items():
            if isinstance(limit, dict):
                if not limit:
                    errors.append(f"provider_limits.{provider} must not be empty")
                    continue
                for profile, profile_limit in limit.items():
                    key = provider_profile_key(str(provider), str(profile))
                    try:
                        if int(profile_limit) < 0:
                            errors.append(f"provider_limits.{key} must be >= 0")
                    except (TypeError, ValueError):
                        errors.append(f"provider_limits.{key} must be integer-like")
            else:
                try:
                    if int(limit) < 0:
                        errors.append(f"provider_limits.{provider} must be >= 0")
                except (TypeError, ValueError):
                    errors.append(f"provider_limits.{provider} must be integer-like")
    stale = policy.get("stale_seconds", {})
    if not isinstance(stale, dict):
        errors.append("stale_seconds must be an object")
    for key in ["resource_guard", "persistent_storage"]:
        if key in policy and not isinstance(policy[key], dict):
            errors.append(f"{key} must be an object")
    errors.extend(_validate_provider_module_routing(policy))
    exception_result = validate_policy_exceptions(policy)
    errors.extend(exception_result["errors"])
    return {
        "ok": not errors,
        "policy_engine_version": POLICY_ENGINE_VERSION,
        "policy_hash": canonical_hash(policy)["sha256"],
        "errors": errors,
        "exceptions": exception_result,
    }


def policy_activation_record(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    validation = validate_policy(policy)
    return {
        **validation,
        "policy_activation_allowed": bool(validation["ok"]),
        "conflict_check_result": "pass" if validation["ok"] else "fail",
    }


def validate_policy_exceptions(policy: dict[str, Any]) -> dict[str, Any]:
    now = now_unix()
    exceptions = policy.get("policy_exceptions", [])
    if not isinstance(exceptions, list):
        return {"ok": False, "errors": ["policy_exceptions must be a list"], "active": [], "expired": []}
    errors = []
    active = []
    expired = []
    for index, item in enumerate(exceptions):
        if not isinstance(item, dict):
            errors.append(f"policy_exceptions[{index}] must be an object")
            continue
        exception_id = str(item.get("id") or f"index-{index}")
        expires_at = int(item.get("expires_at") or 0)
        approval_id = str(item.get("approval_id") or "")
        if not expires_at:
            errors.append(f"policy_exceptions[{index}].expires_at is required")
        if not approval_id:
            errors.append(f"policy_exceptions[{index}].approval_id is required")
        row = {"id": exception_id, "expires_at": expires_at, "approval_id": approval_id, "scope": item.get("scope")}
        if expires_at and expires_at < now:
            expired.append(row)
            errors.append(f"policy exception expired: {exception_id}")
        else:
            active.append(row)
    return {"ok": not errors, "errors": errors, "active": active, "expired": expired}


def _validate_provider_module_routing(policy: dict[str, Any]) -> list[str]:
    if "provider_module_routing" not in policy:
        return []
    routing = policy.get("provider_module_routing")
    if not isinstance(routing, dict):
        return ["provider_module_routing must be an object"]
    errors = []
    enabled = routing.get("routing_by_module_enabled", False)
    if enabled is not False:
        errors.append("provider_module_routing.routing_by_module_enabled must remain false until module routing is implemented")
    canary_required = routing.get("canary_evidence_required", True)
    if not isinstance(canary_required, bool):
        errors.append("provider_module_routing.canary_evidence_required must be boolean")
    return errors
