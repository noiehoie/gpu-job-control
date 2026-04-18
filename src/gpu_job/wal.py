from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import uuid

from .audit import append_audit
from .canonical import canonical_json
from .models import Job, now_unix
from .store import JobStore


WAL_VERSION = "gpu-job-wal-v1"


def wal_path(store: JobStore | None = None) -> Path:
    store = store or JobStore()
    store.ensure()
    return store.logs_dir / "wal.jsonl"


def append_wal(
    job: Job, transition: str, intent: str, extra: dict[str, Any] | None = None, store: JobStore | None = None
) -> dict[str, Any]:
    store = store or JobStore()
    record = {
        "wal_version": WAL_VERSION,
        "tx_id": uuid.uuid4().hex,
        "timestamp": now_unix(),
        "job_id": job.job_id,
        "status": job.status,
        "transition": transition,
        "intent": intent,
        "extra": extra or {},
    }
    with wal_path(store).open("a") as fh:
        fh.write(canonical_json(record) + "\n")
    append_audit("wal.append", record, store=store)
    return record


def wal_status(store: JobStore | None = None, limit: int = 100) -> dict[str, Any]:
    path = wal_path(store)
    records = []
    if path.is_file():
        lines = [line for line in path.read_text().splitlines() if line.strip()]
        for line in lines[-limit:]:
            records.append(line)
    return {"ok": True, "path": str(path), "count_returned": len(records), "records": records}


def _read_wal_records(store: JobStore | None = None) -> list[dict[str, Any]]:
    path = wal_path(store)
    records: list[dict[str, Any]] = []
    if not path.is_file():
        return records
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            records.append(
                {
                    "line_number": line_number,
                    "parse_error": str(exc),
                    "raw": line,
                }
            )
            continue
        record["line_number"] = line_number
        records.append(record)
    return records


def wal_recovery_status(store: JobStore | None = None) -> dict[str, Any]:
    store = store or JobStore()
    records = _read_wal_records(store)
    parse_errors = [record for record in records if record.get("parse_error")]
    provider_submit: dict[str, dict[str, Any]] = {}
    provider_final: set[str] = set()
    for record in records:
        job_id = str(record.get("job_id") or "")
        if not job_id:
            continue
        intent = str(record.get("intent") or "")
        if intent == "provider_submit":
            provider_submit[job_id] = record
        elif intent == "provider_submit_final":
            provider_final.add(job_id)
    ambiguous = []
    resolved = []
    for job_id, record in sorted(provider_submit.items()):
        if job_id in provider_final:
            continue
        resolution = _terminal_job_resolution(job_id, store)
        if resolution:
            resolved.append({**resolution, "line_number": record.get("line_number"), "timestamp": record.get("timestamp")})
            continue
        ambiguous.append(
            {
                "job_id": job_id,
                "line_number": record.get("line_number"),
                "timestamp": record.get("timestamp"),
                "provider": record.get("extra", {}).get("provider") if isinstance(record.get("extra"), dict) else "",
                "execute": record.get("extra", {}).get("execute") if isinstance(record.get("extra"), dict) else None,
                "recovery_action": "inspect provider-side state before retry or purge",
            }
        )
    return {
        "ok": not parse_errors and not ambiguous,
        "wal_version": WAL_VERSION,
        "record_count": len(records),
        "parse_errors": parse_errors,
        "ambiguous_provider_submits": ambiguous,
        "ambiguous_count": len(ambiguous),
        "resolved_terminal_provider_submits": resolved,
        "resolved_terminal_count": len(resolved),
    }


def _terminal_job_resolution(job_id: str, store: JobStore) -> dict[str, Any] | None:
    try:
        job = store.load(job_id)
    except Exception:
        return None
    if job.status not in {"succeeded", "failed", "cancelled"}:
        return None
    return {
        "job_id": job_id,
        "status": job.status,
        "provider": job.provider or job.metadata.get("selected_provider") or job.metadata.get("requested_provider"),
        "finished_at": job.finished_at,
        "exit_code": job.exit_code,
        "resolution": "job already terminal; provider_submit ambiguity resolved by durable job state",
    }


def wal_recovery_plan(store: JobStore | None = None) -> dict[str, Any]:
    status = wal_recovery_status(store=store)
    plans = []
    for item in status.get("ambiguous_provider_submits", []):
        provider = str(item.get("provider") or "")
        job_id = str(item.get("job_id") or "")
        plans.append(
            {
                "job_id": job_id,
                "provider": provider,
                "safe_automatic_action": "block_new_dispatch",
                "inspection_action": "query provider-side job/resource by provider_job_id or gpu-job label before retry",
                "destructive_action": "cancel_provider_job",
                "destructive_requires_approval": True,
                "retry_allowed_before_inspection": False,
            }
        )
    return {
        "ok": status["ok"],
        "wal_version": WAL_VERSION,
        "status": status,
        "plans": plans,
    }
