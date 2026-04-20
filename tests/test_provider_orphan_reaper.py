from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gpu_job.models import Job
from gpu_job.orphan_inventory import orphan_inventory, orphan_reaper
from gpu_job.store import JobStore


class FakeRunPodProvider:
    def __init__(self) -> None:
        self.stopped: list[str] = []

    def cost_guard(self) -> dict:
        return {
            "provider": "runpod",
            "ok": False,
            "billable_resources": [
                {"type": "pod", "id": "pod-1", "name": "gpu-job-pod-canary", "costPerHr": 0.46},
                {"type": "serverless_endpoint_warm_capacity", "id": "endpoint-1", "name": "manual", "workersMin": 1},
            ],
            "estimated_hourly_usd": 0.46,
            "reason": "test resources present",
        }

    def _terminate_pod(self, pod_id: str) -> dict:
        self.stopped.append(pod_id)
        return {"ok": True, "pod_id": pod_id}


def _terminal_job(job_id: str, provider_job_id: str) -> Job:
    job = Job(
        job_id=job_id,
        job_type="asr",
        input_uri="local://in",
        output_uri="local://out",
        worker_image="image",
        gpu_profile="asr_diarization",
        provider="runpod",
        provider_job_id=provider_job_id,
        status="failed",
    )
    job.metadata["timing_v2"] = {
        "version": "gpu-job-timing-v2",
        "events": [
            {"seq": 1, "event_id": "1", "phase": "cleaning_up", "event": "enter", "at": 1.0},
            {"seq": 2, "event_id": "2", "phase": "cleaning_up", "event": "exit", "at": 2.0, "status": "failed"},
        ],
    }
    return job


def test_provider_neutral_inventory_reports_exact_runpod_resource_match() -> None:
    with TemporaryDirectory() as tmp:
        store = JobStore(Path(tmp))
        store.save(_terminal_job("done", "pod-1"))

        result = orphan_inventory(providers=["runpod"], store=store, provider_objects={"runpod": FakeRunPodProvider()}, checked_at=1)

    candidates = result["providers"]["runpod"]["candidates"]
    by_id = {item["resource_id"]: item for item in candidates}
    assert by_id["pod-1"]["reason"] == "terminal_job_active_resource"
    assert by_id["pod-1"]["evidence"]["provider_job_id_exact_match"] is True
    assert by_id["endpoint-1"]["reason"] == "unmatched_billable_resource"


def test_provider_neutral_reaper_applies_only_exact_runpod_pod_with_cleanup_evidence() -> None:
    provider = FakeRunPodProvider()
    with TemporaryDirectory() as tmp:
        store = JobStore(Path(tmp))
        store.save(_terminal_job("done", "pod-1"))

        with patch("gpu_job.orphan_inventory.destructive_preflight", return_value={"ok": True, "reason": "approved"}):
            result = orphan_reaper(
                providers=["runpod"],
                apply=True,
                principal="operator",
                store=store,
                provider_objects={"runpod": provider},
                checked_at=1,
            )

    actions = result["providers"]["runpod"]["actions"]
    by_id = {item["resource_id"]: item for item in actions}
    assert by_id["pod-1"]["destroy_result"]["ok"] is True
    assert by_id["endpoint-1"]["skip_reason"] == "report_only_reason"
    assert provider.stopped == ["pod-1"]


def test_provider_neutral_reaper_requires_destructive_preflight() -> None:
    provider = FakeRunPodProvider()
    with TemporaryDirectory() as tmp:
        store = JobStore(Path(tmp))
        store.save(_terminal_job("done", "pod-1"))

        with patch("gpu_job.orphan_inventory.destructive_preflight", return_value={"ok": False, "reason": "blocked"}):
            result = orphan_reaper(
                providers=["runpod"],
                apply=True,
                principal="operator",
                store=store,
                provider_objects={"runpod": provider},
                checked_at=1,
            )

    action = {item["resource_id"]: item for item in result["providers"]["runpod"]["actions"]}["pod-1"]
    assert action["skip_reason"] == "destructive_preflight_failed"
    assert provider.stopped == []
