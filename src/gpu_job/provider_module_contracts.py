from __future__ import annotations

from typing import Any
import copy


PROVIDER_MODULE_CONTRACT_VERSION = "gpu-job-provider-module-contract-v1"
PROVIDER_MODULE_CANARY_EVIDENCE_VERSION = "gpu-job-provider-module-canary-evidence-v1"

RUNPOD_SERVERLESS = "runpod_serverless"
RUNPOD_POD = "runpod_pod"
VAST_INSTANCE = "vast_instance"
VAST_PYWORKER_SERVERLESS = "vast_pyworker_serverless"
MODAL_FUNCTION = "modal_function"


PROVIDER_MODULE_CANARY_OBSERVATION_CATEGORIES = [
    "provider_resource_identity",
    "image_contract",
    "secret_availability",
    "workspace_cache",
    "startup_phases",
    "queue_or_reservation",
    "model_load",
    "gpu_execution",
    "artifact_contract",
    "cost_guard",
    "cleanup_result",
    "provider_residue",
]


PROVIDER_MODULE_CANARY_REQUIREMENT_CATEGORY_MAP: dict[str, dict[str, list[str]]] = {
    RUNPOD_SERVERLESS: {
        "endpoint_health": ["startup_phases", "queue_or_reservation"],
        "minimal_run": ["model_load", "gpu_execution", "artifact_contract"],
        "status_poll": ["provider_resource_identity", "queue_or_reservation"],
        "cancel": ["cleanup_result", "provider_residue"],
        "result_retention": ["artifact_contract"],
        "artifact_upload": ["artifact_contract"],
        "billing_surface_snapshot": ["cost_guard", "provider_residue"],
    },
    RUNPOD_POD: {
        "gpu_stock_query": ["queue_or_reservation"],
        "create_pod": ["provider_resource_identity", "startup_phases"],
        "proxy_url_ready": ["startup_phases"],
        "ssh_ready": ["startup_phases"],
        "workspace_path_check": ["image_contract", "workspace_cache"],
        "stop_resume": ["startup_phases", "cleanup_result"],
        "terminate": ["cleanup_result", "provider_residue"],
        "billing_residue_check": ["cost_guard", "provider_residue"],
    },
    VAST_INSTANCE: {
        "search_offer": ["queue_or_reservation", "cost_guard"],
        "create_instance": ["provider_resource_identity", "startup_phases"],
        "ssh_ready": ["startup_phases"],
        "boot_marker_check": ["startup_phases", "image_contract"],
        "gpu_cuda_check": ["gpu_execution"],
        "small_workload": ["model_load", "gpu_execution", "artifact_contract"],
        "destroy_instance": ["cleanup_result", "provider_residue"],
        "no_active_matching_instance": ["provider_residue"],
        "billing_snapshot": ["cost_guard"],
    },
    VAST_PYWORKER_SERVERLESS: {
        "endpoint_create": ["provider_resource_identity", "queue_or_reservation"],
        "workergroup_create": ["provider_resource_identity", "queue_or_reservation"],
        "worker_loading_ready": ["startup_phases"],
        "healthcheck": ["startup_phases"],
        "log_action_match": ["startup_phases", "artifact_contract"],
        "benchmark": ["gpu_execution", "cost_guard"],
        "request": ["model_load", "gpu_execution", "artifact_contract"],
        "delete_endpoint_workergroup": ["cleanup_result", "provider_residue"],
        "orphan_instance_check": ["provider_residue"],
        "billing_snapshot": ["cost_guard"],
    },
    MODAL_FUNCTION: {
        "function_invoke": ["provider_resource_identity", "startup_phases", "gpu_execution"],
        "artifact_verify": ["artifact_contract"],
        "volume_visibility": ["workspace_cache", "artifact_contract"],
        "cost_snapshot": ["cost_guard", "provider_residue"],
    },
}


