from __future__ import annotations

from typing import Any
import copy
import json
import subprocess

from .config import project_root
from .guard import collect_cost_guard
from .policy import load_execution_policy
from .policy_engine import validate_policy
from .provider_contract_probe import parse_contract_probe_artifact, provider_contract_probe_schema, recent_contract_probe_summary


LAUNCH_GATE_VERSION = "gpu-job-launch-gate-v1"
LAUNCH_PROVIDER_GUARD = ["modal", "runpod", "vast"]


def launch_phase_gate(*, limit: int = 100) -> dict[str, Any]:
    policy = load_execution_policy()
    guard = collect_cost_guard(LAUNCH_PROVIDER_GUARD)
    manifest = _load_manifest()
    probe_schema = provider_contract_probe_schema()
    probe_summary = recent_contract_probe_summary(limit=limit)
    provider_adapter_diff = _git_diff_names(["src/gpu_job/providers"])

    policy_validation = validate_policy(policy)
    routing_flag = dict(policy.get("provider_module_routing") or {})
    routing_disabled = routing_flag.get("routing_by_module_enabled") is False
    rejects_true = _routing_true_is_rejected(policy)
    phase0 = _phase(
        "phase_0_current_diff_fixed",
        [
            ("policy_valid", bool(policy_validation.get("ok"))),
            ("routing_by_module_enabled_false", routing_disabled),
            ("routing_by_module_true_rejected", rejects_true),
            ("provider_adapter_diff_empty", not provider_adapter_diff),
            ("guard_clean", bool(guard.get("ok"))),
            ("contract_probe_schema_has_module_canary_evidence", _probe_schema_has_module_canary_evidence(probe_schema)),
            ("contract_probe_schema_has_serverless_identity_rules", _probe_schema_has_serverless_identity_rules(probe_schema)),
        ],
    )
    phase1 = _phase(
        "phase_1_contract_core_launch_candidate",
        [
            ("slice_01_contract_core_locally_verified", _slice_status(manifest, "01_contract_core") == "locally_verified"),
            (
                "slice_02_runtime_binding_locally_verified_after_ci",
                _slice_status(manifest, "02_runtime_binding") == "locally_verified_after_ci",
            ),
            (
                "slice_03_lifecycle_conservative_only",
                _slice_status(manifest, "03_lifecycle_reconciliation") == "locally_verified_conservative_only",
            ),
            (
                "destructive_cleanup_not_enabled",
                _slice_blocks(manifest, "03_lifecycle_reconciliation", "cleanup without destructive_preflight"),
            ),
            ("contract_probe_schema_has_module_canary_evidence", _probe_schema_has_module_canary_evidence(probe_schema)),
            ("contract_probe_schema_has_serverless_identity_rules", _probe_schema_has_serverless_identity_rules(probe_schema)),
        ],
    )
    phase2 = _phase(
        "phase_2_runtime_config_cross_check",
        [
            (
                "slice_05_runtime_config_requires_provider_cross_check",
                _slice_status(manifest, "05_runtime_configuration") == "needs_provider_slice_cross_check",
            ),
            (
                "production_unverified_image_routing_blocked",
                _slice_blocks(manifest, "05_runtime_configuration", "unverified provider images"),
            ),
            ("routing_by_module_enabled_false", routing_disabled),
        ],
    )
    runpod_active = _provider_billable_count(guard, "runpod")
    modal_llm_probe_ok = _latest_module_probe_ok(probe_summary, "modal.llm_heavy.qwen2_5_32b", "modal_function")
    modal_asr_probe_ok = _latest_module_probe_ok(probe_summary, "modal.asr_diarization.pyannote", "modal_function")
    runpod_pod_probe_ok = (
        _latest_probe_ok(probe_summary, "runpod.generic.pod_http")
        or _latest_probe_ok(probe_summary, "runpod.asr_diarization.pyannote")
        or _latest_probe_ok(probe_summary, "runpod.llm_heavy.pod_http")
    )
    runpod_serverless_probe_ok = (
        _latest_module_probe_ok(
            probe_summary,
            "runpod.generic.serverless_endpoint",
            "runpod_serverless",
        )
        or _latest_module_probe_ok(
            probe_summary,
            "runpod.asr_diarization.serverless_handler",
            "runpod_serverless",
        )
        or _latest_module_probe_ok(
            probe_summary,
            "runpod.asr.official_whisper_smoke",
            "runpod_serverless",
        )
    )
    vast_instance_probe_ok = (
        _latest_module_probe_ok(probe_summary, "vast.generic.instance", "vast_instance")
        or _latest_module_probe_ok(probe_summary, "vast.instance_smoke.cuda", "vast_instance")
        or _latest_module_probe_ok(probe_summary, "vast.asr_diarization.pyannote", "vast_instance")
    )
    vast_pyworker_probe_ok = _latest_module_probe_ok(
        probe_summary, "vast.generic.pyworker_serverless", "vast_pyworker_serverless"
    ) or _latest_module_probe_ok(probe_summary, "vast.asr.serverless_template", "vast_pyworker_serverless")
    phase3 = _phase(
        "phase_3_modal_canary",
        [
            ("guard_clean_before_canary", bool(guard.get("ok"))),
            ("modal_llm_contract_probe_evidence_present", modal_llm_probe_ok),
            ("modal_asr_contract_probe_evidence_present", modal_asr_probe_ok),
            (
                "modal_slice_promoted_after_repeat_canary",
                _slice_status(manifest, "04_modal") in {"high_risk_provider_slice", "production_primary_after_repeat_canary"},
            ),
        ],
    )
    phase4 = _phase(
        "phase_4_runpod_bounded_canary",
        [
            ("runpod_no_active_billable_resources", runpod_active == 0),
            ("runpod_bounded_pod_canary_evidence_present", runpod_pod_probe_ok),
            ("runpod_serverless_endpoint_canary_evidence_present", runpod_serverless_probe_ok),
            (
                "runpod_slice_conditional_or_high_risk",
                _slice_status(manifest, "04_runpod") in {"high_risk_provider_slice", "conditional_batch_and_serverless_contract_path"},
            ),
            ("serverless_vllm_deferred", True),
        ],
    )
    phase5 = _phase(
        "phase_5_vast_reserve_canary",
        [
            ("guard_clean_before_canary", bool(guard.get("ok"))),
            ("vast_direct_instance_canary_evidence_present", vast_instance_probe_ok),
            ("vast_pyworker_serverless_canary_evidence_present", vast_pyworker_probe_ok),
            ("vast_slice_high_risk_until_repeat_canary", _slice_status(manifest, "04_vast") == "high_risk_provider_slice"),
            ("vast_primary_forbidden", True),
        ],
    )
    stop_conditions = _stop_conditions(
        guard=guard,
        provider_adapter_diff=provider_adapter_diff,
        routing_disabled=routing_disabled,
        rejects_true=rejects_true,
    )
    destructive_questions = []
    if runpod_active:
        destructive_questions = [
            "Are these RunPod pods still serving a current production or diagnostic role?",
            "Are their attached volumes or logs needed before termination?",
            "Is this account expected to keep any gpu-job-pod-canary resource alive during launch prep?",
        ]
    return {
        "ok": not stop_conditions and all(item["ok"] for item in [phase0, phase1, phase2]),
        "launch_gate_version": LAUNCH_GATE_VERSION,
        "routing_by_module_enabled": routing_flag.get("routing_by_module_enabled", False),
        "routing_true_rejected": rejects_true,
        "provider_adapter_diff": provider_adapter_diff,
        "phases": [phase0, phase1, phase2, phase3, phase4, phase5],
        "stop_conditions": stop_conditions,
        "destructive_questions_before_cleanup": destructive_questions,
        "guard_summary": _guard_summary(guard),
        "contract_probe_summary": {
            "ok": probe_summary.get("ok"),
            "count": probe_summary.get("count"),
            "latest_probe_names": sorted((probe_summary.get("latest") or {}).keys()),
        },
        "forbidden_until_next_approval": [
            "Mac Studio Docker build/push",
            "provider adapter changes before canary evidence parity",
            "provider_module_routing.routing_by_module_enabled=true",
            "RunPod Serverless vLLM or Hub-template as a launch blocker",
            "Vast production_primary promotion",
            "serverless module promotion without endpoint identity evidence",
            "destructive cleanup without explicit approval, fresh provider read, and destructive preflight",
        ],
    }


def _phase(name: str, checks: list[tuple[str, bool]]) -> dict[str, Any]:
    normalized = [{"name": check_name, "ok": bool(ok)} for check_name, ok in checks]
    return {
        "name": name,
        "ok": all(item["ok"] for item in normalized),
        "checks": normalized,
    }


def _load_manifest() -> dict[str, Any]:
    path = project_root() / "docs" / "launch-slice-manifest.json"
    return json.loads(path.read_text())


def _slice_status(manifest: dict[str, Any], name: str) -> str:
    return str(((manifest.get("slices") or {}).get(name) or {}).get("review_status") or "")


def _slice_blocks(manifest: dict[str, Any], name: str, text: str) -> bool:
    blocks = ((manifest.get("slices") or {}).get(name) or {}).get("blocks") or []
    return any(text in str(item) for item in blocks)


def _probe_schema_has_module_canary_evidence(schema: dict[str, Any]) -> bool:
    return "provider_module_canary_evidence" in (schema.get("required_top_level_fields") or []) and bool(
        schema.get("provider_module_canary_evidence")
    )


def _probe_schema_has_serverless_identity_rules(schema: dict[str, Any]) -> bool:
    canary = schema.get("provider_module_canary_evidence")
    rules = canary.get("module_specific_identity_requirements") if isinstance(canary, dict) else {}
    if not isinstance(rules, dict):
        return False
    return rules.get("runpod_serverless") == ["endpoint_id"] and rules.get("vast_pyworker_serverless") == [
        "endpoint_id",
        "workergroup_id",
    ]


