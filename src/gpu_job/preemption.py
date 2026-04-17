from __future__ import annotations

from typing import Any

from .manifest import verify_manifest
from .models import Job
from .store import JobStore


PREEMPTION_VERSION = "gpu-job-preemption-v1"


def preemption_check(job: Job, store: JobStore | None = None) -> dict[str, Any]:
    store = store or JobStore()
    policy = job.metadata.get("preemption")
    policy = policy if isinstance(policy, dict) else {}
    preemptible = bool(policy.get("preemptible"))
    checkpoint_rel = str(policy.get("checkpoint_artifact") or "")
    checkpoint_ok = False
    checkpoint_path = ""
    if checkpoint_rel:
        artifact_dir = store.artifact_dir(job.job_id)
        checkpoint_path = str(artifact_dir / checkpoint_rel)
        manifest = verify_manifest(artifact_dir)
        checkpoint_ok = manifest["ok"] and not any(item.get("path") == checkpoint_rel for item in manifest.get("missing", []))
    resume_allowed = preemptible and checkpoint_ok
    return {
        "ok": (not preemptible) or resume_allowed or bool(policy.get("allow_restart_from_beginning", False)),
        "preemption_version": PREEMPTION_VERSION,
        "preemptible": preemptible,
        "checkpoint_artifact": checkpoint_rel or None,
        "checkpoint_path": checkpoint_path or None,
        "checkpoint_ok": checkpoint_ok,
        "resume_allowed": resume_allowed,
    }