PROVIDER_MODULE_CONTRACTS: dict[str, dict[str, Any]] = {
    RUNPOD_SERVERLESS: {
        "module_id": RUNPOD_SERVERLESS,
        "parent_provider": "runpod",
        "resource_model": "serverless_endpoint_worker",
        "execution_semantics": "queued_request_to_autoscaled_worker",
        "api_surfaces": [
            {
                "surface": "serverless_v2",
                "base_url": "https://api.runpod.ai/v2",
                "operations": ["run", "runsync", "status", "stream", "cancel", "health", "purge_queue"],
                "evidence": [
                    "tasks/research/runpod-org/repos/runpod__runpod-python/runpod/endpoint/runner.py",
                    "tasks/research/runpod-org/repos/runpod__js-sdk/src/index.ts",
                ],
            }
        ],
        "workspace_paths": ["/runpod/cache", "/runpod-volume"],
        "status_model": {
            "terminal_states": ["COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"],
            "known_inconsistency": "Python SDK FINAL_STATES omits CANCELLED while is_completed includes it",
            "evidence": ["tasks/research/runpod-org/repos/runpod__runpod-python/runpod/endpoint/helpers.py"],
        },
        "cost_model": ["compute_seconds", "active_worker_idle_seconds", "network_volume_gb_month"],
        "canary_requirements": [
            "endpoint_health",
            "minimal_run",
            "status_poll",
            "cancel",
            "result_retention",
            "artifact_upload",
            "billing_surface_snapshot",
        ],
    },
    RUNPOD_POD: {
        "module_id": RUNPOD_POD,
        "parent_provider": "runpod",
        "resource_model": "direct_pod",
        "execution_semantics": "persistent_pod_lifecycle",
        "api_surfaces": [
            {
                "surface": "rest_v1",
                "base_url": "https://rest.runpod.io/v1",
                "operations": ["pods", "endpoints", "billing_pods", "billing_endpoints", "billing_networkvolumes"],
                "evidence": ["tasks/research/runpod-org/repos/runpod__runpodctl/internal/api/client.go"],
            },
            {
                "surface": "graphql",
                "base_url": "https://api.runpod.io/graphql",
                "operations": ["myself", "gpuTypes", "networkVolumes", "podFindAndDeployOnDemand"],
                "evidence": [
                    "tasks/research/runpod-org/repos/runpod__runpodctl/internal/api/graphql.go",
                    "tasks/research/runpod-org/repos/runpod__runpod-python/runpod/api/mutations/pods.py",
                ],
            },
        ],
        "workspace_paths": ["/workspace", "/runpod-volume"],
        "status_model": {
            "terminal_states": [],
            "resource_status_source": "pod status and desiredStatus",
            "evidence": ["tasks/research/runpod-org/repos/runpod__runpodctl/internal/api/pods.go"],
        },
        "cost_model": ["pod_cost_per_hr", "stopped_storage_cost", "network_volume_gb_month"],
        "canary_requirements": [
            "gpu_stock_query",
            "create_pod",
            "proxy_url_ready",
            "ssh_ready",
            "workspace_path_check",
            "stop_resume",
            "terminate",
            "billing_residue_check",
        ],
    },
    VAST_INSTANCE: {
        "module_id": VAST_INSTANCE,
        "parent_provider": "vast",
        "resource_model": "market_instance",
        "execution_semantics": "offer_selected_instance_lifecycle",
        "api_surfaces": [
            {
                "surface": "vast_cli_api",
                "operations": ["search_offers", "create_instance", "show_instances", "execute", "copy", "destroy_instance"],
                "evidence": [
                    "tasks/research/vast-ai-org/repos/vast-ai__vast-cli/vastai/api/offers.py",
                    "tasks/research/vast-ai-org/repos/vast-ai__vast-cli/vastai/api/instances.py",
                ],
            }
        ],
        "workspace_paths": ["/workspace"],
        "status_model": {
            "terminal_states": [],
            "resource_status_source": "instance status from show_instances",
            "boot_sequence_source": "vast base-image vast_boot.d and provisioning manifest",
            "evidence": [
                "tasks/research/vast-ai-org/repos/vast-ai__base-image/ROOT/etc/vast_boot.d",
                "tasks/research/vast-ai-org/repos/vast-ai__base-image/ROOT/opt/instance-tools/lib/provisioner/manifest.py",
            ],
        },
        "cost_model": ["offer_dph", "disk_gb", "copy_transfer", "billing_charge_snapshot"],
        "canary_requirements": [
            "search_offer",
            "create_instance",
            "ssh_ready",
            "boot_marker_check",
            "gpu_cuda_check",
            "small_workload",
            "destroy_instance",
            "no_active_matching_instance",
            "billing_snapshot",
        ],
    },
    VAST_PYWORKER_SERVERLESS: {
        "module_id": VAST_PYWORKER_SERVERLESS,
        "parent_provider": "vast",
        "resource_model": "endpoint_workergroup_pyworker",
        "execution_semantics": "endpoint_workergroup_autoscaled_worker",
        "api_surfaces": [
            {
                "surface": "vast_endpoint_workergroup",
                "operations": ["create_endpoint", "create_workergroup", "worker_status", "logs", "delete_workergroup", "delete_endpoint"],
                "evidence": [
                    "tasks/research/vast-ai-org/repos/vast-ai__vast-cli/vastai/api/endpoints.py",
                    "tasks/research/vast-ai-org/repos/vast-ai__vast-cli/vastai/data/workergroup.py",
                ],
            },
            {
                "surface": "pyworker",
                "operations": ["healthcheck", "handler_route", "benchmark", "metrics", "log_action_config"],
                "evidence": [
                    "tasks/research/vast-ai-org/repos/vast-ai__pyworker/workers/comfyui-json/worker.py",
                    "tasks/research/vast-ai-org/repos/vast-ai__vast-cli/vastai/serverless/server/lib/backend.py",
                    "tasks/research/vast-ai-org/repos/vast-ai__vast-cli/vastai/serverless/server/lib/metrics.py",
                ],
            },
        ],
        "workspace_paths": ["/workspace"],
        "status_model": {
            "terminal_states": [],
            "worker_states": [
                "Ready",
                "Loading",
                "Inactive",
                "Destroying",
                "Error",
                "Rebooting",
                "Creating",
                "Starting",
                "Stopping",
                "Updating",
            ],
            "evidence": ["tasks/research/vast-ai-org/repos/vast-ai__docs/documentation/serverless/worker-states.mdx"],
        },
        "cost_model": ["serverless_worker_runtime", "endpoint_residue", "workergroup_residue", "billing_charge_snapshot"],
        "canary_requirements": [
            "endpoint_create",
            "workergroup_create",
            "worker_loading_ready",
            "healthcheck",
            "log_action_match",
            "benchmark",
            "request",
            "delete_endpoint_workergroup",
            "orphan_instance_check",
            "billing_snapshot",
        ],
    },
    MODAL_FUNCTION: {
        "module_id": MODAL_FUNCTION,
        "parent_provider": "modal",
        "resource_model": "modal_function",
        "execution_semantics": "function_invocation_with_image_and_volume_contracts",
        "api_surfaces": [
            {
                "surface": "modal_function",
                "operations": ["invoke_function", "image_build", "volume_mount", "logs"],
                "evidence": ["docs/cloud-gpu-provider-research.md"],
            }
        ],
        "workspace_paths": ["/workspace", "/mnt"],
        "status_model": {"terminal_states": [], "resource_status_source": "modal function invocation lifecycle"},
        "cost_model": ["function_runtime", "idle_container_seconds", "volume_storage"],
        "canary_requirements": ["function_invoke", "artifact_verify", "volume_visibility", "cost_snapshot"],
    },
}


