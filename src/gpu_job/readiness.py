from __future__ import annotations

from typing import Any

from .audit import verify_audit_chain
from .circuit import all_circuits
from .decision import replay_all_decisions
from .dlq import dlq_status
from .drain import drain_status
from .guard import collect_cost_guard
from .metrics_export import metrics_snapshot
from .policy_engine import policy_activation_record
from .provider_stability import provider_stability_report
from .queue import queue_status
from .reconcile import reconcile_detect_only
from .retention import retention_report
from .wal import wal_recovery_status


READINESS_VERSION = "gpu-job-readiness-v1"


def launch_readiness(limit: int = 100) -> dict[str, Any]:
    guard = collect_cost_guard()
    policy = policy_activation_record()
    audit = verify_audit_chain()
    wal = wal_recovery_status()
    circuits = all_circuits()
    reconcile = reconcile_detect_only()
    replay = replay_all_decisions(limit=limit)
    dlq = dlq_status(limit=limit)
    queue = queue_status(limit=limit, compact=True)
    drain = drain_status()
    retention = retention_report()
    metrics = metrics_snapshot()
    provider_stability = provider_stability_report()
    checks = {
        "billing_guard": bool(guard.get("ok")),
        "policy": bool(policy.get("ok")),
        "audit_chain": bool(audit.get("ok")),
        "wal_recovery": bool(wal.get("ok")),
        "circuits": bool(circuits.get("ok")),
        "reconcile_detect_only": bool(reconcile.get("ok")),
        "decision_replay": bool(replay.get("ok")),
        "drain_state": bool(drain.get("ok")),
        "retention_report": bool(retention.get("ok")),
        "metrics": bool(metrics.get("ok")),
        "provider_stability": bool(provider_stability.get("ok")),
    }
    ok = all(checks.values())
    return {
        "ok": ok,
        "readiness_version": READINESS_VERSION,
        "checks": checks,
        "guard_summary": _guard_summary(guard),
        "policy": _compact(policy),
        "audit": _compact(audit),
        "wal_recovery": _compact(wal),
        "circuits": _compact(circuits),
        "reconcile": _compact(reconcile),
        "decision_replay": _compact(replay),
        "dlq": {"ok": dlq.get("ok"), "count": dlq.get("count")},
        "drain": drain,
        "retention": _compact(retention),
        "metrics": _compact(metrics),
        "provider_stability": _compact(provider_stability),
        "queue": {"ok": queue.get("ok"), "counts": queue.get("counts"), "capacity": queue.get("capacity")},
    }


def _compact(value: dict[str, Any]) -> dict[str, Any]:
    out = dict(value)
    for key in ("jobs", "records", "approvals"):
        out.pop(key, None)
    return out


def _guard_summary(guard: dict[str, Any]) -> dict[str, Any]:
    providers = guard.get("providers", {}) if isinstance(guard.get("providers"), dict) else {}
    return {
        "ok": guard.get("ok"),
        "estimated_hourly_usd": guard.get("estimated_hourly_usd"),
        "providers": {
            name: {
                "ok": item.get("ok"),
                "reason": item.get("reason"),
                "estimated_hourly_usd": item.get("estimated_hourly_usd"),
                "billable_count": len(item.get("billable_resources") or []),
            }
            for name, item in providers.items()
            if isinstance(item, dict)
        },
    }
