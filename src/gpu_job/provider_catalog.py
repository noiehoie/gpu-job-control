from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .config import config_path
from .concurrency import flatten_provider_limits, provider_profile_limit
from .models import app_data_dir, now_unix
from .policy import load_execution_policy


CATALOG_VERSION = "gpu-job-provider-catalog-v1"
SUPPORT_LEVELS = ("registered", "catalog_routable", "canary_executable", "production_route")
ADAPTER_EXECUTABLE_JOB_TYPES = {
    "local": {"smoke", "embedding", "llm_heavy", "pdf_ocr", "vlm_ocr", "cpu_workflow_helper"},
    "modal": {"smoke", "asr", "llm_heavy", "pdf_ocr", "vlm_ocr", "gpu_task"},
    "ollama": {"embedding", "llm_heavy", "vlm_ocr"},
    "runpod": {"smoke", "llm_heavy", "asr", "embedding", "pdf_ocr", "vlm_ocr", "gpu_task"},
    "vast": {"smoke", "llm_heavy", "asr", "embedding", "pdf_ocr", "vlm_ocr", "gpu_task"},
}
PROVIDER_SUPPORT_BASELINES = {
    "local": "production_route",
    "ollama": "production_route",
    "modal": "production_route",
    "runpod": "canary_executable",
    "vast": "canary_executable",
}


def default_catalog_path() -> Path:
    return config_path("GPU_JOB_PROVIDER_CATALOG", "provider-catalog.json")


def load_provider_catalog(path: Path | None = None) -> dict[str, Any]:
    catalog_path = path or default_catalog_path()
    if catalog_path.exists():
        data = json.loads(catalog_path.read_text())
        data.setdefault("catalog_version", CATALOG_VERSION)
        return data
    return build_provider_catalog()