PROVIDER_MODULES_BY_PARENT: dict[str, list[str]] = {
    "modal": [MODAL_FUNCTION],
    "runpod": [RUNPOD_SERVERLESS, RUNPOD_POD],
    "vast": [VAST_INSTANCE, VAST_PYWORKER_SERVERLESS],
}


DEFAULT_PROVIDER_MODULE_BY_PARENT: dict[str, str] = {
    "modal": MODAL_FUNCTION,
    "runpod": RUNPOD_SERVERLESS,
    "vast": VAST_INSTANCE,
}


def provider_module_input_schema() -> dict[str, Any]:
    return {
        "fields": ["metadata.provider_module_id", "metadata.provider_contract_unit"],
        "aliases": {
            "provider_module_id": "preferred caller-facing field",
            "provider_contract_unit": "compatibility alias",
        },
        "registered_modules": sorted(PROVIDER_MODULE_CONTRACTS),
        "rule": "module input is validated and recorded only; parent provider routing is unchanged",
    }


def provider_module_contract_schema() -> dict[str, Any]:
    return {
        "provider_module_contract_version": PROVIDER_MODULE_CONTRACT_VERSION,
        "registered_modules": sorted(PROVIDER_MODULE_CONTRACTS),
        "parent_providers": sorted(PROVIDER_MODULES_BY_PARENT),
        "input": provider_module_input_schema(),
        "provider_module_canary_evidence": provider_module_canary_evidence_schema(),
        "provider_module_routing_flag": provider_module_routing_flag_schema(),
        "required_module_fields": [
            "module_id",
            "parent_provider",
            "resource_model",
            "execution_semantics",
            "api_surfaces",
            "workspace_paths",
            "status_model",
            "cost_model",
            "canary_requirements",
        ],
        "invariants": [
            "top-level provider strings remain parent provider names until routing-by-module is explicitly implemented",
            "provider_module_contract is additive visibility metadata and must not allocate resources",
            "workspace_plan_id must not change solely because provider_module_contract is added or expanded",
        ],
    }


