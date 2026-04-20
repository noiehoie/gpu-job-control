from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .config import config_path


def default_execution_policy_path() -> Path:
    return config_path("GPU_JOB_EXECUTION_POLICY", "execution-policy.json")


def default_provider_operations_policy_path() -> Path:
    explicit = config_path("GPU_JOB_PROVIDER_OPERATIONS_POLICY", "provider-operations.json")
    local = explicit.parent / "provider-operations.local.json"
    if local.exists() and not explicit.exists():
        return local
    return explicit


def load_execution_policy(path: Path | None = None) -> dict[str, Any]:
    policy_path = path or default_execution_policy_path()
    if not policy_path.exists():
        policy = {
            "stale_seconds": {"starting": 900, "running": 14400},
            "provider_limits": {
                "local": {"*": 1},
                "modal": {"*": 1},
                "ollama": {"*": 1},
                "runpod": {"*": 1},
                "vast": {"*": 1},
            },
        }
    else:
        policy = json.loads(policy_path.read_text())
    if path is not None:
        return policy
    return _merge_policy(policy, load_provider_operations_policy())


def load_provider_operations_policy(path: Path | None = None) -> dict[str, Any]:
    policy_path = path or default_provider_operations_policy_path()
    if not policy_path.exists():
        return {}
    return json.loads(policy_path.read_text())


def _merge_policy(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_policy(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged
