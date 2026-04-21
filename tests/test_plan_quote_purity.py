from __future__ import annotations

from gpu_job.plan_quote import build_plan_quote


def test_plan_quote_hash_ignores_runtime_evidence_and_cleanup_residue() -> None:
    plan = {
        "contract_version": "gpu-job-routing-contract-v1",
        "request": {"job_type": "asr", "gpu_profile": "asr_diarization"},
        "catalog_version": "test",
        "catalog_snapshot_id": "snapshot-1",
        "gpu_profile": "asr_diarization",
        "selected_option": {"provider": "runpod", "gpu_profile": "asr_diarization", "estimated_total_cost_usd_p95": 0.5},
        "options": [],
        "refusals": [],
        "estimate": {"estimated_total_cost_usd_p95": 0.5},
        "approval": {"decision": "requires_action", "reason": "run contract probe"},
        "can_run_now": False,
        "action_requirements": {"required_action_type": "run_contract_probe"},
    }
    mutated = {
        **plan,
        "created_at": 999999999,
        "cleanup": {"ok": False, "residue": ["endpoint-1"]},
        "provider_residue": [{"id": "endpoint-1"}],
        "post_guard": {"ok": False},
        "runtime_evidence": {"handler_contract_id": "asr-diarization-runpod-serverless-large-v3-pyannote3.3.2-cuda12.4"},
    }

    assert build_plan_quote(plan)["quote_hash"] == build_plan_quote(mutated)["quote_hash"]
