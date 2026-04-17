from __future__ import annotations

from pathlib import Path
import os


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def config_path(env_name: str, default_name: str) -> Path:
    explicit = os.getenv(env_name, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    xdg_config = os.getenv("XDG_CONFIG_HOME", "").strip()
    if xdg_config:
        local = Path(xdg_config).expanduser() / "gpu-job-control" / default_name
        if local.exists():
            return local
    return project_root() / "config" / default_name