def build_provider_catalog(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_execution_policy()
    provider_limits = dict(policy.get("provider_limits", {}))
    provider_regions = dict(policy.get("provider_regions", {}))
    prices = dict(policy.get("provider_price_usd_per_second", {}))
    providers: dict[str, Any] = {}
    capabilities = _load_model_capabilities()
    probe_summary = _load_probe_summary()
    for provider in sorted(_known_providers(policy, capabilities)):
        providers[provider] = _provider_entry(provider, provider_limits, provider_regions, prices, capabilities, probe_summary)
    catalog = {
        "catalog_version": CATALOG_VERSION,
        "source": "generated_from_execution_policy_and_model_capabilities",
        "providers": providers,
    }
    catalog["catalog_snapshot_id"] = catalog_snapshot_id(catalog)
    catalog["created_at"] = now_unix()
    return catalog


def catalog_dir() -> Path:
    path = app_data_dir() / "catalog"
    path.mkdir(parents=True, exist_ok=True)
    return path


def catalog_snapshot_id(catalog: dict[str, Any]) -> str:
    stable = dict(catalog)
    stable.pop("created_at", None)
    stable.pop("catalog_snapshot_id", None)
    blob = json.dumps(stable, ensure_ascii=False, sort_keys=True)
    import hashlib

    return f"cat-{hashlib.sha256(blob.encode()).hexdigest()[:16]}"


def save_catalog_snapshot(catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog = catalog or build_provider_catalog()
    snapshot_id = str(catalog.get("catalog_snapshot_id") or catalog_snapshot_id(catalog))
    catalog["catalog_snapshot_id"] = snapshot_id
    path = catalog_dir() / f"{snapshot_id}.json"
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"ok": True, "catalog_snapshot_id": snapshot_id, "path": str(path), "catalog": catalog}


def provider_capability(provider: str, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog = catalog or load_provider_catalog()
    return dict((catalog.get("providers") or {}).get(provider) or {})


def provider_supports_job_type(provider: str, job_type: str, catalog: dict[str, Any] | None = None) -> bool:
    capability = provider_capability(provider, catalog)
    return job_type in set(capability.get("supported_job_types") or [])


def provider_profile_catalog_limit(provider: str, gpu_profile: str, policy: dict[str, Any] | None = None) -> int:
    policy = policy or load_execution_policy()
    return provider_profile_limit(dict(policy.get("provider_limits", {})), provider, gpu_profile)


def _known_providers(policy: dict[str, Any], capabilities: dict[str, Any]) -> set[str]:
    providers = set(str(item) for item in dict(policy.get("provider_limits", {})).keys())
    for item in dict(capabilities.get("models", {})).values():
        if isinstance(item, dict) and item.get("provider"):
            providers.add(str(item["provider"]))
    return providers


def _provider_entry(
    provider: str,
    provider_limits: dict[str, Any],
    provider_regions: dict[str, Any],
    prices: dict[str, Any],
    capabilities: dict[str, Any],
    probe_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    models = {
        key: value
        for key, value in dict(capabilities.get("models", {})).items()
        if isinstance(value, dict) and str(value.get("provider") or "") == provider
    }
    model_supported_job_types: set[str] = set()
    model_names = []
    for key, value in models.items():
        model_names.append(key.split(":", 1)[1] if ":" in key else key)
        model_supported_job_types.update(str(item) for item in value.get("job_types") or [])
    adapter_supported = set(ADAPTER_EXECUTABLE_JOB_TYPES.get(provider, set()))
    supported_job_types = sorted(adapter_supported & model_supported_job_types) if model_supported_job_types else sorted(adapter_supported)
    flattened = flatten_provider_limits({provider: provider_limits.get(provider, 1)})
    profiles = []
    for key, limit in sorted(flattened.items()):
        _, profile = key.split(":", 1) if ":" in key else (provider, "*")
        profiles.append({"gpu_profile": profile, "max_concurrent": limit})
    probe_stats = dict((probe_summary or {}).get("stats", {}).get(provider) or {})
    latest_probe = dict((probe_summary or {}).get("latest", {}).get(provider) or {})
    support = _support_contract(provider, supported_job_types, provider_regions)
    return {
        "provider": provider,
        "provider_region": str(provider_regions.get(provider) or "unknown"),
        "is_cloud_gpu_provider": str(provider_regions.get(provider) or "") == "external",
        "support_contract": support,
        "supported_job_types": supported_job_types,
        "adapter_supported_job_types": sorted(adapter_supported),
        "model_supported_job_types": sorted(model_supported_job_types),
        "models": sorted(model_names),
        "gpu_profiles": profiles,
        "price_usd_per_second": float(prices.get(provider) or 0.0),
        "observed": {
            "probe_runtime_seconds": probe_stats.get("probe_runtime_seconds", {"p50": None, "p95": None, "count": 0}),
            "estimated_startup_seconds": probe_stats.get("estimated_startup_seconds", {"p50": None, "p95": None, "count": 0}),
            "latest_probe_ok": latest_probe.get("ok"),
            "latest_probe_recorded_at": latest_probe.get("recorded_at"),
        },
        "artifact_contract": {
            "required_files": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
            "manifest_required_for_final_verify": True,
        },
        "failure_classes": [
            "routing_refusal",
            "provider_backpressure",
            "cold_start_timeout",
            "model_unavailable",
            "image_missing_dependency",
            "context_overflow",
            "artifact_contract_failure",
            "circuit_breaker_open",
        ],
    }


def _support_contract(provider: str, supported_job_types: list[str], provider_regions: dict[str, Any]) -> dict[str, Any]:
    baseline = PROVIDER_SUPPORT_BASELINES.get(provider, "registered")
    if baseline not in SUPPORT_LEVELS:
        baseline = "registered"
    enabled = set(SUPPORT_LEVELS[: SUPPORT_LEVELS.index(baseline) + 1])
    catalog_routable = bool(supported_job_types)
    if not catalog_routable:
        enabled.discard("catalog_routable")
        enabled.discard("canary_executable")
        enabled.discard("production_route")
    return {
        "support_contract_version": "gpu-job-provider-support-contract-v1",
        "highest_support_level": max(enabled, key=SUPPORT_LEVELS.index),
        "levels": {level: level in enabled for level in SUPPORT_LEVELS},
        "provider_region": str(provider_regions.get(provider) or "unknown"),
        "is_cloud_gpu_provider": str(provider_regions.get(provider) or "") == "external",
        "basis": {
            "registered": "adapter or policy entry is present in the generated provider catalog",
            "catalog_routable": "provider has at least one catalog-supported job type",
            "canary_executable": "provider is allowed only for explicit canary or controlled routes unless promoted",
            "production_route": "provider may be selected by normal planning for its supported job types",
        },
    }


def _load_model_capabilities() -> dict[str, Any]:
    path = config_path("GPU_JOB_CAPABILITIES_CONFIG", "model-capabilities.json")
    if not path.exists():
        return {"models": {}, "quality_order": []}
    return json.loads(path.read_text())


def _load_probe_summary() -> dict[str, Any]:
    try:
        from .provider_probe import recent_probe_summary

        return recent_probe_summary()
    except Exception:
        return {"latest": {}, "stats": {}}
