from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import json

from gpu_job.models import Job
from gpu_job.providers.runpod import RunPodProvider
from gpu_job.store import JobStore


def test_runpod_asr_submit_remains_bounded_pod_canary_until_serverless_probe_is_promoted(tmp_path: Path) -> None:
    job_file = tmp_path / "job.json"
    job_file.write_text(
        json.dumps(
            {
                "job_id": "asr-job-123",
                "job_type": "asr",
                "gpu_profile": "asr_diarization",
                "input_uri": "local://audio.wav",
                "output_uri": f"file://{tmp_path}/out",
                "worker_image": "ghcr.io/example/gpu-job-asr-worker:canary",
            }
        )
    )
    job = Job.from_file(job_file)
    store = JobStore(root=tmp_path / "store")
    provider = RunPodProvider()
    output = {
        "ok": True,
        "observed_runtime": True,
        "observed_http_worker": True,
        "runtime_seconds": 3,
        "health_samples": [{"ok": True}],
        "generate_result": {
            "text": "GPU_JOB_ASR_DIARIZATION_WORKSPACE_CANARY_OK",
            "model": "pyannote/speaker-diarization-3.1",
            "asr_diarization_runtime_ok": True,
            "cache_hit": True,
            "hf_token_present": True,
            "image_contract_marker_present": True,
            "checks": {
                "hf_token_present": True,
                "faster_whisper_import": True,
                "pyannote_import": True,
                "matplotlib_import": True,
                "image_contract_marker_present": True,
                "cache_hit": True,
            },
        },
        "actual_cost_guard": {"ok": True, "estimated_cost_usd": 0.01},
        "cleanup": {"ok": True, "terminated": True},
        "pod": {"id": "pod-asr"},
    }

    with patch.object(provider, "canary_pod_http_worker", return_value=output) as canary:
        submitted = provider.submit(job, store, execute=True)

    assert submitted.status == "succeeded"
    assert submitted.provider_job_id == "pod-asr"
    assert canary.call_args.kwargs["worker_mode"] == "asr_diarization"
    artifact_dir = store.artifact_dir(job.job_id)
    result = json.loads((artifact_dir / "result.json").read_text())
    assert result["workspace_contract_ok"] is True


def test_runpod_serverless_asr_is_plan_only_until_workspace_contract_probe_passes() -> None:
    plan = RunPodProvider().plan_asr_endpoint(gpu_ids="ADA_24", network_volume_id="vol-asr")

    assert plan["ok"] is True
    assert plan["safety_invariants"]["production_dispatch"] == "blocked_until_contract_probe_passes"
    assert plan["endpoint"]["workersMin"] == 0
    assert plan["endpoint"]["workersMax"] == 1
    assert "provider_residue" in plan["workspace_observation_contract"]["required_categories"]
