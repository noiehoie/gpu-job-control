from __future__ import annotations

from typing import Any
import os

from .guard import collect_cost_guard
from .providers import get_provider
from .router import load_routing_config, provider_signal


def provider_stability_report() -> dict[str, Any]:
    """Deterministic readiness gates for paid GPU providers."""
    config = load_routing_config()
    profile = config.get("profiles", {}).get("llm_heavy", {})
    guard = collect_cost_guard(["modal", "runpod", "vast"])
    modal_signal = provider_signal("modal", profile)
    runpod_signal = provider_signal("runpod", profile)
    vast_signal = provider_signal("vast", profile)

    runpod_provider = get_provider("runpod")
    runpod_snapshot = runpod_provider._api_snapshot() if hasattr(runpod_provider, "_api_snapshot") else {}
    runpod_endpoints = runpod_snapshot.get("endpoints") or []

    vast_provider = get_provider("vast")
    vast_offers = {}
    vast_templates = {}
    if hasattr(vast_provider, "offers"):
        vast_offers = vast_provider.offers(profile, limit=5)
    if hasattr(vast_provider, "recommended_templates"):
        vast_templates = vast_provider.recommended_templates()

    providers = {
        "modal": _modal_stability(guard, modal_signal),
        "runpod": _runpod_stability(guard, runpod_signal, runpod_endpoints),
        "vast": _vast_stability(guard, vast_signal, vast_offers, vast_templates),
    }
    return {
        "ok": all(item["safety_ok"] for item in providers.values()),
        "production_primary": "modal",
        "providers": providers,
        "routing_policy": {
            "now": [
                "modal is primary for external GPU llm_heavy because it has a successful canary and zero standing resources.",
                (
                    "runpod serverless vLLM / Hub-template creation is deferred for launch; "
                    "runpod may be used only through proven public endpoints or bounded Pod routes with cleanup."
                ),
                (
                    "vast may be used only after serverless endpoint/workergroup canary succeeds; "
                    "direct instance execution remains disabled by default."
                ),
            ],
            "promotion_gates": [
                "provider guard ok before submit",
                "no warm or active billable resources unless explicitly approved",
                "bounded queue wait and provider-side cancel path",
                "successful canary with artifact verification",
                "guard ok after canary",
            ],
        },
        "sources": {
            "runpod": [
                "RunPod endpoint API supports workersMin=0/workersMax and idleTimeout.",
                "RunPod endpoint deletion attempts workersMin=0/workersMax=0 quiesce first, then deletes and verifies guard.",
                "RunPod cached models reduce cold start and model download cost for Hugging Face models.",
            ],
            "vast": [
                "Vast serverless consists of endpoint, workergroup, workers, and engine.",
                "Vast workergroup controls template_hash/template_id, search_params, cold_workers, max_workers, and test_workers.",
                "Vast CLI defaults are unsafe for zero-cost idle unless cold_workers/test_workers/max_workers are explicitly bounded.",
            ],
        },
    }


def _provider_guard(guard: dict[str, Any], provider: str) -> dict[str, Any]:
    return dict((guard.get("providers") or {}).get(provider) or {})


def _modal_stability(guard: dict[str, Any], signal: dict[str, Any]) -> dict[str, Any]:
    provider_guard = _provider_guard(guard, "modal")
    ready = bool(provider_guard.get("ok") and signal.get("healthy"))
    return {
        "safety_ok": bool(provider_guard.get("ok")),
        "stable_for_production": ready,
        "status": "production_primary" if ready else "blocked",
        "reasons": _reasons(
            ready,
            [
                "Modal health check ok",
                "no running Modal apps in guard",
                "canary success already recorded in external canary report",
            ],
            [
                provider_guard.get("reason") or "Modal guard failed",
                signal.get("reason") or "Modal signal failed",
            ],
        ),
        "guard": provider_guard,
        "signal": _compact_signal(signal),
    }