def provider_module_routing_flag_schema() -> dict[str, Any]:
    return {
        "config_key": "provider_module_routing",
        "flag": "provider_module_routing.routing_by_module_enabled",
        "default": False,
        "current_allowed_values": [False],
        "activation_stage": "design_only",
        "rule": "the flag is validated and documented but not connected to routing behavior",
        "activation_requirements": [
            "provider_module_canary_evidence parity is recorded for every registered module",
            "workspace_plan_id and idempotency compatibility are reviewed",
            "parent-provider fallback behavior is specified",
            "provider adapter changes are reviewed in a separate patch",
        ],
    }


def provider_module_canary_evidence_schema(module_id: str = "") -> dict[str, Any]:
    modules = sorted(PROVIDER_MODULE_CONTRACTS) if not module_id else [module_id]
    return {
        "provider_module_canary_evidence_version": PROVIDER_MODULE_CANARY_EVIDENCE_VERSION,
        "record_location": "record.provider_module_canary_evidence",
        "evidence_source": "record.observed.workspace_observation_coverage",
        "registered_modules": sorted(PROVIDER_MODULE_CONTRACTS),
        "observation_categories": [*PROVIDER_MODULE_CANARY_OBSERVATION_CATEGORIES],
        "category_shape": {
            "observed": "bool; deterministic artifact evidence was present",
            "ok": "bool|null; category verdict when observed",
            "evidence_fields": "list[str]; artifact fields or files that supplied evidence",
            "evidence_values": "dict[str, str]; normalized resource identity values when needed for module-specific audit",
        },
        "module_shape": {
            "module_canary_requirements": "list[str] copied from provider module contract",
            "requirement_observation_mapping": (
                "dict[str, list[str]] mapping provider-native canary requirements to shared observation categories"
            ),
            "required_observation_categories": "list[str] derived from the requirement mapping",
        },
        "module_specific_identity_requirements": {
            RUNPOD_SERVERLESS: ["endpoint_id"],
            VAST_PYWORKER_SERVERLESS: ["endpoint_id", "workergroup_id"],
        },
        "modules": {item: _provider_module_canary_schema_for_module(item) for item in modules if item in PROVIDER_MODULE_CONTRACTS},
        "invariants": [
            "provider_module_canary_evidence is read-side audit metadata and must not allocate resources",
            "provider adapters remain the native execution boundary",
            "routing_by_module_enabled remains false until a separate feature-flag design is approved",
            "all modules report the same observation category vocabulary for canary parity",
        ],
    }


