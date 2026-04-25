from __future__ import annotations

from unittest.mock import patch

from gpu_job.launch_gate import launch_phase_gate


def _manifest() -> dict:
    return {
        "slices": {
            "01_contract_core": {"review_status": "locally_verified", "blocks": []},
            "02_runtime_binding": {"review_status": "locally_verified_after_ci", "blocks": []},
            "03_lifecycle_reconciliation": {
                "review_status": "locally_verified_conservative_only",
                "blocks": ["cleanup without destructive_preflight"],
            },
            "05_runtime_configuration": {
                "review_status": "needs_provider_slice_cross_check",
                "blocks": ["production routing to unverified provider images"],
            },
            "04_modal": {"review_status": "high_risk_provider_slice", "blocks": []},
            "04_runpod": {"review_status": "high_risk_provider_slice", "blocks": []},
            "04_vast": {"review_status": "high_risk_provider_slice", "blocks": []},
        }
    }


def _policy() -> dict:
    return {
        "provider_limits": {"runpod": {"asr": 1}},
        "provider_module_routing": {
            "routing_by_module_enabled": False,
            "activation_stage": "design_only",
            "canary_evidence_required": True,
        },
    }


def _probe_schema() -> dict:
    return {
        "required_top_level_fields": ["provider_module_canary_evidence"],
        "provider_module_canary_evidence": {
            "provider_module_canary_evidence_version": "gpu-job-provider-module-canary-evidence-v1",
            "module_specific_identity_requirements": {
                "runpod_serverless": ["endpoint_id"],
                "vast_pyworker_serverless": ["endpoint_id", "workergroup_id"],
            },
        },
    }


def _module_probe(module_id: str, *, ok: bool = True) -> dict:
    return {
        "ok": True,
        "provider_module_canary_evidence": {
            "ok": ok,
            "provider_module_id": module_id,
            "module_specific_failures": [],
        },
    }


def test_launch_phase_gate_blocks_when_runpod_billable_resources_exist() -> None:
    guard = {
        "ok": False,
        "estimated_hourly_usd": 0.44,
        "providers": {
            "runpod": {
                "ok": False,
                "reason": "RunPod active pods or warm serverless workers present",
                "estimated_hourly_usd": 0.44,
                "billable_resources": [{"id": "pod-1"}, {"id": "pod-2"}],
            }
        },
    }
    with (
        patch("gpu_job.launch_gate.load_execution_policy", return_value=_policy()),
        patch("gpu_job.launch_gate.collect_cost_guard", return_value=guard),
        patch("gpu_job.launch_gate._load_manifest", return_value=_manifest()),
        patch("gpu_job.launch_gate.provider_contract_probe_schema", return_value=_probe_schema()),
        patch("gpu_job.launch_gate.recent_contract_probe_summary", return_value={"ok": True, "count": 0, "latest": {}}),
        patch("gpu_job.launch_gate._git_diff_names", return_value=[]),
    ):
        result = launch_phase_gate()

    assert result["ok"] is False
    assert result["phases"][0]["ok"] is False
    assert result["stop_conditions"][0]["name"] == "billing_guard_failed"
    assert result["guard_summary"]["providers"]["runpod"]["billable_count"] == 2
    assert result["destructive_questions_before_cleanup"]
    assert result["routing_by_module_enabled"] is False
    assert result["routing_true_rejected"] is True


