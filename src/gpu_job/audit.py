from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .canonical import canonical_hash, canonical_json
from .models import now_unix
from .store import JobStore


AUDIT_CHAIN_VERSION = "gpu-job-audit-chain-v1"


def audit_path(store: JobStore | None = None) -> Path:
    store = store or JobStore()
    store.ensure()
    return store.logs_dir / "audit-chain.jsonl"


def _last_chain_hash(path: Path) -> str:
    if not path.is_file():
        return "0" * 64
    last = ""
    with path.open() as fh:
        for line in fh:
            if line.strip():
                last = line
    if not last:
        return "0" * 64
    try:
        return str(json.loads(last).get("audit_chain_hash") or "0" * 64)
    except json.JSONDecodeError:
        return "0" * 64


def append_audit(event_type: str, payload: dict[str, Any], store: JobStore | None = None) -> dict[str, Any]:
    store = store or JobStore()
    path = audit_path(store)
    previous = _last_chain_hash(path)
    record = {
        "audit_chain_version": AUDIT_CHAIN_VERSION,
        "event_type": event_type,
        "timestamp": now_unix(),
        "payload": payload,
        "previous_chain_hash": previous,
    }
    record_hash = canonical_hash(record)["sha256"]
    chain_hash = canonical_hash({"previous": previous, "record": record_hash})["sha256"]
    record["audit_record_hash"] = record_hash
    record["audit_chain_hash"] = chain_hash
    with path.open("a") as fh:
        fh.write(canonical_json(record) + "\n")
    return record


def verify_audit_chain(store: JobStore | None = None) -> dict[str, Any]:
    path = audit_path(store)
    previous = "0" * 64
    count = 0
    errors = []
    if not path.is_file():
        return {"ok": True, "path": str(path), "count": 0, "errors": []}
    with path.open() as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append({"line": lineno, "error": f"invalid json: {exc}"})
                continue
            expected_prev = record.get("previous_chain_hash")
            stored_record_hash = str(record.get("audit_record_hash") or "")
            stored_chain_hash = str(record.get("audit_chain_hash") or "")
            body = dict(record)
            body.pop("audit_record_hash", None)
            body.pop("audit_chain_hash", None)
            computed_record_hash = canonical_hash(body)["sha256"]
            computed_chain_hash = canonical_hash({"previous": previous, "record": computed_record_hash})["sha256"]
            if expected_prev != previous:
                errors.append({"line": lineno, "error": "previous_chain_hash mismatch"})
            if stored_record_hash != computed_record_hash:
                errors.append({"line": lineno, "error": "audit_record_hash mismatch"})
            if stored_chain_hash != computed_chain_hash:
                errors.append({"line": lineno, "error": "audit_chain_hash mismatch"})
            previous = stored_chain_hash
            count += 1
    return {"ok": not errors, "path": str(path), "count": count, "errors": errors}
