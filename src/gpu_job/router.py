from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .config import config_path
from .circuit import provider_circuit_state
from .concurrency import provider_profile_limit
from .models import Job
from .policy import load_execution_policy
from .providers import PROVIDERS
from .stats import collect_stats
from .store import JobStore


ACTIVE_STATUSES = {"starting", "running"}


def default_config_path() -> Path:
    return config_path("GPU_JOB_PROFILES_CONFIG", "gpu-profiles.json")


def load_routing_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or default_config_path()
    return json.loads(config_path.read_text())


def provider_signal(name: str, profile: dict[str, Any]) -> dict[str, Any]:
    return PROVIDERS[name].signal(profile)


def apply_observed_signal(job: Job, signal: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    updated = dict(signal)
    key = f"{signal.get('provider')}:{job.job_type}:{job.gpu_profile}"
    group = stats.get("groups", {}).get(key)
    if not group or not group.get("succeeded"):
        return updated
    overhead = group.get("startup_overhead_seconds", {}).get("p50")
    runtime = group.get("runtime_seconds", {}).get("p50")
    remote = group.get("remote_runtime_seconds", {}).get("p50")
    updated["observed"] = {
        "key": key,
        "succeeded": group.get("succeeded"),
        "failed": group.get("failed"),
        "runtime_seconds_p50": runtime,
        "remote_runtime_seconds_p50": remote,
        "startup_overhead_seconds_p50": overhead,
    }
    if overhead is not None:
        updated["estimated_startup_seconds_source"] = "observed_p50_startup_overhead"
        updated["estimated_startup_seconds"] = float(overhead)
    return updated


def job_runtime_minutes(job: Job, profile: dict[str, Any]) -> float:
    return float(job.limits.get("max_runtime_minutes") or profile.get("max_runtime_minutes") or 60)


def _metadata_input(job: Job) -> dict[str, Any]:
    value = job.metadata.get("input")
    return value if isinstance(value, dict) else {}


def _metadata_routing(job: Job) -> dict[str, Any]:
    value = job.metadata.get("routing")
    return value if isinstance(value, dict) else {}


def _int_value(*values: Any, default: int = 0) -> int:
    for value in values:
        try:
            if value is not None and value != "":
                return int(float(value))
        except (TypeError, ValueError):
            continue
    return default


def _str_value(*values: Any, default: str = "") -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _estimated_input_tokens(job: Job) -> int:
    input_data = _metadata_input(job)
    routing = _metadata_routing(job)
    explicit = _int_value(
        routing.get("estimated_input_tokens"),
        input_data.get("estimated_input_tokens"),
        job.metadata.get("estimated_input_tokens"),
        default=0,
    )
    if explicit:
        return explicit
    prompt = str(input_data.get("prompt") or "")
    system_prompt = str(input_data.get("system_prompt") or "")
    text = f"{system_prompt}\n{prompt}".strip()
    if not text and job.input_uri.startswith("text://"):
        text = job.input_uri.removeprefix("text://")
    return max(0, len(text) // 4)


def _estimated_gpu_runtime_seconds(job: Job, profile: dict[str, Any], signal: dict[str, Any]) -> float:
    input_data = _metadata_input(job)
    routing = _metadata_routing(job)
    explicit = _int_value(
        routing.get("estimated_gpu_runtime_seconds"),
        input_data.get("estimated_gpu_runtime_seconds"),
        job.metadata.get("estimated_gpu_runtime_seconds"),
        default=0,
    )
    if explicit:
        return float(explicit)
    observed = signal.get("observed")
    if isinstance(observed, dict):
        for key in ("remote_runtime_seconds_p50", "runtime_seconds_p50"):
            if observed.get(key) is not None:
                return float(observed[key])
    return float(profile.get("estimated_gpu_runtime_seconds") or job_runtime_minutes(job, profile) * 60)


def _provider_load(provider: str, gpu_profile: str = "*") -> dict[str, Any]:
    policy = load_execution_policy()
    provider_limits = dict(policy.get("provider_limits", {}))
    max_concurrent = provider_profile_limit(provider_limits, provider, gpu_profile)
    active = 0
    queued = 0
    for job in JobStore().list_jobs(limit=1000):
        selected = str(job.metadata.get("selected_provider") or job.provider or "")
        requested = str(job.metadata.get("requested_provider") or "")
        if provider not in {selected, requested}:
            continue
        if gpu_profile != "*" and job.gpu_profile != gpu_profile:
            continue
        if job.status in ACTIVE_STATUSES:
            active += 1
        elif job.status == "queued":
            queued += 1
    return {
        "provider": provider,
        "max_concurrent": max_concurrent,
        "active": active,
        "queued": queued,
        "available_slots": max(0, max_concurrent - active),
        "saturated": max_concurrent > 0 and active >= max_concurrent,
    }


def workload_policy_decision(job: Job, profile: dict[str, Any], signal: dict[str, Any]) -> dict[str, Any]:
    routing = _metadata_routing(job)
    input_data = _metadata_input(job)
    provider = str(signal.get("provider") or "")
    startup = float(signal.get("estimated_startup_seconds") or 0)
    batch_size = max(1, _int_value(routing.get("batch_size"), input_data.get("batch_size"), default=1))
    burst_size = max(
        _int_value(routing.get("burst_size"), input_data.get("burst_size"), job.metadata.get("burst_size"), default=1),
        1,
    )
    latency_class = _str_value(routing.get("latency_class"), input_data.get("latency_class"), default="batch")
    startup_amortized = startup / batch_size
    gpu_runtime = _estimated_gpu_runtime_seconds(job, profile, signal)
    load = _provider_load(provider, job.gpu_profile)
    queue_wait = 0.0
    if load["saturated"] or load["queued"]:
        queue_wait = max(30.0, gpu_runtime) * max(1, int(load["queued"]) + 1)
    external_queue_depth = _int_value(signal.get("external_queue_depth"), default=0)
    if external_queue_depth:
        queue_wait += max(30.0, gpu_runtime) * external_queue_depth
    expected_total = queue_wait + startup_amortized + gpu_runtime
    cpu_runtime = _int_value(
        routing.get("estimated_cpu_runtime_seconds"),
        input_data.get("estimated_cpu_runtime_seconds"),
        job.metadata.get("estimated_cpu_runtime_seconds"),
        default=0,
    )
    deadline = _int_value(routing.get("deadline_seconds"), input_data.get("deadline_seconds"), default=0)
    quality_requires_gpu = bool(routing.get("quality_requires_gpu") or input_data.get("quality_requires_gpu"))
    tokens = _estimated_input_tokens(job)
    max_ollama_tokens = int(profile.get("ollama_max_input_tokens") or 0)
    burst_policy = dict(profile.get("burst_policy", {}))
    ollama_max_burst = int(burst_policy.get("ollama_max_burst_size") or 1)
    modal_preferred_burst = int(burst_policy.get("modal_preferred_burst_size") or 5)
    runpod_preferred_runtime = int(burst_policy.get("runpod_preferred_runtime_seconds") or 1800)
    interactive_deadline = int(burst_policy.get("interactive_deadline_seconds") or 300)

    reasons = []
    preferences = []
    ok = True
    if provider == "ollama" and max_ollama_tokens and tokens > max_ollama_tokens:
        ok = False
        reasons.append("estimated input tokens exceed ollama_max_input_tokens")
    if provider == "ollama" and burst_size > ollama_max_burst:
        ok = False
        reasons.append("burst workload exceeds resident ollama concurrency")
    if provider in {"local", "ollama"} and quality_requires_gpu:
        ok = False
        reasons.append("quality_requires_gpu excludes fixed/local deterministic providers")
    if cpu_runtime and expected_total >= cpu_runtime and not quality_requires_gpu:
        ok = False
        reasons.append("expected provider time is not faster than CPU/local path")
    if deadline and expected_total > deadline:
        ok = False
        reasons.append("expected provider time exceeds deadline_seconds")
    if provider == "modal" and burst_size >= modal_preferred_burst:
        preferences.append("modal preferred for burst fanout")
    if provider in {"runpod", "vast"} and gpu_runtime >= runpod_preferred_runtime:
        preferences.append("batch GPU provider preferred for long runtime")
    if provider == "ollama" and latency_class == "interactive" and deadline and deadline <= interactive_deadline:
        preferences.append("resident ollama preferred for light interactive latency")
    if not reasons:
        reasons.append("workload estimate accepted")
    score_components = _provider_score_components(
        provider=provider,
        expected_total=expected_total,
        startup=startup,
        burst_size=burst_size,
        gpu_runtime=gpu_runtime,
        deadline=deadline,
        quality_requires_gpu=quality_requires_gpu,
        preferences=preferences,
    )
    score = sum(float(item["value"]) for item in score_components)
    return {
        "ok": ok,
        "provider": provider,
        "estimated_input_tokens": tokens,
        "estimated_cpu_runtime_seconds": cpu_runtime or None,
        "estimated_gpu_runtime_seconds": round(gpu_runtime, 3),
        "estimated_startup_seconds": startup,
        "startup_amortized_seconds": round(startup_amortized, 3),
        "batch_size": batch_size,
        "burst_size": burst_size,
        "latency_class": latency_class,
        "queue_wait_seconds": round(queue_wait, 3),
        "expected_total_seconds": round(expected_total, 3),
        "score": round(score, 3),
        "score_components": score_components,
        "deadline_seconds": deadline or None,
        "quality_requires_gpu": quality_requires_gpu,
        "provider_load": load,
        "external_queue_depth": external_queue_depth,
        "preferences": preferences,
        "reason": "; ".join(reasons),
    }


def _provider_score(
    *,
    provider: str,
    expected_total: float,
    startup: float,
    burst_size: int,
    gpu_runtime: float,
    deadline: int,
    quality_requires_gpu: bool,
    preferences: list[str],
) -> float:
    return sum(
        float(item["value"])
        for item in _provider_score_components(
            provider=provider,
            expected_total=expected_total,
            startup=startup,
            burst_size=burst_size,
            gpu_runtime=gpu_runtime,
            deadline=deadline,
            quality_requires_gpu=quality_requires_gpu,
            preferences=preferences,
        )
    )


def _provider_score_components(
    *,
    provider: str,
    expected_total: float,
    startup: float,
    burst_size: int,
    gpu_runtime: float,
    deadline: int,
    quality_requires_gpu: bool,
    preferences: list[str],
) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = [{"name": "expected_total_seconds", "value": round(expected_total, 3)}]
    if provider == "ollama":
        components.append({"name": "resident_ollama_discount", "value": -20})
        if burst_size > 1:
            components.append({"name": "ollama_burst_penalty", "value": 10000 * burst_size})
    elif provider == "modal":
        components.append({"name": "modal_base_penalty", "value": 15})
        if burst_size >= 3:
            components.append({"name": "modal_burst_discount", "value": -min(300, 30 * burst_size)})
    elif provider in {"runpod", "vast"}:
        components.append({"name": "cold_start_penalty", "value": round(startup, 3)})
        if gpu_runtime >= 1800:
            components.append({"name": "long_runtime_batch_discount", "value": -180})
    if quality_requires_gpu and provider != "ollama":
        components.append({"name": "external_gpu_quality_discount", "value": -30})
    if deadline and expected_total > deadline * 0.8:
        components.append({"name": "deadline_risk_penalty", "value": 100})
    if preferences:
        components.append({"name": "preference_discount", "value": -25 * len(preferences)})
    return components


def startup_policy_decision(job: Job, profile: dict[str, Any], signal: dict[str, Any]) -> dict[str, Any]:
    startup = signal.get("estimated_startup_seconds")
    runtime_minutes = job_runtime_minutes(job, profile)
    runtime_seconds = max(1.0, runtime_minutes * 60)
    policy = dict(profile.get("startup_policy", {}))
    mode = str(policy.get("mode") or "strict")
    max_startup = float(policy.get("max_startup_seconds") or profile.get("max_startup_seconds") or 0)
    hard_max = float(policy.get("hard_max_startup_seconds") or 0)
    max_fraction = float(policy.get("max_startup_fraction") or 0)
    fraction = None if startup is None else float(startup) / runtime_seconds

    if startup is None:
        return {
            "ok": True,
            "mode": mode,
            "reason": "no startup estimate; provider health decides",
            "startup_fraction": fraction,
        }
    if hard_max and float(startup) > hard_max:
        return {
            "ok": False,
            "mode": mode,
            "reason": "startup exceeds hard_max_startup_seconds",
            "startup_fraction": fraction,
        }
    if mode == "amortized":
        if max_fraction and fraction is not None and fraction > max_fraction:
            return {
                "ok": False,
                "mode": mode,
                "reason": "startup fraction exceeds max_startup_fraction",
                "startup_fraction": fraction,
            }
        return {
            "ok": True,
            "mode": mode,
            "reason": "startup acceptable after runtime amortization",
            "startup_fraction": fraction,
        }
    if max_startup and float(startup) > max_startup:
        return {
            "ok": False,
            "mode": mode,
            "reason": "startup exceeds max_startup_seconds",
            "startup_fraction": fraction,
        }
    return {
        "ok": True,
        "mode": mode,
        "reason": "startup within strict latency budget",
        "startup_fraction": fraction,
    }


def capability_policy_decision(job: Job, provider: str) -> dict[str, Any]:
    from .provider_catalog import provider_capability

    capability = provider_capability(provider)
    supported = set(capability.get("supported_job_types") or [])
    if not capability:
        return {"ok": False, "reason": "unknown provider capability"}
    if job.job_type not in supported:
        return {
            "ok": False,
            "reason": f"provider does not execute job_type: {job.job_type}",
            "supported_job_types": sorted(supported),
            "catalog_version": capability.get("catalog_version"),
        }
    return {
        "ok": True,
        "reason": "provider supports job_type",
        "supported_job_types": sorted(supported),
        "catalog_version": capability.get("catalog_version"),
    }


def route_job(job: Job, config_path: Path | None = None) -> dict[str, Any]:
    config = load_routing_config(config_path)
    profiles = config.get("profiles", {})
    profile = profiles.get(job.gpu_profile)
    if not profile:
        raise ValueError(f"unknown gpu_profile in routing config: {job.gpu_profile}")
    candidates = [profile["preferred_provider"], *profile.get("fallback_providers", [])]
    seen: set[str] = set()
    ordered = []
    for name in candidates:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    stats = collect_stats()
    provider_signals = {name: apply_observed_signal(job, provider_signal(name, profile), stats) for name in ordered if name in PROVIDERS}
    provider_decisions: dict[str, Any] = {}
    selected = ""
    eligible_ranked = []
    for name in ordered:
        signal = provider_signals.get(name, {})
        circuit_decision = provider_circuit_state(name)
        capability_decision = capability_policy_decision(job, name)
        startup_decision = startup_policy_decision(job, profile, signal)
        workload_decision = workload_policy_decision(job, profile, signal)
        ok = (
            bool(capability_decision["ok"])
            and bool(circuit_decision["ok"])
            and bool(signal.get("available"))
            and bool(startup_decision["ok"])
            and bool(workload_decision["ok"])
        )
        provider_decisions[name] = {
            "eligible": ok,
            "circuit": circuit_decision,
            "capability_policy": capability_decision,
            "provider_available": bool(signal.get("available")),
            "startup_policy": startup_decision,
            "workload_policy": workload_decision,
        }
        if ok:
            eligible_ranked.append((float(workload_decision.get("score") or 0), name))
    if not selected:
        if eligible_ranked:
            eligible_ranked.sort(key=lambda item: item[0])
            selected = eligible_ranked[0][1]
        else:
            raise ValueError(f"no provider passed health and startup policy for gpu_profile: {job.gpu_profile}")
    return {
        "job_id": job.job_id,
        "gpu_profile": job.gpu_profile,
        "selected_provider": selected,
        "candidates": ordered,
        "profile": profile,
        "provider_signals": provider_signals,
        "provider_decisions": provider_decisions,
        "eligible_ranked": [{"provider": name, "score": score} for score, name in sorted(eligible_ranked)],
        "stats_used": stats.get("ok", False),
        "decision": {
            "strategy": (
                "v4 scored routing with live provider signals, resource guard, startup, queue, burst, workload, deadline, and cost policy"
            ),
            "reason": provider_decisions[selected]["workload_policy"]["reason"],
            "preferences": provider_decisions[selected]["workload_policy"].get("preferences", []),
        },
    }


def route_explanation(route_result: dict[str, Any]) -> str:
    selected = str(route_result.get("selected_provider") or "")
    gpu_profile = str(route_result.get("gpu_profile") or "")
    candidates = ", ".join(str(item) for item in route_result.get("candidates") or []) or selected
    decision = route_result.get("decision") if isinstance(route_result.get("decision"), dict) else {}
    reason = str(decision.get("reason") or "provider explicitly requested")
    ranked = route_result.get("eligible_ranked") if isinstance(route_result.get("eligible_ranked"), list) else []
    score = None
    for item in ranked:
        if isinstance(item, dict) and item.get("provider") == selected:
            score = item.get("score")
            break
    score_text = f" score={score}" if score is not None else ""
    return f"selected provider '{selected}' for gpu_profile '{gpu_profile}' from candidates [{candidates}].{score_text} reason: {reason}"
