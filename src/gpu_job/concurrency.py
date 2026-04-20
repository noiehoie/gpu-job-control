from __future__ import annotations

from typing import Any


def provider_profile_key(provider: str, gpu_profile: str) -> str:
    return f"{provider}:{gpu_profile or '*'}"


def provider_profile_limit(provider_limits: dict[str, Any], provider: str, gpu_profile: str, default: int = 1) -> int:
    raw = provider_limits.get(provider)
    if isinstance(raw, dict):
        value = raw.get(gpu_profile)
        if value is None:
            value = raw.get("*")
        if value is None:
            value = raw.get("default")
        if value is None:
            return default
    else:
        value = raw
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def flatten_provider_limits(provider_limits: dict[str, Any]) -> dict[str, int]:
    flattened: dict[str, int] = {}
    for provider, raw in provider_limits.items():
        provider_name = str(provider)
        if isinstance(raw, dict):
            for profile, value in raw.items():
                try:
                    flattened[provider_profile_key(provider_name, str(profile))] = max(0, int(value))
                except (TypeError, ValueError):
                    continue
        else:
            try:
                flattened[provider_name] = max(0, int(raw))
            except (TypeError, ValueError):
                continue
    return flattened