def _routing_true_is_rejected(policy: dict[str, Any]) -> bool:
    candidate = copy.deepcopy(policy)
    routing = dict(candidate.get("provider_module_routing") or {})
    routing["routing_by_module_enabled"] = True
    candidate["provider_module_routing"] = routing
    result = validate_policy(candidate)
    return not result.get("ok") and any("routing_by_module_enabled" in str(item) for item in result.get("errors") or [])


def _latest_probe_ok(summary: dict[str, Any], probe_name: str) -> bool:
    latest = summary.get("latest") if isinstance(summary.get("latest"), dict) else {}
    row = latest.get(probe_name) if isinstance(latest, dict) else None
    return bool(isinstance(row, dict) and row.get("ok"))


def _latest_module_probe_ok(summary: dict[str, Any], probe_name: str, module_id: str) -> bool:
    latest = summary.get("latest") if isinstance(summary.get("latest"), dict) else {}
    row = latest.get(probe_name) if isinstance(latest, dict) else None
    if not isinstance(row, dict) or not row.get("ok"):
        return False
    artifact_dir = str(row.get("artifact_dir") or "")
    if artifact_dir:
        try:
            reparsed = parse_contract_probe_artifact(
                artifact_dir,
                provider=str(row.get("provider") or ""),
                probe_name=probe_name,
                spec=row.get("spec") if isinstance(row.get("spec"), dict) else None,
                execution_mode=str(row.get("execution_mode") or "executed"),
                append=False,
            )
        except Exception:
            return False
        row = reparsed
    evidence = row.get("provider_module_canary_evidence") if isinstance(row.get("provider_module_canary_evidence"), dict) else {}
    if "module_specific_failures" not in evidence:
        return False
    return bool(evidence.get("ok")) and evidence.get("provider_module_id") == module_id


def _provider_billable_count(guard: dict[str, Any], provider: str) -> int:
    providers = guard.get("providers") if isinstance(guard.get("providers"), dict) else {}
    row = providers.get(provider) if isinstance(providers, dict) else None
    if not isinstance(row, dict):
        return 0
    return len(row.get("billable_resources") or [])


def _guard_summary(guard: dict[str, Any]) -> dict[str, Any]:
    providers = guard.get("providers") if isinstance(guard.get("providers"), dict) else {}
    return {
        "ok": bool(guard.get("ok")),
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


def _stop_conditions(
    *,
    guard: dict[str, Any],
    provider_adapter_diff: list[str],
    routing_disabled: bool,
    rejects_true: bool,
) -> list[dict[str, Any]]:
    stops = []
    if not guard.get("ok"):
        stops.append(
            {
                "name": "billing_guard_failed",
                "reason": "active billable resources or local resource guard failure present",
                "guard_summary": _guard_summary(guard),
            }
        )
    if provider_adapter_diff:
        stops.append({"name": "provider_adapter_diff_present", "paths": provider_adapter_diff})
    if not routing_disabled:
        stops.append({"name": "routing_by_module_enabled_not_false"})
    if not rejects_true:
        stops.append({"name": "routing_by_module_true_not_rejected"})
    return stops


def _git_diff_names(paths: list[str]) -> list[str]:
    root = project_root()
    cmd = ["git", "diff", "--name-only", "--", *paths]
    try:
        proc = subprocess.run(cmd, cwd=root, check=True, capture_output=True, text=True)
    except Exception:
        return ["<git_diff_failed>"]
    return [line for line in proc.stdout.splitlines() if line.strip()]
