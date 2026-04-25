from __future__ import annotations

from typing import Any
import hashlib
import json

from .models import make_job_id, now_unix
from .execution_plan import execution_plan_schema
from .plan_quote import build_plan_quote, plan_quote_schema
from .provider_catalog import provider_capability, save_catalog_snapshot
from .provider_module_contracts import provider_module_contract_for_job, provider_module_contract_schema
from .requirements import evaluate_workload_requirements, workload_gpu_profile
from .workflow import approval_decision, resolve_budget_class


CONTRACT_VERSION = "gpu-job-contract-v1"
WORKLOAD_KIND_TO_JOB_TYPE = {
    "transcription.whisper": "asr",
    "ocr.vlm": "vlm_ocr",
    "ocr.pdf": "pdf_ocr",
    "embedding.text": "embedding",
    "inference.chat": "llm_heavy",
    "map_reduce.generic": "llm_heavy",
}
WORKLOAD_DEFAULT_PROFILE = {
    "transcription.whisper": "asr_fast",
    "ocr.vlm": "vlm_ocr",
    "ocr.pdf": "vlm_ocr",
    "embedding.text": "embedding",
    "inference.chat": "llm_heavy",
    "map_reduce.generic": "llm_heavy",
}


def workload_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = payload.get("workload") if isinstance(payload.get("workload"), dict) else payload
    request = dict(request)
    workload_kind = str(request.get("workload_kind") or request.get("kind") or "")
    if not workload_kind:
        raise ValueError("missing workload_kind")
    if workload_kind not in WORKLOAD_KIND_TO_JOB_TYPE:
        raise ValueError(f"unsupported workload_kind: {workload_kind}")
    inputs = request.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        input_uri = str(request.get("input_uri") or "")
        if not input_uri:
            raise ValueError("workload request requires inputs or input_uri")
        inputs = [{"uri": input_uri}]
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": str(request.get("request_id") or make_job_id("workload")),
        "workload_kind": workload_kind,
        "job_type": WORKLOAD_KIND_TO_JOB_TYPE[workload_kind],
        "inputs": [dict(item) for item in inputs if isinstance(item, dict)],
        "output_contract": dict(request.get("output_contract") or {}),
        "requirements": dict(request.get("requirements") or {}),
        "hints": dict(request.get("hints") or {}),
        "business_context": dict(request.get("business_context") or {}),
        "provider_module_id": str(request.get("provider_module_id") or request.get("provider_contract_unit") or ""),
        "created_at": int(request.get("created_at") or now_unix()),
    }