def test_launch_phase_gate_accepts_phase_zero_to_two_when_guard_clean() -> None:
    guard = {"ok": True, "estimated_hourly_usd": 0.0, "providers": {"runpod": {"ok": True, "billable_resources": []}}}
    summary = {
        "ok": True,
        "count": 3,
        "latest": {
            "modal.llm_heavy.qwen2_5_32b": _module_probe("modal_function"),
            "modal.asr_diarization.pyannote": _module_probe("modal_function"),
            "runpod.asr_diarization.pyannote": {"ok": True},
            "vast.asr_diarization.pyannote": {"ok": True},
        },
    }
    with (
        patch("gpu_job.launch_gate.load_execution_policy", return_value=_policy()),
        patch("gpu_job.launch_gate.collect_cost_guard", return_value=guard),
        patch("gpu_job.launch_gate._load_manifest", return_value=_manifest()),
        patch("gpu_job.launch_gate.provider_contract_probe_schema", return_value=_probe_schema()),
        patch("gpu_job.launch_gate.recent_contract_probe_summary", return_value=summary),
        patch("gpu_job.launch_gate._git_diff_names", return_value=[]),
    ):
        result = launch_phase_gate()

    assert result["ok"] is True
    assert [phase["ok"] for phase in result["phases"][:3]] == [True, True, True]
    assert result["stop_conditions"] == []


def test_launch_phase_gate_modal_phase_requires_module_canary_evidence() -> None:
    guard = {"ok": True, "estimated_hourly_usd": 0.0, "providers": {"runpod": {"ok": True, "billable_resources": []}}}
    summary = {
        "ok": True,
        "count": 3,
        "latest": {
            "modal.llm_heavy.qwen2_5_32b": {"ok": True},
            "modal.asr_diarization.pyannote": {"ok": True},
            "runpod.asr_diarization.pyannote": {"ok": True},
            "vast.asr_diarization.pyannote": {"ok": True},
        },
    }
    with (
        patch("gpu_job.launch_gate.load_execution_policy", return_value=_policy()),
        patch("gpu_job.launch_gate.collect_cost_guard", return_value=guard),
        patch("gpu_job.launch_gate._load_manifest", return_value=_manifest()),
        patch("gpu_job.launch_gate.provider_contract_probe_schema", return_value=_probe_schema()),
        patch("gpu_job.launch_gate.recent_contract_probe_summary", return_value=summary),
        patch("gpu_job.launch_gate._git_diff_names", return_value=[]),
    ):
        result = launch_phase_gate()

    phase3 = next(phase for phase in result["phases"] if phase["name"] == "phase_3_modal_canary")
    assert phase3["ok"] is False
    assert [check["name"] for check in phase3["checks"] if not check["ok"]] == [
        "modal_llm_contract_probe_evidence_present",
        "modal_asr_contract_probe_evidence_present",
    ]


def test_launch_phase_gate_accepts_official_runpod_serverless_probe() -> None:
    guard = {
        "ok": True,
        "estimated_hourly_usd": 0.0,
        "providers": {
            "runpod": {"ok": True, "billable_resources": []},
            "vast": {"ok": True, "billable_resources": []},
        },
    }
    summary = {
        "ok": True,
        "count": 5,
        "latest": {
            "modal.llm_heavy.qwen2_5_32b": _module_probe("modal_function"),
            "modal.asr_diarization.pyannote": _module_probe("modal_function"),
            "runpod.asr_diarization.pyannote": {"ok": True},
            "runpod.asr.official_whisper_smoke": _module_probe("runpod_serverless"),
            "vast.asr.serverless_template": _module_probe("vast_pyworker_serverless"),
            "vast.asr_diarization.pyannote": _module_probe("vast_instance"),
        },
    }
    with (
        patch("gpu_job.launch_gate.load_execution_policy", return_value=_policy()),
        patch("gpu_job.launch_gate.collect_cost_guard", return_value=guard),
        patch("gpu_job.launch_gate._load_manifest", return_value=_manifest()),
        patch("gpu_job.launch_gate.provider_contract_probe_schema", return_value=_probe_schema()),
        patch("gpu_job.launch_gate.recent_contract_probe_summary", return_value=summary),
        patch("gpu_job.launch_gate._git_diff_names", return_value=[]),
    ):
        result = launch_phase_gate()

    phase4 = next(phase for phase in result["phases"] if phase["name"] == "phase_4_runpod_bounded_canary")
    runpod_serverless = next(check for check in phase4["checks"] if check["name"] == "runpod_serverless_endpoint_canary_evidence_present")
    assert runpod_serverless["ok"] is True
