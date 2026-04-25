from __future__ import annotations

import json
from pathlib import Path

from gpu_job.policy import load_provider_operations_policy
from gpu_job.policy_engine import validate_policy


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in (current.parent, *current.parents):
        if (candidate / "config" / "execution-policy.json").exists():
            return candidate
    raise RuntimeError("repository root not found")


ROOT = _repo_root()


def test_tracked_provider_operations_allow_contract_probe_hf_tokens() -> None:
    policy = load_provider_operations_policy(ROOT / "config" / "provider-operations.json")
    refs = dict(policy.get("secret_policy", {}).get("allowed_refs", {}))

    assert refs["*:*:*"] == []
    assert refs["modal:contract-probe:asr"] == ["hf_token"]
    assert refs["vast:contract-probe:asr"] == ["hf_token"]
    assert refs["runpod:contract-probe:asr"] == ["hf_token"]


def test_launch_identity_evidence_is_fixed_in_tracked_provider_operations() -> None:
    policy = load_provider_operations_policy(ROOT / "config" / "provider-operations.json")
    identities = dict(policy.get("launch_identity_evidence", {}))

    runpod = dict(identities["runpod_serverless"])
    vast = dict(identities["vast_pyworker_serverless"])

    assert runpod["probe_name"] == "runpod.asr.official_whisper_smoke"
    assert runpod["endpoint_id"]
    assert runpod["artifact_log"] == "docs/launch-logs/20260425-R1-runpod-serverless.out"
    assert vast["probe_name"] == "vast.asr.serverless_template"
    assert vast["endpoint_id"]
    assert vast["workergroup_id"]
    assert vast["artifact_log"] == "docs/launch-logs/20260425-R1-vast-serverless.out"


def test_launch_freeze_manifest_and_readiness_remain_conservative() -> None:
    manifest = json.loads((ROOT / "docs" / "launch-slice-manifest.json").read_text())
    readiness = json.loads((ROOT / "docs" / "launch-logs" / "20260425-R3-readiness-fixed.json").read_text())

    assert manifest["slices"]["04_modal"]["review_status"] == "production_primary_after_repeat_canary"
    assert manifest["slices"]["04_runpod"]["review_status"] == "conditional_batch_and_serverless_contract_path"
    assert manifest["slices"]["04_vast"]["review_status"] == "high_risk_provider_slice"

    assert readiness["provider_adapter_diff"] == []
    assert readiness["routing_by_module_enabled"] is False
    assert readiness["stop_conditions"] == []
    assert all(bool(phase["ok"]) for phase in readiness["phases"])


def test_execution_policy_still_rejects_routing_by_module_enabled_true() -> None:
    policy = json.loads((ROOT / "config" / "execution-policy.json").read_text())
    validation = validate_policy(policy)
    rejected = validate_policy(
        {
            **policy,
            "provider_module_routing": {
                "routing_by_module_enabled": True,
            },
        }
    )

    assert validation["ok"] is True
    assert policy["provider_module_routing"]["routing_by_module_enabled"] is False
    assert rejected["ok"] is False
    assert any("routing_by_module_enabled must remain false" in error for error in rejected["errors"])
