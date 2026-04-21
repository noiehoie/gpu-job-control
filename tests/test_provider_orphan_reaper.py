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
        resources = [
            {"type": "pod", "id": "pod-1", "name": "gpu-job-pod-canary", "costPerHr": 0.46},
            {"type": "serverless_endpoint_warm_capacity", "id": "endpoint-1", "name": "manual", "workersMin": 1},
        ]
        resources = [item for item in resources if item["id"] not in self.stopped]
        return {
            "provider": "runpod",
            "ok": not resources,
            "billable_resources": resources,
            "estimated_hourly_usd": 0.46,
            "reason": "test resources present",
        }

    def _terminate_pod(self, pod_id: str) -> dict:
        self.stopped.append(pod_id)
        return {"ok": True, "pod_id": pod_id}


class StickyRunPodProvider(FakeRunPodProvider):
    def cost_guard(self) -> dict:
        return {
            "provider": "runpod",
            "ok": False,
            "billable_resources": [
                {"type": "pod", "id": "pod-1", "name": "gpu-job-pod-canary", "costPerHr": 0.46},
            ],
            "estimated_hourly_usd": 0.46,
            "reason": "test residue remains",
        }


class BrokenCostGuardRunPodProvider(FakeRunPodProvider):
    def __init__(self, *, fail_after_terminate: bool = False) -> None:
        super().__init__()
        self.fail_after_terminate = fail_after_terminate

    def cost_guard(self) -> dict:
        if not self.fail_after_terminate or self.stopped:
            raise RuntimeError("cost guard unavailable")
        return super().cost_guard()


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
    assert by_id["pod-1"]["fresh_resource"]["id"] == "pod-1"
    assert by_id["pod-1"]["post_guard"]["billable_resources"] == [
        {"type": "serverless_endpoint_warm_capacity", "id": "endpoint-1", "name": "manual", "workersMin": 1}
    ]
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


def test_provider_neutral_reaper_fails_when_post_guard_still_reports_resource() -> None:
    provider = StickyRunPodProvider()
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

    action = {item["resource_id"]: item for item in result["providers"]["runpod"]["actions"]}["pod-1"]
    assert result["providers"]["runpod"]["ok"] is False
    assert action["destroy_result"]["ok"] is False
    assert action["destroy_result"]["error"] == "post_destroy_resource_still_billable"
    assert action["destroy_result"]["residue"]["id"] == "pod-1"


def test_provider_neutral_inventory_fails_closed_when_cost_guard_unavailable() -> None:
    with TemporaryDirectory() as tmp:
        store = JobStore(Path(tmp))

        result = orphan_inventory(
            providers=["runpod"],
            store=store,
            provider_objects={"runpod": BrokenCostGuardRunPodProvider()},
            checked_at=1,
        )

    runpod = result["providers"]["runpod"]
    assert result["ok"] is False
    assert runpod["ok"] is False
    assert runpod["error"] == "cost_guard_failed"
    assert runpod["candidates"] == []


def test_provider_neutral_reaper_fails_when_post_destroy_cost_guard_unavailable() -> None:
    provider = BrokenCostGuardRunPodProvider(fail_after_terminate=True)
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

    action = {item["resource_id"]: item for item in result["providers"]["runpod"]["actions"]}["pod-1"]
    assert action["destroy_result"]["ok"] is False
    assert action["destroy_result"]["error"] == "post_destroy_cost_guard_failed"


def test_provider_neutral_reaper_requires_same_resource_type_on_fresh_read() -> None:
    class TypeChangedProvider(FakeRunPodProvider):
        def __init__(self) -> None:
            super().__init__()
            self.guard_calls = 0

        def cost_guard(self) -> dict:
            self.guard_calls += 1
            if self.stopped:
                return {"provider": "runpod", "ok": True, "billable_resources": []}
            if self.guard_calls == 1:
                return {
                    "provider": "runpod",
                    "ok": False,
                    "billable_resources": [{"type": "pod", "id": "pod-1"}],
                }
            return {
                "provider": "runpod",
                "ok": False,
                "billable_resources": [{"type": "serverless_endpoint_warm_capacity", "id": "pod-1"}],
            }

    provider = TypeChangedProvider()
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

    action = {item["resource_id"]: item for item in result["providers"]["runpod"]["actions"]}["pod-1"]
    assert action["skip_reason"] == "fresh_resource_not_found"
    assert provider.stopped == []
