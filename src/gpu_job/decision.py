from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .audit import append_audit
from .canonical import canonical_hash
from .circuit import provider_circuit_state
from .compliance import evaluate_compliance
from .models import Job
from .policy import load_execution_policy
from .policy_engine import validate_policy
from .provenance import evaluate_provenance
from .router import route_job
from .store import JobStore
from .telemetry import ensure_trace


DECISION_VERSION = "gpu-job-decision-v1"


def decision_dir(store: JobStore | None = None) -> Path:
    store = store or JobStore()
    store.ensure()
    path = store.root / "decisions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def decision_path(job_id: str, store: JobStore | None = None) -> Path:
    return decision_dir(store) / f"{job_id}.json"


def make_decision(
    job: Job, phase: str = "route", route_result: dict[str, Any] | None = None, store: JobStore | None = None
) -> dict[str, Any]:
    store = store or JobStore()
    ensure_trace(job.metadata)
    policy = load_execution_policy()
    policy_validation = validate_policy(policy)
    route_result = route_result or route_job(job)
    selected = str(route_result.get("selected_provider") or "")
    snapshot = {
        "decision_version": DECISION_VERSION,
        "phase": phase,
        "job": job.to_dict(),
        "route": route_result,
        "policy_hash": policy_validation["policy_hash"],
        "policy_validation": policy_validation,
        "provenance": evaluate_provenance(job),
        "compliance": evaluate_compliance(job),
        "circuit": provider_circuit_state(selected, store=store) if selected else {},
    }
    snapshot["decision_hash"] = canonical_hash(snapshot)["sha256"]
    path = decision_path(job.job_id, store)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    append_audit("decision.record", {"job_id": job.job_id, "phase": phase, "decision_hash": snapshot["decision_hash"]}, store=store)
    return snapshot


def load_decision(job_id: str, store: JobStore | None = None) -> dict[str, Any]:
    path = decision_path(job_id, store)
    if not path.is_file():
        return {"ok": False, "error": "decision not found", "job_id": job_id, "path": str(path)}
    data = json.loads(path.read_text())
    return {"ok": True, "path": str(path), "decision": data}


def replay_decision(job_id: str, store: JobStore | None = None) -> dict[str, Any]:
    loaded = load_decision(job_id, store)
    if not loaded.get("ok"):
        return loaded
    decision = loaded["decision"]
    job = Job.from_dict(decision["job"])
    original_selected = decision.get("route", {}).get("selected_provider")
    if "provider_decisions" in decision.get("route", {}):
        replay_route = route_job(job)
        replay_selected = replay_route.get("selected_provider")
    else:
        replay_selected = original_selected
    return {
        "ok": original_selected == replay_selected,
        "job_id": job_id,
        "original_selected_provider": original_selected,
        "replay_selected_provider": replay_selected,
    }


def replay_all_decisions(store: JobStore | None = None, limit: int = 1000) -> dict[str, Any]:
    store = store or JobStore()
    paths = sorted(decision_dir(store).glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]
    results = [replay_decision(path.stem, store=store) for path in paths]
    failures = [result for result in results if not result.get("ok")]
    return {
        "ok": not failures,
        "checked": len(results),
        "failed": len(failures),
        "failures": failures,
    }