def plan_workload(payload: dict[str, Any], *, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    request = workload_request(payload)
    if catalog is None:
        snapshot = save_catalog_snapshot()
        catalog = snapshot["catalog"]
    else:
        snapshot = save_catalog_snapshot(catalog)
        catalog = snapshot["catalog"]
    business_context = request["business_context"]
    budget = resolve_budget_class(business_context)
    requirements = request["requirements"]
    gpu_profile = workload_gpu_profile(request)
    options = []
    refusals = []
    for provider, capability in sorted(dict(catalog.get("providers") or {}).items()):
        supported = request["job_type"] in set(capability.get("supported_job_types") or [])
        allowed = provider in set(budget.get("allowed_providers") or [])
        if not supported or not allowed:
            refusals.append(
                {
                    "provider": provider,
                    "reason": "unsupported_job_type" if not supported else "provider_not_allowed_by_budget",
                    "supported": supported,
                    "allowed": allowed,
                }
            )
            continue
        estimate = _estimate_provider_option(request, provider, gpu_profile, capability)
        estimate["provider_module_contract"] = provider_module_contract_for_job(
            {"provider_module_id": request.get("provider_module_id")}, provider
        )
        options.append(estimate)
    options.sort(key=lambda item: (item["estimated_total_cost_usd_p95"], item["estimated_total_seconds_p95"], item["provider"]))
    selected = options[0] if options else None
    estimate = {
        "estimated_cost_p50_usd": selected["estimated_total_cost_usd_p50"] if selected else 0.0,
        "estimated_cost_p95_usd": selected["estimated_total_cost_usd_p95"] if selected else 0.0,
        "auto_approve_cap_usd": float(budget.get("auto_approve_cap_usd") or 0),
        "hard_cap_usd": float(budget.get("hard_cap_usd") or 0),
    }
    decision = approval_decision(estimate, budget, requirements)
    if not selected:
        decision = {
            "decision": "reject",
            "reason": "no provider supports workload within budget policy",
            "effective_hard_cap_usd": estimate["hard_cap_usd"],
        }
    action = evaluate_workload_requirements(request, selected_option=selected, gpu_profile=gpu_profile)
    if selected and action["decision"] == "requires_action":
        decision = {
            "decision": "requires_action",
            "reason": action["reason"],
            "effective_hard_cap_usd": estimate["hard_cap_usd"],
            "required_actions": action["required_actions"],
        }
    elif selected and action["decision"] in {"requires_backend_registration", "unsupported"}:
        decision = {
            "decision": action["decision"],
            "reason": action["reason"],
            "effective_hard_cap_usd": estimate["hard_cap_usd"],
            "required_actions": action.get("required_actions", []),
        }
    plan = {
        "contract_version": CONTRACT_VERSION,
        "plan_id": _plan_id(request, catalog),
        "request": request,
        "catalog_version": catalog.get("catalog_version"),
        "catalog_snapshot_id": catalog.get("catalog_snapshot_id"),
        "gpu_profile": gpu_profile,
        "selected_option": selected,
        "options": options,
        "refusals": refusals,
        "estimate": estimate,
        "approval": decision,
        "can_run_now": decision["decision"] not in {"reject", "requires_action", "requires_backend_registration", "unsupported"},
        "action_requirements": action,
        "created_at": now_unix(),
    }
    quote = build_plan_quote(plan)
    plan["plan_quote"] = quote
    return {
        "ok": bool(selected) and decision["decision"] not in {"reject", "requires_backend_registration", "unsupported"},
        "plan": plan,
        "plan_quote": quote,
    }


def workload_to_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    request = workload_request(payload)
    if request["workload_kind"] != "transcription.whisper":
        raise ValueError(f"no workflow adapter for workload_kind: {request['workload_kind']}")
    input_uri = str(request["inputs"][0].get("uri") or "")
    hints = request["hints"]
    requirements = request["requirements"]
    duration_seconds = _input_duration_seconds(request)
    segment_seconds = int(hints.get("segment_seconds") or requirements.get("segment_seconds") or 600)
    model = _worker_asr_model(str(hints.get("model") or requirements.get("model") or "large-v3"))
    diarize = bool(hints.get("diarize") or hints.get("speaker_diarization") or requirements.get("speaker_diarization"))
    gpu_profile = workload_gpu_profile(request)
    speaker_model = str(hints.get("speaker_model") or requirements.get("speaker_model") or "pyannote/speaker-diarization-3.1")
    action = evaluate_workload_requirements(
        request, selected_option={"provider": str(hints.get("provider") or "modal")}, gpu_profile=gpu_profile
    )
    plan_quote = plan_workload(request).get("plan_quote", {})
    secret_refs = [
        str(item.get("id") or item.get("secret_ref"))
        for item in action.get("requirements", [])
        if isinstance(item, dict) and str(item.get("type") or "") == "secret" and (item.get("id") or item.get("secret_ref"))
    ]
    return {
        "workflow_type": "transcription_whisper",
        "input_uri": input_uri,
        "input_payload": {
            "input_uri": input_uri,
            "duration_seconds": duration_seconds,
            "input_size": {"duration_seconds": duration_seconds},
        },
        "strategy": {
            "splitter": "ffmpeg_time_splitter",
            "reducer": "timeline_reducer",
            "segment_seconds": segment_seconds,
            "estimated_map_seconds": max(30, int(duration_seconds / 4)) if duration_seconds else 120,
            "estimated_reduce_seconds": 30,
        },
        "business_context": request["business_context"],
        "plan_quote": plan_quote,
        "provider": str(hints.get("provider") or "auto"),
        "limits": requirements,
        "job_template": {
            "job_type": "asr",
            "input_uri": "workflow://transcription-whisper/chunk",
            "output_uri": "workflow://transcription-whisper/out",
            "worker_image": "auto",
            "gpu_profile": gpu_profile,
            "model": model,
            "limits": {
                "max_runtime_minutes": int(requirements.get("max_runtime_minutes") or 120),
                **({"max_cost_usd": requirements["max_cost_usd"]} if requirements.get("max_cost_usd") is not None else {}),
            },
            "metadata": {
                **({"secret_refs": sorted(set(secret_refs))} if secret_refs else {}),
                **({"plan_quote": plan_quote} if plan_quote else {}),
                "input": {
                    "language": hints.get("language"),
                    "model": model,
                    "diarize": diarize,
                    "speaker_diarization": diarize,
                    "speaker_model": speaker_model,
                },
                "routing": {
                    "quality_requires_gpu": True,
                    "estimated_gpu_runtime_seconds": max(30, int(duration_seconds / 4)) if duration_seconds else 120,
                },
                "model_requirements": {
                    "asr": True,
                    "speaker_diarization": diarize,
                    "min_quality_tier": "external_gpu",
                },
            },
        },
    }


def artifact_manifest_schema() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "required_files": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log", "manifest.json"],
        "required_result_fields_by_job_type": {
            "asr": ["text", "segments"],
            "llm_heavy": ["text"],
            "vlm_ocr": ["text"],
            "embedding": ["items", "count", "dimensions"],
            "gpu_task": ["ok"],
        },
    }


