from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .config import config_path
from .image_contracts import image_contract_status
from .policy import load_execution_policy


REGISTRY_VERSION = "gpu-job-requirement-registry-v1"


def default_requirement_registry_path() -> Path:
    return config_path("GPU_JOB_REQUIREMENT_REGISTRY", "requirement-registry.json")


def load_requirement_registry(path: Path | None = None) -> dict[str, Any]:
    data = json.loads((path or default_requirement_registry_path()).read_text())
    data.setdefault("registry_version", REGISTRY_VERSION)
    return data


def workload_gpu_profile(request: dict[str, Any], registry: dict[str, Any] | None = None) -> str:
    requirements = request.get("requirements") if isinstance(request.get("requirements"), dict) else {}
    hints = request.get("hints") if isinstance(request.get("hints"), dict) else {}
    if hints.get("gpu_profile") or requirements.get("gpu_profile"):
        return str(hints.get("gpu_profile") or requirements.get("gpu_profile"))
    rule = resolve_workload_rule(request, registry=registry)
    return str(rule.get("default_gpu_profile") or "")


def resolve_workload_rule(request: dict[str, Any], registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or load_requirement_registry()
    workload_kind = str(request.get("workload_kind") or "")
    candidates = [
        rule
        for rule in registry.get("workload_rules", [])
        if isinstance(rule, dict) and str(rule.get("workload_kind") or "") == workload_kind
    ]
    if not candidates:
        return {}
    matching = [rule for rule in candidates if _rule_matches(rule, request)]
    if matching:
        return sorted(matching, key=lambda item: len(dict(item.get("when") or {})), reverse=True)[0]
    return next((rule for rule in candidates if not rule.get("when")), candidates[0])


def evaluate_workload_requirements(
    request: dict[str, Any],
    *,
    provider: str = "",
    gpu_profile: str = "",
    selected_option: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registry = registry or load_requirement_registry()
    rule = resolve_workload_rule(request, registry=registry)
    if not rule:
        return _unsupported_workload(request, registry)
    selected_provider = str((selected_option or {}).get("provider") or provider or request.get("hints", {}).get("provider") or "modal")
    if selected_provider == "auto":
        selected_provider = "modal"
    selected_profile = gpu_profile or workload_gpu_profile(request, registry=registry)
    runtime = _provider_runtime(registry, selected_provider, selected_profile)
    backends, unsupported = _resolve_backends(request, rule, registry)
    if unsupported:
        return unsupported
    runtime_support = _runtime_support_status(runtime, backends)
    if not runtime_support["ok"]:
        return runtime_support
    source = _source_system(request)
    reqs = _backend_requirements(registry, backends)
    reqs.extend(_provider_secret_bindings(runtime))
    image_status = image_contract_status(runtime, backends)
    runtime_verified = _runtime_contract_probe_ok(runtime)
    evaluated = [
        _evaluate_requirement(
            item,
            provider=selected_provider,
            source=source,
            job_type=str(request.get("job_type") or ""),
            policy=policy,
            image_status=image_status,
            runtime_verified=runtime_verified,
        )
        for item in reqs
    ]
    if runtime.get("contract_probe") and not runtime_verified:
        evaluated.append(_runtime_contract_probe_requirement(runtime))
    if not image_status["ok"]:
        evaluated.append(_image_contract_requirement(image_status, runtime=runtime))
    blockers = [item for item in evaluated if not item.get("ok")]
    required_actions = [_action_for_requirement(item, runtime=runtime) for item in blockers]
    required_actions = [item for item in required_actions if item]
    if not blockers:
        return {
            "decision": "can_run_now",
            "reason": "all registered requirements are satisfied",
            "registry_version": registry.get("registry_version"),
            "rule_id": rule.get("rule_id"),
            "capabilities": list(rule.get("requires_capabilities") or []),
            "backends": backends,
            "provider_runtime": runtime,
            "image_contract": image_status,
            "requirements": evaluated,
            "blockers": [],
            "required_actions": [],
            "alternatives": list(rule.get("alternatives") or []),
        }
    return {
        "decision": "requires_action",
        "reason": "registered requirements need caller-visible action before execution",
        "registry_version": registry.get("registry_version"),
        "rule_id": rule.get("rule_id"),
        "capabilities": list(rule.get("requires_capabilities") or []),
        "backends": backends,
        "provider_runtime": runtime,
        "image_contract": image_status,
        "requirements": evaluated,
        "blockers": blockers,
        "required_actions": required_actions,
        "recommended_option": {
            "option_id": "enable_cloud_gpu_workload",
            "provider": selected_provider,
            "gpu_profile": selected_profile,
            "expected_gpu": runtime.get("expected_gpu") or "provider_selected_gpu",
            "selected_option": selected_option,
        },
        "alternatives": list(rule.get("alternatives") or []),
    }


def evaluate_workflow_requirements(
    workflow: dict[str, Any],
    *,
    estimate: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    job_template = workflow.get("job_template") if isinstance(workflow.get("job_template"), dict) else {}
    job_type = str(job_template.get("job_type") or "")
    workflow_type = str(workflow.get("workflow_type") or "")
    if job_type != "asr" and workflow_type not in {"transcription_whisper"}:
        return {
            "decision": "can_run_now",
            "reason": "workflow has no registered backend prerequisites",
            "registry_version": (registry or load_requirement_registry()).get("registry_version"),
            "rule_id": None,
            "capabilities": [],
            "backends": {},
            "requirements": [],
            "blockers": [],
            "required_actions": [],
            "alternatives": [],
        }
    metadata = job_template.get("metadata") if isinstance(job_template.get("metadata"), dict) else {}
    input_data = metadata.get("input") if isinstance(metadata.get("input"), dict) else {}
    request = {
        "workload_kind": "transcription.whisper" if job_type == "asr" else workflow_type,
        "job_type": job_type,
        "requirements": dict(workflow.get("limits") or {}),
        "hints": {
            "gpu_profile": job_template.get("gpu_profile"),
            "speaker_diarization": bool(input_data.get("diarize") or input_data.get("speaker_diarization")),
            "speaker_model": input_data.get("speaker_model"),
            "provider": workflow.get("provider"),
        },
        "business_context": dict(workflow.get("business_context") or {}),
    }
    result = evaluate_workload_requirements(
        request,
        provider=str(workflow.get("provider") or (estimate or {}).get("provider") or ""),
        gpu_profile=str(job_template.get("gpu_profile") or ""),
        selected_option={"provider": str((estimate or {}).get("provider") or workflow.get("provider") or "modal")},
        registry=registry,
        policy=policy,
    )
    if result.get("recommended_option") and estimate:
        result["recommended_option"]["estimate"] = estimate
    return result


def _rule_matches(rule: dict[str, Any], request: dict[str, Any]) -> bool:
    when = rule.get("when") if isinstance(rule.get("when"), dict) else {}
    for key, expected in when.items():
        if key == "speaker_diarization":
            actual = _speaker_diarization_requested(request)
        else:
            actual = (request.get("hints") or {}).get(key, (request.get("requirements") or {}).get(key))
        if actual != expected:
            return False
    return True


def _speaker_diarization_requested(request: dict[str, Any]) -> bool:
    requirements = request.get("requirements") if isinstance(request.get("requirements"), dict) else {}
    hints = request.get("hints") if isinstance(request.get("hints"), dict) else {}
    return bool(hints.get("diarize") or hints.get("speaker_diarization") or requirements.get("speaker_diarization"))


def _resolve_backends(
    request: dict[str, Any], rule: dict[str, Any], registry: dict[str, Any]
) -> tuple[dict[str, str], dict[str, Any] | None]:
    backends = dict(rule.get("default_backends") or {})
    hints = request.get("hints") if isinstance(request.get("hints"), dict) else {}
    speaker_model = str(hints.get("speaker_model") or (request.get("requirements") or {}).get("speaker_model") or "")
    if speaker_model and backends.get("speaker_diarization"):
        backend = dict((registry.get("backends") or {}).get(backends["speaker_diarization"]) or {})
        supported_models = set(str(item) for item in backend.get("supported_models") or [])
        if speaker_model not in supported_models:
            return {}, _unsupported_backend(request, rule, speaker_model, registry)
    return backends, None


def _backend_requirements(registry: dict[str, Any], backends: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    for capability, backend_id in sorted(backends.items()):
        backend = dict((registry.get("backends") or {}).get(backend_id) or {})
        for item in backend.get("requires") or []:
            if isinstance(item, dict):
                row = dict(item)
                row["backend"] = backend_id
                row["capability"] = capability
                out.append(row)
    return out


def _provider_runtime(registry: dict[str, Any], provider: str, gpu_profile: str) -> dict[str, Any]:
    return dict((registry.get("provider_runtimes") or {}).get(f"{provider}:{gpu_profile}") or {})


def _provider_secret_bindings(runtime: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in runtime.get("provider_secret_bindings") or [] if isinstance(item, dict)]


def _runtime_support_status(runtime: dict[str, Any], backends: dict[str, str]) -> dict[str, Any]:
    if not runtime:
        return {
            "decision": "requires_backend_registration",
            "reason": "no registered provider runtime supports this provider/profile combination",
            "blockers": [{"type": "missing_provider_runtime_registration"}],
            "required_actions": [{"action": "register_provider_runtime"}],
            "alternatives": [{"option_id": "reject", "can_run_now": True}],
            "ok": False,
        }
    supported = set(str(item) for item in runtime.get("supports_backends") or [])
    missing = sorted(set(backends.values()) - supported)
    if missing:
        return {
            "decision": "unsupported",
            "reason": "registered provider runtime does not support required backends",
            "provider_runtime": runtime,
            "backends": backends,
            "blockers": [{"type": "unsupported_backend_on_provider_runtime", "backend": item} for item in missing],
            "required_actions": [{"action": "choose_supported_provider_runtime"}],
            "alternatives": [{"option_id": "reject", "can_run_now": True}],
            "ok": False,
        }
    return {"ok": True}


def _evaluate_requirement(
    requirement: dict[str, Any],
    *,
    provider: str,
    source: str,
    job_type: str,
    policy: dict[str, Any] | None,
    image_status: dict[str, Any] | None = None,
    runtime_verified: bool = False,
) -> dict[str, Any]:
    row = dict(requirement)
    req_type = str(row.get("type") or "")
    if req_type == "secret":
        status = _secret_ref_status(provider=provider, source=source, job_type=job_type, refs=[str(row.get("id") or "")], policy=policy)
        row.update(status)
        row["ok"] = bool(status.get("ok"))
        row["status"] = "satisfied" if row["ok"] else "missing_authorization"
        return row
    if req_type == "provider_secret_binding":
        # Provider secret existence is intentionally not inferred from policy. It must be proven by a provider/image canary.
        row["ok"] = runtime_verified
        row["status"] = "satisfied_by_contract_probe" if row["ok"] else "requires_provider_secret_verification"
        return row
    if req_type == "worker_dependency":
        if row.get("status") == "assumed_by_asr_worker_contract":
            row["ok"] = True
            row["status"] = "satisfied_by_worker_contract"
        elif image_status and image_status.get("ok"):
            row["ok"] = True
            row["status"] = "satisfied_by_image_contract"
            row["image_contract_id"] = image_status.get("contract_id")
        elif runtime_verified:
            row["ok"] = True
            row["status"] = "satisfied_by_contract_probe"
        else:
            row["ok"] = False
            row["status"] = "unverified"
        return row
    row["ok"] = False
    row["status"] = "unknown_requirement_type"
    return row


def _action_for_requirement(requirement: dict[str, Any], *, runtime: dict[str, Any]) -> dict[str, Any]:
    req_type = str(requirement.get("type") or "")
    if req_type == "secret":
        return {
            "action": "authorize_secret",
            "secret_ref": requirement.get("id"),
            "scope": requirement.get("scope"),
            "reason": requirement.get("human_message") or "allow this caller/job_type/provider to use the runtime secret",
        }
    if req_type == "provider_secret_binding":
        return {
            "action": "create_provider_secret",
            "secret_ref": requirement.get("secret_ref"),
            "provider_secret_name": requirement.get("provider_secret_name"),
            "env": requirement.get("env"),
            "reason": "bind the approved runtime secret into the selected provider environment",
        }
    if req_type == "runtime_contract_probe":
        return {
            "action": "run_contract_probe",
            "contract_probe": requirement.get("contract_probe"),
            "reason": "prove the selected provider workspace, image, cache, and secret binding before executing caller data",
        }
    if req_type == "worker_dependency":
        return {
            "action": "build_image",
            "worker_image": runtime.get("worker_image") or "gpu-job-asr-worker",
            "dependency": requirement.get("id"),
            "image_contract_id": requirement.get("image_contract_id") or runtime.get("image_contract_id"),
            "reason": "build and verify the selected worker image before executing caller media",
        }
    if req_type == "image_contract":
        contract = dict(requirement.get("contract") or {})
        return {
            "action": "build_image",
            "worker_image": contract.get("image") or runtime.get("worker_image") or "gpu-job-worker",
            "image_contract_id": requirement.get("contract_id"),
            "dockerfile": dict(contract.get("build_action") or {}).get("dockerfile"),
            "reason": dict(contract.get("build_action") or {}).get("reason") or requirement.get("reason"),
        }
    return {}


def _image_contract_requirement(image_status: dict[str, Any], *, runtime: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "image_contract",
        "id": image_status.get("contract_id") or runtime.get("image_contract_id"),
        "contract_id": image_status.get("contract_id") or runtime.get("image_contract_id"),
        "status": image_status.get("status"),
        "ok": False,
        "reason": image_status.get("reason"),
        "contract": image_status.get("contract") or {},
        "required_backends": image_status.get("required_backends") or [],
        "worker_image": runtime.get("worker_image"),
    }


def _runtime_contract_probe_requirement(runtime: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "runtime_contract_probe",
        "id": runtime.get("contract_probe"),
        "contract_probe": runtime.get("contract_probe"),
        "ok": False,
        "status": "unverified",
        "reason": "provider runtime declares a contract_probe and the latest passing probe is missing",
    }


def _secret_ref_status(
    *, provider: str, source: str, job_type: str, refs: list[str], policy: dict[str, Any] | None = None
) -> dict[str, Any]:
    policy = policy or load_execution_policy()
    scopes = dict(dict(policy.get("secret_policy", {})).get("allowed_refs", {}))
    scope = f"{provider}:{source}:{job_type}"
    allowed = set(str(item) for item in scopes.get(scope, []))
    allowed.update(str(item) for item in scopes.get(f"{provider}:*:{job_type}", []))
    allowed.update(str(item) for item in scopes.get("*:*:*", []))
    denied = [item for item in refs if item not in allowed]
    return {
        "scope": scope,
        "requested_secret_refs": refs,
        "allowed_secret_refs": sorted(allowed),
        "denied_secret_refs": denied,
        "ok": not denied,
    }


def _source_system(request: dict[str, Any]) -> str:
    business_context = request.get("business_context") if isinstance(request.get("business_context"), dict) else {}
    return str(business_context.get("app_id") or business_context.get("source_system") or "default")


def _runtime_contract_probe_ok(runtime: dict[str, Any]) -> bool:
    probe_name = str(runtime.get("contract_probe") or "")
    if not probe_name:
        return False
    try:
        from .provider_contract_probe import recent_contract_probe_summary

        latest = dict(recent_contract_probe_summary().get("latest") or {})
        record = dict(latest.get(probe_name) or {})
        return bool(record.get("ok")) and str(record.get("verdict") or "") == "pass"
    except Exception:
        return False


def _unsupported_workload(request: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision": "unsupported",
        "reason": "no registered workload rule handles requested workload_kind",
        "requested_workload_kind": request.get("workload_kind"),
        "registry_version": registry.get("registry_version"),
        "blockers": [{"type": "unsupported_workload_kind"}],
        "required_actions": [{"action": "register_backend"}],
        "alternatives": [{"option_id": "reject", "can_run_now": True}],
    }


def _unsupported_backend(request: dict[str, Any], rule: dict[str, Any], requested_model: str, registry: dict[str, Any]) -> dict[str, Any]:
    supported = []
    for backend_id in dict(rule.get("default_backends") or {}).values():
        backend = dict((registry.get("backends") or {}).get(backend_id) or {})
        for model in backend.get("supported_models") or []:
            supported.append({"backend": backend_id, "model": model})
    return {
        "decision": "requires_backend_registration",
        "reason": "requested model is not registered for the requested capability",
        "requested_model": requested_model,
        "supported_options": supported,
        "registry_version": registry.get("registry_version"),
        "rule_id": rule.get("rule_id"),
        "blockers": [{"type": "unknown_model_or_backend", "requested_model": requested_model}],
        "required_actions": [{"action": "register_backend"}],
        "alternatives": [{"option_id": "reject", "can_run_now": True}],
    }
