from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .config import config_path


def default_execution_policy_path() -> Path:
    return config_path("GPU_JOB_EXECUTION_POLICY", "execution-policy.json")


def load_execution_policy(path: Path | None = None) -> dict[str, Any]:
    policy_path = path or default_execution_policy_path()
    if not policy_path.exists():
        return {
            "stale_seconds": {"starting": 900, "running": 14400},
            "provider_limits": {"local": 1, "modal": 1, "runpod": 1, "vast": 1},
        }
    return json.loads(policy_path.read_text())
