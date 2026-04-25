from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json

from .config import project_root


CALLER_CONTRACT_VERSION = "gpu-job-caller-request-v1"
CALLER_SCHEMA_VERSION = "gpu-job-caller-schema-bundle-v1"
OPERATION_CATALOG_VERSION = "gpu-job-operation-catalog-v1"
SUPPORTED_CALLER_CONTRACT_VERSIONS = [CALLER_CONTRACT_VERSION]
LEGACY_PUBLIC_SURFACES = {
    "http": ["/validate", "/route", "/plan", "/submit"],
    "cli": ["gpu-job validate", "gpu-job workload-plan"],
}
DEFAULT_REQUIRED_FILES = ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"]
ALLOWED_PREFERENCE_FIELDS = {
    "model",
    "gpu_profile",
    "worker_image",
    "provider_module_id",
    "quality_requires_gpu",
    "allow_quality_downgrade",
    "quality_tier",
    "local_fixed_resource_policy",
    "model_size_class",
    "model_size_billion_parameters",
}
QUALITY_TIERS = {"smoke", "development", "degraded", "production_quality"}
LOCAL_FIXED_RESOURCE_POLICIES = {"unsuitable", "suitable", "unknown"}
MODEL_SIZE_CLASSES = {"unknown", "under_70b", "at_least_70b"}
FORBIDDEN_TOP_LEVEL_FIELDS = {
    "job_type",
    "input_uri",
    "output_uri",
    "worker_image",
    "gpu_profile",
    "provider",
    "provider_job_id",
}


def _schema_path() -> Path:
    return project_root() / "schemas" / "gpu-job-caller-request.schema.json"


def _catalog_path() -> Path:
    return project_root() / "config" / "operation-catalog.json"


def _prompt_path() -> Path:
    return project_root() / "docs" / "generic-system-integration-prompt-v1.md"


def caller_request_schema() -> dict[str, Any]:
    data = json.loads(_schema_path().read_text())
    data["schema_bundle_version"] = CALLER_SCHEMA_VERSION
    data["supported_contract_versions"] = SUPPORTED_CALLER_CONTRACT_VERSIONS
    data["legacy_public_surfaces"] = LEGACY_PUBLIC_SURFACES
    data["forbidden_top_level_fields"] = sorted(FORBIDDEN_TOP_LEVEL_FIELDS)
    data["fail_closed_rule"] = "reject requests that cannot be compiled without guessing"
    data["backward_compatibility_policy"] = {
        "current_contract_version": CALLER_CONTRACT_VERSION,
        "supported_contract_versions": SUPPORTED_CALLER_CONTRACT_VERSIONS,
        "rule": "new optional fields may be added; required field changes require a new contract_version",
    }
    return data


def load_operation_catalog() -> dict[str, Any]:
    data = json.loads(_catalog_path().read_text())
    data.setdefault("catalog_version", OPERATION_CATALOG_VERSION)
    return data


def operation_catalog_snapshot() -> dict[str, Any]:
    catalog = load_operation_catalog()
    return {
        "ok": True,
        "catalog_version": catalog["catalog_version"],
        "operations": catalog["operations"],
        "free_form_job_type_allowed": False,
    }


def prompt_asset_snapshot() -> dict[str, Any]:
    path = _prompt_path()
    text = path.read_text()
    return {
        "ok": True,
        "current_prompt_version": "generic-system-integration-prompt-v1",
        "path": str(path),
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "stable_alias_path": str(project_root() / "docs" / "generic-system-integration-prompt.md"),
    }


def is_caller_request(payload: dict[str, Any]) -> bool:
    return "operation" in payload or "caller" in payload or "output_expectation" in payload