def provider_module_canary_evidence(
    *,
    module_id: str,
    parent_provider: str,
    provider_module_probe_name: str,
    workspace_observation_coverage: dict[str, Any] | None,
) -> dict[str, Any]:
    coverage = workspace_observation_coverage if isinstance(workspace_observation_coverage, dict) else {}
    categories = coverage.get("categories") if isinstance(coverage.get("categories"), dict) else {}
    mapping = copy.deepcopy(PROVIDER_MODULE_CANARY_REQUIREMENT_CATEGORY_MAP.get(module_id) or {})
    required_categories = _required_canary_categories(module_id)
    category_rows = {
        category: copy.deepcopy(categories.get(category) or {"observed": False, "ok": None, "evidence_fields": []})
        for category in PROVIDER_MODULE_CANARY_OBSERVATION_CATEGORIES
    }
    module_failures = _module_specific_evidence_failures(module_id, category_rows)
    for failure in module_failures:
        category = str(failure.get("category") or "")
        if category in category_rows:
            category_rows[category]["ok"] = False
    requirements = {
        requirement: _requirement_evidence(requirement_categories, category_rows) for requirement, requirement_categories in mapping.items()
    }
    failed_categories = [name for name in required_categories if category_rows[name].get("ok") is False]
    return {
        "provider_module_canary_evidence_version": PROVIDER_MODULE_CANARY_EVIDENCE_VERSION,
        "provider_module_id": module_id,
        "parent_provider": parent_provider,
        "provider_module_probe_name": provider_module_probe_name,
        "module_canary_requirements": list(PROVIDER_MODULE_CONTRACTS.get(module_id, {}).get("canary_requirements") or []),
        "requirement_observation_mapping": mapping,
        "required_observation_categories": required_categories,
        "observation_categories": category_rows,
        "observed_categories": [name for name in required_categories if bool(category_rows[name].get("observed"))],
        "missing_categories": [name for name in required_categories if not bool(category_rows[name].get("observed"))],
        "failed_categories": failed_categories,
        "module_specific_failures": module_failures,
        "requirements": requirements,
        "ok": bool(required_categories)
        and all(bool(category_rows[name].get("observed")) for name in required_categories)
        and not failed_categories
        and not module_failures,
        "evidence_source": "record.observed.workspace_observation_coverage",
    }


def provider_module_contract_for_job(job_metadata: dict[str, Any] | None, provider: str) -> dict[str, Any]:
    metadata = job_metadata if isinstance(job_metadata, dict) else {}
    parent_provider = str(provider or "")
    available_ids = list(PROVIDER_MODULES_BY_PARENT.get(parent_provider) or [])
    requested = str(metadata.get("provider_module_id") or metadata.get("provider_contract_unit") or "")
    active = requested if requested in available_ids else str(DEFAULT_PROVIDER_MODULE_BY_PARENT.get(parent_provider) or "")
    modules = [copy.deepcopy(PROVIDER_MODULE_CONTRACTS[module_id]) for module_id in available_ids]
    active_contract = copy.deepcopy(PROVIDER_MODULE_CONTRACTS.get(active) or {})
    return {
        "provider_module_contract_version": PROVIDER_MODULE_CONTRACT_VERSION,
        "parent_provider": parent_provider,
        "active_module_id": active,
        "requested_module_id": requested,
        "available_module_ids": available_ids,
        "available_modules": modules,
        "active_module": active_contract,
        "selection": {
            "mode": "metadata_requested_or_parent_default",
            "routing_by_module_enabled": False,
            "requested_module_valid": bool(requested and requested in available_ids),
            "reason": ("provider module is recorded for contract visibility; execution still uses the parent provider adapter"),
        },
    }


def provider_module_contract(module_id: str) -> dict[str, Any]:
    return copy.deepcopy(PROVIDER_MODULE_CONTRACTS.get(module_id) or {})


