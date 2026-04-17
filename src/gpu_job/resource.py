from __future__ import annotations

from pathlib import Path
from subprocess import run
from typing import Any
from urllib import request
import json
import os

from .policy import load_execution_policy


def collect_resource_guard(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_execution_policy()
    limits = dict(policy.get("resource_guard", {}))
    host = _host_snapshot()
    ollama = _ollama_snapshot()
    checks = _checks(host, ollama, limits)
    ok = all(item["ok"] for item in checks)
    return {
        "ok": ok,
        "reason": "resource guard ok" if ok else "; ".join(item["reason"] for item in checks if not item["ok"]),
        "checks": checks,
        "limits": limits,
        "host": host,
        "ollama": ollama,
    }


def ollama_resource_ok(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    guard = collect_resource_guard(policy)
    return {
        "ok": bool(guard.get("ok")),
        "reason": guard.get("reason", ""),
        "host": guard.get("host", {}),
        "ollama": guard.get("ollama", {}),
        "checks": guard.get("checks", []),
    }


def _host_snapshot() -> dict[str, Any]:
    mem = _meminfo()
    statvfs = os.statvfs("/")
    total = statvfs.f_blocks * statvfs.f_frsize
    available = statvfs.f_bavail * statvfs.f_frsize
    used = total - available
    load1, load5, load15 = os.getloadavg()
    cpu_count = os.cpu_count() or 1
    return {
        "load_1m": round(load1, 3),
        "load_5m": round(load5, 3),
        "load_15m": round(load15, 3),
        "cpu_count": cpu_count,
        "load_5m_per_cpu": round(load5 / cpu_count, 3),
        "mem_total_gb": _kb_to_gb(mem["MemTotal"]) if "MemTotal" in mem else None,
        "mem_available_gb": _kb_to_gb(mem["MemAvailable"]) if "MemAvailable" in mem else None,
        "swap_total_gb": _kb_to_gb(mem["SwapTotal"]) if "SwapTotal" in mem else None,
        "swap_free_gb": _kb_to_gb(mem["SwapFree"]) if "SwapFree" in mem else None,
        "swap_used_gb": _kb_to_gb(mem["SwapTotal"] - mem["SwapFree"]) if "SwapTotal" in mem and "SwapFree" in mem else None,
        "disk_root_total_gb": round(total / 1024**3, 3),
        "disk_root_available_gb": round(available / 1024**3, 3),
        "disk_root_used_percent": round((used / total) * 100, 3) if total else 0,
    }


def _ollama_snapshot() -> dict[str, Any]:
    service = _systemctl_show("ollama.service")
    api_ps = _ollama_api("/api/ps")
    models = []
    for item in api_ps.get("models", []) if isinstance(api_ps, dict) else []:
        models.append(
            {
                "name": item.get("name"),
                "size_gb": round(float(item.get("size") or 0) / 1024**3, 3),
                "size_vram_gb": round(float(item.get("size_vram") or 0) / 1024**3, 3),
                "context_length": item.get("context_length"),
                "expires_at": item.get("expires_at"),
            }
        )
    memory_current = _systemd_bytes(service.get("MemoryCurrent"))
    memory_peak = _systemd_bytes(service.get("MemoryPeak"))
    return {
        "service_active_state": service.get("ActiveState", ""),
        "service_sub_state": service.get("SubState", ""),
        "memory_current_gb": round(memory_current / 1024**3, 3) if memory_current is not None else None,
        "memory_peak_gb": round(memory_peak / 1024**3, 3) if memory_peak is not None else None,
        "tasks_current": _int_or_none(service.get("TasksCurrent")),
        "loaded_models": models,
        "loaded_model_count": len(models),
    }


def _checks(host: dict[str, Any], ollama: dict[str, Any], limits: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _min_check("mem_available_gb", host.get("mem_available_gb"), limits.get("min_mem_available_gb", 16)),
        _max_check("swap_used_gb", host.get("swap_used_gb"), limits.get("max_swap_used_gb", 2)),
        _max_check("disk_root_used_percent", host.get("disk_root_used_percent"), limits.get("max_disk_root_used_percent", 90)),
        _max_check("load_5m_per_cpu", host.get("load_5m_per_cpu"), limits.get("max_load_5m_per_cpu", 1.2)),
        _max_check("ollama_memory_current_gb", ollama.get("memory_current_gb"), limits.get("max_ollama_memory_current_gb", 112)),
        _max_check("ollama_memory_peak_gb", ollama.get("memory_peak_gb"), limits.get("max_ollama_memory_peak_gb", 124)),
    ]


def _min_check(name: str, value: Any, limit: Any) -> dict[str, Any]:
    value_f = _float_or_none(value)
    limit_f = _float_or_none(limit)
    ok = True if value_f is None or limit_f is None else value_f >= limit_f
    return {"name": name, "ok": ok, "value": value, "limit": limit, "reason": f"{name} below limit"}


def _max_check(name: str, value: Any, limit: Any) -> dict[str, Any]:
    value_f = _float_or_none(value)
    limit_f = _float_or_none(limit)
    ok = True if value_f is None or limit_f is None else value_f <= limit_f
    return {"name": name, "ok": ok, "value": value, "limit": limit, "reason": f"{name} above limit"}


def _meminfo() -> dict[str, int]:
    out = {}
    path = Path("/proc/meminfo")
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        key, _, rest = line.partition(":")
        parts = rest.strip().split()
        if parts:
            try:
                out[key] = int(parts[0])
            except ValueError:
                continue
    return out


def _systemctl_show(unit: str) -> dict[str, str]:
    try:
        proc = run(
            ["systemctl", "show", unit, "--property=ActiveState,SubState,MemoryCurrent,MemoryPeak,TasksCurrent"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return {"error": "systemctl not found"}
    if proc.returncode != 0:
        return {"error": proc.stderr.strip() or proc.stdout.strip()}
    out = {}
    for line in proc.stdout.splitlines():
        key, _, value = line.partition("=")
        out[key] = value
    return out


def _ollama_api(path: str) -> dict[str, Any]:
    try:
        req = request.Request(f"http://127.0.0.1:11434{path}")
        with request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        return {"error": str(exc)}


def _systemd_bytes(value: str | None) -> int | None:
    if not value or value in {"[not set]", "infinity"}:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value or "")
    except ValueError:
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _kb_to_gb(value: int) -> float:
    return round(value / 1024**2, 3)