def validate_caller_request(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["caller request must be a JSON object"]}
    contract_version = str(payload.get("contract_version") or "")
    if contract_version != CALLER_CONTRACT_VERSION:
        errors.append(f"contract_version must be {CALLER_CONTRACT_VERSION}")
    operation = str(payload.get("operation") or "")
    if not operation:
        errors.append("missing operation")
    catalog = load_operation_catalog()
    operations = dict(catalog.get("operations") or {})
    op_spec = dict(operations.get(operation) or {})
    if operation and not op_spec:
        errors.append(f"unsupported operation: {operation}")
    input_data = payload.get("input")
    if not isinstance(input_data, dict):
        errors.append("input must be an object")
    else:
        if not str(input_data.get("uri") or ""):
            errors.append("input.uri is required")
        scheme = str(input_data.get("uri") or "").split("://", 1)[0] if "://" in str(input_data.get("uri") or "") else ""
        allowed = set(op_spec.get("input_contract", {}).get("required_input_uri_schemes") or [])
        if scheme and allowed and scheme not in allowed:
            errors.append(f"input.uri scheme {scheme} is not allowed for operation {operation}")
        parameters = input_data.get("parameters")
        if parameters is not None and not isinstance(parameters, dict):
            errors.append("input.parameters must be an object when present")
        required_parameters = op_spec.get("input_contract", {}).get("required_parameters") or []
        for key in required_parameters:
            source = parameters if isinstance(parameters, dict) else {}
            if key not in source:
                errors.append(f"input.parameters.{key} is required for operation {operation}")
    output_expectation = payload.get("output_expectation")
    if not isinstance(output_expectation, dict):
        errors.append("output_expectation must be an object")
    else:
        if not str(output_expectation.get("target_uri") or ""):
            errors.append("output_expectation.target_uri is required")
        required_files = output_expectation.get("required_files")
        if not isinstance(required_files, list) or not required_files or not all(str(item).strip() for item in required_files):
            errors.append("output_expectation.required_files must be a non-empty string array")
    limits = payload.get("limits")
    if not isinstance(limits, dict):
        errors.append("limits must be an object")
    else:
        for key in ("max_runtime_minutes", "max_cost_usd", "max_output_gb"):
            if key not in limits:
                errors.append(f"limits.{key} is required")
        for key in ("max_runtime_minutes", "max_cost_usd", "max_output_gb"):
            value = limits.get(key)
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                errors.append(f"limits.{key} must be numeric and positive")
                continue
            if numeric <= 0:
                errors.append(f"limits.{key} must be positive")
    idempotency = payload.get("idempotency")
    if not isinstance(idempotency, dict) or not str(idempotency.get("key") or ""):
        errors.append("idempotency.key is required")
    caller = payload.get("caller")
    if not isinstance(caller, dict):
        errors.append("caller must be an object")
    else:
        for key in ("system", "operation", "request_id", "version"):
            if not str(caller.get(key) or ""):
                errors.append(f"caller.{key} is required")
    trace_context = payload.get("trace_context")
    if trace_context is not None and not isinstance(trace_context, dict):
        errors.append("trace_context must be an object when present")
    preferences = payload.get("preferences")
    if preferences is not None and not isinstance(preferences, dict):
        errors.append("preferences must be an object when present")
    prefs = dict(preferences or {}) if isinstance(preferences, dict) else {}
    unknown_preferences = sorted(key for key in prefs if key not in ALLOWED_PREFERENCE_FIELDS)
    if unknown_preferences:
        errors.append(f"unsupported preferences present: {', '.join(unknown_preferences)}")
    quality_tier = str(prefs.get("quality_tier") or "")
    if quality_tier and quality_tier not in QUALITY_TIERS:
        errors.append(f"preferences.quality_tier must be one of {', '.join(sorted(QUALITY_TIERS))}")
    local_policy = str(prefs.get("local_fixed_resource_policy") or "")
    if local_policy and local_policy not in LOCAL_FIXED_RESOURCE_POLICIES:
        errors.append(f"preferences.local_fixed_resource_policy must be one of {', '.join(sorted(LOCAL_FIXED_RESOURCE_POLICIES))}")
    model_size_class = str(prefs.get("model_size_class") or "")
    if model_size_class and model_size_class not in MODEL_SIZE_CLASSES:
        errors.append(f"preferences.model_size_class must be one of {', '.join(sorted(MODEL_SIZE_CLASSES))}")
    model_size_billion_parameters = prefs.get("model_size_billion_parameters")
    if model_size_billion_parameters is not None:
        try:
            if float(model_size_billion_parameters) < 0:
                errors.append("preferences.model_size_billion_parameters must be non-negative")
        except (TypeError, ValueError):
            errors.append("preferences.model_size_billion_parameters must be numeric")

    if not errors and operation == "llm.generate" and quality_tier == "production_quality":
        try:
            model_size = float(prefs.get("model_size_billion_parameters"))
        except (TypeError, ValueError):
            model_size = 0.0
        large_by_size = model_size >= 70
        large_by_class = model_size_class == "at_least_70b"
        if not (large_by_size or large_by_class):
            errors.append("production_quality llm.generate requires >=70B model")
        if prefs.get("quality_requires_gpu") is not True:
            errors.append("production_quality llm.generate requires quality_requires_gpu=true")
        if local_policy != "unsuitable":
            errors.append("production_quality llm.generate requires local_fixed_resource_policy=unsuitable")

    forbidden = sorted(key for key in FORBIDDEN_TOP_LEVEL_FIELDS if key in payload)
    if forbidden:
        errors.append(f"forbidden top-level fields present: {', '.join(forbidden)}")
    return {
        "ok": not errors,
        "contract_version": CALLER_CONTRACT_VERSION,
        "catalog_version": catalog["catalog_version"],
        "errors": errors,
    }


