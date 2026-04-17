from __future__ import annotations

from typing import Any

from .capacity import queue_capacity
from .providers import PROVIDERS
from .resource import collect_resource_guard


def collect_cost_guard(provider_names: list[str] | None = None) -> dict[str, Any]:
    names = provider_names or sorted(PROVIDERS)
    guards = {name: PROVIDERS[name].cost_guard() for name in names}
    resource_guard = collect_resource_guard()
    ok = all(item.get("ok") for item in guards.values()) and bool(resource_guard.get("ok"))
    estimated = 0.0
    unknown_estimate = False
    for item in guards.values():
        value = item.get("estimated_hourly_usd")
        if value is None:
            unknown_estimate = True
        else:
            estimated += float(value)
    return {
        "ok": ok,
        "providers": guards,
        "estimated_hourly_usd": None if unknown_estimate and estimated == 0 else estimated,
        "capacity": queue_capacity(),
        "resource": resource_guard,
    }


def guard_summary(guard: dict[str, Any]) -> list[dict[str, Any]]:
    blocked = []
    for provider, result in guard.get("providers", {}).items():
        if result.get("ok"):
            continue
        blocked.append(
            {
                "provider": provider,
                "reason": result.get("reason", ""),
                "estimated_hourly_usd": result.get("estimated_hourly_usd"),
                "billable_resources": result.get("billable_resources", []),
            }
        )
    return blocked