def _runpod_stability(
    guard: dict[str, Any],
    signal: dict[str, Any],
    endpoints: list[dict[str, Any]],
) -> dict[str, Any]:
    provider_guard = _provider_guard(guard, "runpod")
    warm = [endpoint for endpoint in endpoints if int(endpoint.get("workersMin") or 0) > 0 or int(endpoint.get("workersStandby") or 0) > 0]
    queue_depth = int(signal.get("external_queue_depth") or 0)
    in_progress = int(signal.get("external_in_progress") or 0)
    zero_warm = not warm
    endpoint_present = bool(endpoints)
    queue_clean = queue_depth == 0 and in_progress == 0
    known_unhealthy_ids = {item.strip() for item in os.getenv("GPU_JOB_RUNPOD_UNHEALTHY_ENDPOINT_IDS", "").split(",") if item.strip()}
    known_unhealthy = [endpoint for endpoint in endpoints if str(endpoint.get("id") or "") in known_unhealthy_ids]
    candidate = bool(
        provider_guard.get("ok") and signal.get("healthy") and endpoint_present and zero_warm and queue_clean and not known_unhealthy
    )
    return {
        "safety_ok": bool(provider_guard.get("ok")),
        "stable_for_controlled_canary": candidate,
        "stable_for_production": False,
        "status": "public_or_pod_only_serverless_vllm_deferred" if candidate else "quarantined",
        "reasons": _reasons(
            candidate,
            [
                "RunPod guard ok",
                "existing serverless endpoint present",
                "workersMin=0 and workersStandby=0 on all observed endpoints",
                "provider queue is empty",
                "RunPod Serverless vLLM / Hub-template path is deferred for launch",
                "use only proven public endpoints or bounded Pod routes with cleanup",
            ],
            [
                provider_guard.get("reason") or "",
                "" if endpoint_present else "no RunPod endpoint exists",
                "" if zero_warm else "warm serverless capacity detected",
                "" if queue_clean else f"provider queue not empty: inQueue={queue_depth}, inProgress={in_progress}",
                "" if not known_unhealthy else "configured unhealthy RunPod endpoint remained queued and was cancelled",
                "RunPod Serverless vLLM / Hub-template path is deferred for launch",
                "Public Endpoint scratch creation produced hidden workersStandby=1 and was deleted",
            ],
        ),
        "required_execution_controls": [
            "always use async /run, never unbounded sync runsync",
            "persist provider job id before polling",
            "cancel provider job when queue wait exceeds max_queue_seconds",
            "guard before submit and after completion/cancel",
            "new Public Endpoint creation must go through quarantine: create, guard, delete if warm capacity appears, post-guard",
            (
                "do not promote raw GraphQL or Hub-template Serverless vLLM endpoints before "
                "Support or Console/Hub diff closes the worker-init gap"
            ),
        ],
        "endpoints": [_runpod_endpoint_summary(endpoint) for endpoint in endpoints],
        "guard": provider_guard,
        "signal": _compact_signal(signal),
    }


def _vast_stability(
    guard: dict[str, Any],
    signal: dict[str, Any],
    offers: dict[str, Any],
    templates: dict[str, Any],
) -> dict[str, Any]:
    provider_guard = _provider_guard(guard, "vast")
    offer_rows = offers.get("offers") or []
    has_offer = bool(offer_rows)
    safety_ok = bool(provider_guard.get("ok"))
    serverless_candidate = bool(safety_ok and signal.get("healthy") and has_offer)
    return {
        "safety_ok": safety_ok,
        "stable_for_production": False,
        "status": "serverless_lifecycle_only_route_unresolved" if serverless_candidate else "blocked",
        "reasons": _reasons(
            False,
            [],
            [
                "Vast direct instance execution is disabled after failed cleanup canaries",
                "" if safety_ok else provider_guard.get("reason") or "Vast guard failed",
                "" if has_offer else "no matching Vast GPU offers",
                "serverless endpoint/workergroup create/delete canary succeeded",
                "serverless route returned endpoint not found or unauthorized despite serverless key",
                "no Vast worker or instance was created during route attempts",
            ],
        ),
        "required_execution_controls": [
            "do not use direct instance submit unless metadata allow_vast_direct_instance_smoke=true is explicitly set",
            "create endpoint with cold_workers=0, max_workers=1, inactivity_timeout set",
            "create workergroup with test_workers=0 and cold_workers=0",
            "delete endpoint/workergroup immediately after canary until production promotion",
            "guard before creation and after deletion",
        ],
        "offer_query": offers.get("query"),
        "offers": [_vast_offer_summary(row) for row in offer_rows[:5]],
        "template_candidates": templates,
        "guard": provider_guard,
        "signal": _compact_signal(signal),
    }


def _reasons(ok: bool, ok_reasons: list[str], failure_reasons: list[str]) -> list[str]:
    if ok:
        return [item for item in ok_reasons if item]
    return [item for item in failure_reasons if item]


def _compact_signal(signal: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "provider",
        "healthy",
        "available",
        "reason",
        "active_jobs",
        "capacity_hint",
        "estimated_startup_seconds",
        "external_queue_depth",
        "external_in_progress",
        "offer_count",
        "cheapest_offer",
        "estimated_max_runtime_cost_usd",
        "credit",
        "can_pay",
    ]
    return {key: signal.get(key) for key in keys if key in signal}


def _runpod_endpoint_summary(endpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": endpoint.get("id"),
        "name": endpoint.get("name"),
        "gpuIds": endpoint.get("gpuIds"),
        "workersMin": endpoint.get("workersMin"),
        "workersStandby": endpoint.get("workersStandby"),
        "workersMax": endpoint.get("workersMax"),
        "idleTimeout": endpoint.get("idleTimeout"),
        "templateId": endpoint.get("templateId"),
        "networkVolumeId": endpoint.get("networkVolumeId"),
    }


def _vast_offer_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "gpu_name": row.get("gpu_name"),
        "num_gpus": row.get("num_gpus"),
        "gpu_ram_mb": row.get("gpu_ram") or row.get("gpu_ram_mb"),
        "dph_total": row.get("dph_total"),
        "reliability": row.get("reliability2") or row.get("reliability"),
        "inet_down": row.get("inet_down"),
        "disk_space_gb": row.get("disk_space") or row.get("disk_space_gb"),
        "cuda_max_good": row.get("cuda_max_good"),
    }