def apply_provider_module_metadata(
    metadata: dict[str, Any] | None,
    *,
    provider_module_id: str = "",
    provider_contract_unit: str = "",
) -> dict[str, Any]:
    updated = dict(metadata or {})
    if provider_module_id:
        updated["provider_module_id"] = str(provider_module_id)
    if provider_contract_unit:
        updated["provider_contract_unit"] = str(provider_contract_unit)
    return updated


def provider_module_validation(job_metadata: dict[str, Any] | None, provider: str) -> dict[str, Any]:
    contract = provider_module_contract_for_job(job_metadata, provider)
    return {
        "ok": bool(contract["active_module_id"]) if contract["available_module_ids"] else not bool(contract["requested_module_id"]),
        "provider": provider,
        "provider_module_contract": contract,
        "input_schema": provider_module_input_schema(),
    }


def provider_module_probe_name(probe_name: str, spec: dict[str, Any] | None = None) -> str:
    spec = spec if isinstance(spec, dict) else {}
    module_id = str(spec.get("provider_module_id") or "")
    if not module_id:
        return str(probe_name or "")
    probe = str(probe_name or "")
    provider = str(spec.get("provider") or "")
    if provider and probe.startswith(f"{provider}."):
        return f"{module_id}.{probe.removeprefix(f'{provider}.')}"
    suffix = probe or str(spec.get("job_type") or "probe")
    return f"{module_id}.{suffix}"


def _provider_module_canary_schema_for_module(module_id: str) -> dict[str, Any]:
    contract = PROVIDER_MODULE_CONTRACTS.get(module_id) or {}
    return {
        "provider_module_id": module_id,
        "parent_provider": str(contract.get("parent_provider") or ""),
        "module_canary_requirements": list(contract.get("canary_requirements") or []),
        "requirement_observation_mapping": copy.deepcopy(PROVIDER_MODULE_CANARY_REQUIREMENT_CATEGORY_MAP.get(module_id) or {}),
        "required_observation_categories": _required_canary_categories(module_id),
    }


def _required_canary_categories(module_id: str) -> list[str]:
    mapped = PROVIDER_MODULE_CANARY_REQUIREMENT_CATEGORY_MAP.get(module_id) or {}
    categories = {
        category
        for requirement_categories in mapped.values()
        for category in requirement_categories
        if category in PROVIDER_MODULE_CANARY_OBSERVATION_CATEGORIES
    }
    return [category for category in PROVIDER_MODULE_CANARY_OBSERVATION_CATEGORIES if category in categories]


def _requirement_evidence(requirement_categories: list[str], category_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = [category_rows.get(category) or {"observed": False, "ok": None, "evidence_fields": []} for category in requirement_categories]
    return {
        "categories": [category for category in requirement_categories if category in PROVIDER_MODULE_CANARY_OBSERVATION_CATEGORIES],
        "observed": bool(rows) and all(bool(row.get("observed")) for row in rows),
        "ok": bool(rows) and all(row.get("ok") is not False and bool(row.get("observed")) for row in rows),
    }


def _module_specific_evidence_failures(module_id: str, category_rows: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    identity = category_rows.get("provider_resource_identity") or {}
    values = identity.get("evidence_values") if isinstance(identity.get("evidence_values"), dict) else {}
    failures: list[dict[str, str]] = []
    if module_id == RUNPOD_SERVERLESS and not str(values.get("endpoint_id") or ""):
        failures.append(
            {
                "category": "provider_resource_identity",
                "reason": "runpod_serverless requires endpoint_id evidence; pod_id/provider_job_id alone is not serverless evidence",
            }
        )
    if module_id == VAST_PYWORKER_SERVERLESS:
        missing = [name for name in ("endpoint_id", "workergroup_id") if not str(values.get(name) or "")]
        if missing:
            failures.append(
                {
                    "category": "provider_resource_identity",
                    "reason": (f"vast_pyworker_serverless requires endpoint_id and workergroup_id evidence; missing {','.join(missing)}"),
                }
            )
    return failures
