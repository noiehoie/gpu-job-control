from __future__ import annotations

from typing import Any
import time
import re

from .models import Job


TIMING_VERSION = "gpu-job-timing-v2"
PHASES = {
    "received",
    "validated",
    "planned",
    "reserving_workspace",
    "image_materialization",
    "staging_input",
    "starting_worker",
    "running_worker",
    "collecting_artifacts",
    "verifying",
    "cleaning_up",
    "succeeded",
    "failed",
    "cancelled",
}
EVENTS = {"enter", "exit", "instant"}
TERMINAL_PHASES = {"succeeded", "failed", "cancelled"}
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def now_seconds() -> float:
    return round(time.time(), 3)


def ensure_timing(job: Job) -> dict[str, Any]:
    timing = job.metadata.get("timing_v2")
    if not isinstance(timing, dict) or timing.get("version") != TIMING_VERSION:
        timing = {
            "version": TIMING_VERSION,
            "clock": "unix_time_seconds",
            "append_only": True,
            "events": [],
        }
        job.metadata["timing_v2"] = timing
    timing.setdefault("events", [])
    return timing


def mark_phase(
    job: Job,
    phase: str,
    event: str = "instant",
    *,
    at: float | None = None,
    attempt: int = 1,
    provider: str = "",
    status: str = "",
    error_class: str = "",
) -> dict[str, Any]:
    if phase not in PHASES:
        raise ValueError(f"invalid timing phase: {phase}")
    if event not in EVENTS:
        raise ValueError(f"invalid timing event: {event}")
    timing = ensure_timing(job)
    seq = len(timing.get("events") or []) + 1
    row: dict[str, Any] = {
        "event_id": f"{seq:012d}",
        "seq": seq,
        "phase": phase,
        "event": event,
        "at": float(now_seconds() if at is None else at),
        "attempt": int(attempt or 1),
    }
    if provider:
        row["provider"] = _safe_token(provider)
    if status:
        row["status"] = _safe_token(status)
    if error_class:
        row["error_class"] = _safe_token(error_class)
    timing["events"].append(row)
    return row


def enter_phase(job: Job, phase: str, **kwargs: Any) -> dict[str, Any]:
    return mark_phase(job, phase, "enter", **kwargs)


def exit_phase(job: Job, phase: str, **kwargs: Any) -> dict[str, Any]:
    return mark_phase(job, phase, "exit", **kwargs)


def instant_phase(job: Job, phase: str, **kwargs: Any) -> dict[str, Any]:
    return mark_phase(job, phase, "instant", **kwargs)


def ensure_received(job: Job) -> None:
    timing = ensure_timing(job)
    if not any(row.get("phase") == "received" for row in timing.get("events") or []):
        instant_phase(job, "received", at=float(job.created_at))


def terminal_phase_for_status(status: str) -> str:
    value = str(status or "")
    return value if value in TERMINAL_PHASES else "failed"


def _safe_token(value: Any) -> str:
    token = str(value or "")
    return token if SAFE_TOKEN_RE.fullmatch(token) else "other"


def timing_summary(job: Job) -> dict[str, Any]:
    timing = ensure_timing(job)
    events = sorted(
        list(timing.get("events") or []),
        key=lambda row: (int(row.get("seq") or 0), str(row.get("event_id") or "")),
    )
    open_events: dict[tuple[int, str], list[dict[str, Any]]] = {}
    phases: list[dict[str, Any]] = []
    totals: dict[str, float] = {}
    first_at: float | None = None
    last_at: float | None = None

    for row in events:
        at = float(row.get("at") or 0)
        first_at = at if first_at is None else min(first_at, at)
        last_at = at if last_at is None else max(last_at, at)
        phase = str(row.get("phase") or "")
        attempt = int(row.get("attempt") or 1)
        key = (attempt, phase)
        event = str(row.get("event") or "")
        if event == "enter":
            open_events.setdefault(key, []).append(row)
        elif event == "exit":
            start = open_events.get(key, []).pop() if open_events.get(key) else None
            started_at = float(start.get("at") if start else at)
            ended_at = at
            duration = max(0.0, round(ended_at - started_at, 3))
            item = {
                "phase": phase,
                "attempt": attempt,
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_seconds": duration,
            }
            if row.get("provider") or (start or {}).get("provider"):
                item["provider"] = row.get("provider") or (start or {}).get("provider")
            if row.get("status"):
                item["status"] = row.get("status")
            if row.get("error_class"):
                item["error_class"] = row.get("error_class")
            phases.append(item)
            totals[phase] = round(totals.get(phase, 0.0) + duration, 3)
        elif event == "instant":
            item = {
                "phase": phase,
                "attempt": attempt,
                "started_at": at,
                "ended_at": at,
                "duration_seconds": 0.0,
            }
            if row.get("provider"):
                item["provider"] = row.get("provider")
            if row.get("status"):
                item["status"] = row.get("status")
            if row.get("error_class"):
                item["error_class"] = row.get("error_class")
            phases.append(item)
            totals.setdefault(phase, 0.0)

    for key, pending in sorted(open_events.items()):
        for row in pending:
            at = float(row.get("at") or 0)
            item = {
                "phase": key[1],
                "attempt": key[0],
                "started_at": at,
                "ended_at": None,
                "duration_seconds": None,
                "open": True,
            }
            if row.get("provider"):
                item["provider"] = row.get("provider")
            phases.append(item)

    elapsed = round((last_at - first_at), 3) if first_at is not None and last_at is not None else 0.0
    measured = round(sum(value for value in totals.values()), 3)
    return {
        "version": TIMING_VERSION,
        "clock": timing.get("clock", "unix_time_seconds"),
        "event_count": len(events),
        "started_at": first_at,
        "ended_at": last_at,
        "elapsed_seconds": elapsed,
        "measured_phase_seconds": measured,
        "unknown_gap_seconds": round(max(0.0, elapsed - measured), 3),
        "phase_totals": dict(sorted(totals.items())),
        "phases": sorted(
            phases, key=lambda row: (float(row.get("started_at") or 0), int(row.get("attempt") or 1), str(row.get("phase") or ""))
        ),
    }


def public_timing(job: Job) -> dict[str, Any]:
    timing = ensure_timing(job)
    return {
        "summary": timing_summary(job),
        "events": list(timing.get("events") or []),
    }
