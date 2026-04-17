from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .capacity import ACTIVE_STATUSES
from .models import now_unix
from .store import JobStore


DRAIN_VERSION = "gpu-job-drain-v1"


def drain_path(store: JobStore | None = None) -> Path:
    store = store or JobStore()
    store.ensure()
    return store.root / "drain.json"


def start_drain(reason: str = "", store: JobStore | None = None) -> dict[str, Any]:
    data = {"drain_version": DRAIN_VERSION, "draining": True, "reason": reason, "started_at": now_unix()}
    drain_path(store).write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"ok": True, "drain": data}


def clear_drain(store: JobStore | None = None) -> dict[str, Any]:
    path = drain_path(store)
    if path.exists():
        path.unlink()
    return {"ok": True, "draining": False}


def drain_status(store: JobStore | None = None) -> dict[str, Any]:
    store = store or JobStore()
    path = drain_path(store)
    data = {"drain_version": DRAIN_VERSION, "draining": False}
    if path.is_file():
        data = json.loads(path.read_text())
    active = [job.to_dict() for job in store.list_jobs(limit=1000) if job.status in ACTIVE_STATUSES]
    return {"ok": True, "drain": data, "active_count": len(active), "drain_complete": bool(data.get("draining")) and not active}