def compile_caller_request(payload: dict[str, Any]) -> dict[str, Any]:
    validation = validate_caller_request(payload)
    if not validation["ok"]:
        return {
            "ok": False,
            "error": "caller request validation failed",
            "errors": validation["errors"],
            "contract_version": CALLER_CONTRACT_VERSION,
        }
    catalog = load_operation_catalog()
    operation = str(payload["operation"])
    spec = dict(catalog["operations"][operation])
    input_data = dict(payload["input"])
    output_expectation = dict(payload["output_expectation"])
    limits = dict(payload["limits"])
    caller = dict(payload["caller"])
    idempotency = dict(payload["idempotency"])
    preferences = dict(payload.get("preferences") or {})
    routing = {
        "quality_requires_gpu": bool(preferences.get("quality_requires_gpu", False)),
        "allow_quality_downgrade": bool(preferences.get("allow_quality_downgrade", False)),
        "local_fixed_resource_policy": str(preferences.get("local_fixed_resource_policy") or "unknown"),
        "model_size_class": str(preferences.get("model_size_class") or "unknown"),
        "quality_tier": str(preferences.get("quality_tier") or "development"),
    }
    if "model_size_billion_parameters" in preferences:
        routing["model_size_billion_parameters"] = preferences["model_size_billion_parameters"]
    input_parameters = dict(input_data.get("parameters") or {})
    job = {
        "job_type": spec["job_type"],
        "input_uri": str(input_data["uri"]),
        "output_uri": str(output_expectation["target_uri"]),
        "worker_image": str(preferences.get("worker_image") or spec["job_defaults"]["worker_image"]),
        "gpu_profile": str(preferences.get("gpu_profile") or spec["job_defaults"]["gpu_profile"]),
        "model": str(preferences.get("model") or spec["job_defaults"].get("model") or ""),
        "limits": {
            "max_runtime_minutes": int(limits["max_runtime_minutes"]),
            "max_cost_usd": float(limits["max_cost_usd"]),
            "max_output_gb": float(limits["max_output_gb"]),
        },
        "verify": {
            "required_files": list(output_expectation.get("required_files") or DEFAULT_REQUIRED_FILES),
        },
        "metadata": {
            "purpose": str(caller.get("operation") or operation),
            "source_system": str(caller["system"]),
            "task_family": operation,
            "caller_operation": str(caller["operation"]),
            "caller_request_id": str(caller["request_id"]),
            "caller_version": str(caller["version"]),
            "idempotency_key": str(idempotency["key"]),
            "trace_context": dict(payload.get("trace_context") or {}),
            "operation_contract": {
                "operation": operation,
                "operation_catalog_version": catalog["catalog_version"],
                "required_secrets": list(spec.get("required_secrets") or []),
                "allowed_lanes": list(spec.get("allowed_lanes") or []),
                "forbidden_lanes": list(spec.get("forbidden_lanes") or []),
                "failure_taxonomy": list(spec.get("failure_taxonomy") or []),
            },
            "input": input_parameters,
            "routing": routing,
        },
    }
    if preferences.get("provider_module_id"):
        job["metadata"]["provider_module_id"] = str(preferences["provider_module_id"])
    return {
        "ok": True,
        "caller_request": payload,
        "operation": operation,
        "operation_spec": spec,
        "job": job,
        "contract_version": CALLER_CONTRACT_VERSION,
        "catalog_version": catalog["catalog_version"],
    }