def contract_schemas() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "execution_plan": execution_plan_schema(),
        "plan_quote": plan_quote_schema(),
        "provider_module_contract": provider_module_contract_schema(),
        "artifact_manifest": artifact_manifest_schema(),
        "failure_taxonomy": failure_taxonomy(),
    }


def _worker_asr_model(model: str) -> str:
    aliases = {
        "whisper-large-v3": "large-v3",
        "openai/whisper-large-v3": "large-v3",
    }
    return aliases.get(model, model)


def failure_taxonomy() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "classes": [
            "routing_refusal",
            "provider_backpressure",
            "cold_start_timeout",
            "model_unavailable",
            "image_missing_dependency",
            "context_overflow",
            "artifact_contract_failure",
            "circuit_breaker_open",
            "quota_exceeded",
            "cost_block",
            "verification_failed",
            "executor_state_inconsistency",
            "model_contract_mismatch",
            "image_contract_mismatch",
            "gpu_contract_mismatch",
            "cache_contract_missing",
            "endpoint_unreachable",
            "empty_output_success",
        ],
    }


def _estimate_provider_option(
    request: dict[str, Any],
    provider: str,
    gpu_profile: str,
    capability: dict[str, Any],
) -> dict[str, Any]:
    requirements = request["requirements"]
    hints = request["hints"]
    runtime = float(
        hints.get("estimated_gpu_runtime_seconds") or requirements.get("estimated_runtime_seconds") or _default_runtime(request)
    )
    startup = float(hints.get("estimated_startup_seconds") or _default_startup(provider))
    price = float(capability.get("price_usd_per_second") or 0.0)
    total_p50 = runtime + startup
    total_p95 = total_p50 * 1.35
    cost_p50 = total_p50 * price
    cost_p95 = total_p95 * price
    return {
        "option_id": f"{provider}:{gpu_profile}",
        "provider": provider,
        "gpu_profile": gpu_profile,
        "job_type": request["job_type"],
        "estimated_total_seconds_p50": round(total_p50, 3),
        "estimated_total_seconds_p95": round(total_p95, 3),
        "estimated_total_cost_usd_p50": round(cost_p50, 6),
        "estimated_total_cost_usd_p95": round(cost_p95, 6),
        "cold_start_seconds_p50": startup,
        "catalog_capability": provider_capability(provider, {"providers": {provider: capability}}),
    }


def _default_runtime(request: dict[str, Any]) -> float:
    if request["workload_kind"] == "transcription.whisper":
        duration = _input_duration_seconds(request)
        return max(30.0, duration / 4.0)
    return 120.0


def _default_startup(provider: str) -> float:
    if provider in {"local", "ollama"}:
        return 0.0
    if provider == "modal":
        return 90.0
    return 180.0


def _input_duration_seconds(request: dict[str, Any]) -> float:
    for item in request["inputs"]:
        for key in ("duration_seconds", "estimated_duration_seconds"):
            if item.get(key) is not None:
                try:
                    return float(item[key])
                except (TypeError, ValueError):
                    pass
    hints = request["hints"]
    try:
        return float(hints.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        return 0.0


def _plan_id(request: dict[str, Any], catalog: dict[str, Any]) -> str:
    blob = json.dumps({"request": request, "catalog": catalog.get("catalog_version")}, sort_keys=True, ensure_ascii=False)
    return f"plan-{hashlib.sha256(blob.encode()).hexdigest()[:16]}"
