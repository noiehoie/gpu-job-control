from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gpu_job.models import Job
from gpu_job.orphan_inventory import _job_lifecycle_evidence, _vast_lifecycle_phase, vast_orphan_inventory, vast_orphan_reaper
from gpu_job.store import JobStore


def _instance(instance_id: str, label: str, *, actual_status: str = "running", cur_state: str = "running") -> dict:
    return {
        "id": instance_id,
        "label": label,
        "actual_status": actual_status,
        "cur_state": cur_state,
        "duration": 123,
        "dph_total": 0.42,
    }


def _job(job_id: str, *, status: str = "running", provider_job_id: str = "100") -> Job:
    return Job(
        job_id=job_id,
        job_type="asr",
        input_uri="/tmp/input.mp4",
        output_uri="/tmp/out",
        worker_image="image",
        gpu_profile="asr_diarization",
        provider="vast",
        provider_job_id=provider_job_id,
        status=status,
    )


def _add_cleanup_evidence(job: Job) -> Job:
    job.metadata["timing_v2"] = {
        "version": "gpu-job-timing-v2",
        "events": [
            {"seq": 1, "event_id": "000000000001", "phase": "running_worker", "event": "enter", "at": 0.0},
            {"seq": 2, "event_id": "000000000002", "phase": "running_worker", "event": "exit", "at": 1.0, "status": "succeeded"},
            {"seq": 3, "event_id": "000000000003", "phase": job.status, "event": "instant", "at": 1.0, "status": job.status},
            {"seq": 4, "event_id": "000000000004", "phase": "cleaning_up", "event": "enter", "at": 1.0},
            {"seq": 5, "event_id": "000000000005", "phase": "cleaning_up", "event": "exit", "at": 2.0, "status": "failed"},
        ],
    }
    return job


def _add_cleanup_enter_only(job: Job) -> Job:
    job.metadata["timing_v2"] = {
        "version": "gpu-job-timing-v2",
        "events": [
            {"seq": 1, "event_id": "000000000001", "phase": "cleaning_up", "event": "enter", "at": 1.0},
        ],
    }
    return job


class FakeVastProvider:
    def __init__(self, instances: list[dict], fresh_instances: list[dict] | None = None) -> None:
        self.instances = instances
        self.fresh_instances = fresh_instances if fresh_instances is not None else instances
        self.destroyed: list[str] = []

    def _instances(self) -> list[dict]:
        return self.instances

    def _instances_by_label(self, label: str) -> list[dict]:
        return [item for item in self.fresh_instances if item.get("label") == label]

    def destroy_instance(self, instance_id: str) -> dict:
        self.destroyed.append(str(instance_id))
        return {"ok": True, "instance_id": str(instance_id), "stdout": "{}", "stderr": "", "exit_code": 0}


class VastOrphanInventoryTest(unittest.TestCase):
    def test_inventory_empty_when_no_instances(self) -> None:
        with TemporaryDirectory() as tmp:
            result = vast_orphan_inventory(store=JobStore(Path(tmp)), instances=[], checked_at=1)

        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["provider"], "vast")
        self.assertEqual(result["instances_seen"], 0)
        self.assertEqual(result["candidates"], [])

    def test_ignores_non_exact_gpu_job_labels(self) -> None:
        instances = [
            _instance("1", "manual-gpu-job:missing"),
            _instance("2", "gpu-job-abc"),
            _instance("3", "xgpu-job:abc"),
            _instance("4", "gpu-job:"),
            _instance("5", ""),
        ]
        with TemporaryDirectory() as tmp:
            result = vast_orphan_inventory(store=JobStore(Path(tmp)), instances=instances, checked_at=1)

        self.assertEqual(result["instances_seen"], 5)
        self.assertEqual(result["candidates"], [])

    def test_detects_job_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            result = vast_orphan_inventory(
                store=JobStore(Path(tmp)),
                instances=[_instance("101", "gpu-job:missing-job")],
                checked_at=1,
            )

        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["reason"], "job_missing")
        self.assertEqual(candidate["category"], "ghost")
        self.assertEqual(candidate["instance_id"], "101")
        self.assertEqual(candidate["job_id"], "missing-job")
        self.assertIsNone(candidate["job_status"])
        self.assertIsNone(candidate["provider_job_id"])
        self.assertEqual(candidate["evidence"]["category"], "ghost")
        self.assertFalse(candidate["evidence"]["job_exists"])
        self.assertTrue(candidate["evidence"]["provider_state"]["active"])
        self.assertEqual(candidate["evidence"]["provider_state"]["lifecycle_phase"], "running")
        self.assertFalse(candidate["would_destroy"])

    def test_detects_unreadable_job_file_separately_from_missing_job(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.ensure()
            store.job_path("broken-job").write_text("{not-json")

            result = vast_orphan_inventory(
                store=store,
                instances=[_instance("101", "gpu-job:broken-job")],
                checked_at=1,
            )

        candidate = result["candidates"][0]
        self.assertEqual(candidate["reason"], "job_unreadable")
        self.assertEqual(candidate["category"], "job_unreadable")
        self.assertEqual(candidate["evidence"]["job_load_error"], "job_file_parse_failed")
        self.assertTrue(candidate["evidence"]["job_file_exists"])
        self.assertTrue(candidate["evidence"]["job_file_unreadable"])
        self.assertFalse(candidate["evidence"]["job_exists"])
        self.assertEqual(result["summary"]["job_unreadable"], 1)

    def test_ignores_deleted_instance_when_job_is_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            result = vast_orphan_inventory(
                store=JobStore(Path(tmp)),
                instances=[_instance("101", "gpu-job:missing-job", actual_status="deleted", cur_state="deleted")],
                checked_at=1,
            )

        self.assertEqual(result["candidates"], [])

    def test_detects_terminal_job_active_instance(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.save(_job("done-job", status="succeeded", provider_job_id="102"))

            result = vast_orphan_inventory(
                store=store,
                instances=[_instance("102", "gpu-job:done-job")],
                checked_at=1,
            )

        candidate = result["candidates"][0]
        self.assertEqual(candidate["reason"], "terminal_job_active_instance")
        self.assertEqual(candidate["category"], "zombie")
        self.assertEqual(candidate["job_status"], "succeeded")
        self.assertEqual(candidate["provider_job_id"], "102")
        self.assertTrue(candidate["evidence"]["job_terminal"])
        self.assertTrue(candidate["evidence"]["provider_job_id_exact_match"])
        self.assertFalse(candidate["evidence"]["provider_job_id_mismatch"])
        self.assertFalse(candidate["evidence"]["job_lifecycle"]["timing_present"])
        self.assertFalse(candidate["would_destroy"])

    def test_ignores_terminal_job_when_instance_is_deleted(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.save(_job("done-job", status="failed", provider_job_id="103"))

            result = vast_orphan_inventory(
                store=store,
                instances=[_instance("103", "gpu-job:done-job", actual_status="deleted", cur_state="deleted")],
                checked_at=1,
            )

        self.assertEqual(result["candidates"], [])

    def test_detects_provider_job_id_mismatch(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.save(_job("running-job", status="running", provider_job_id="expected-104"))

            result = vast_orphan_inventory(
                store=store,
                instances=[_instance("104", "gpu-job:running-job")],
                checked_at=1,
            )

        candidate = result["candidates"][0]
        self.assertEqual(candidate["reason"], "provider_job_id_mismatch")
        self.assertEqual(candidate["category"], "id_mismatch")
        self.assertEqual(candidate["provider_job_id"], "expected-104")
        self.assertFalse(candidate["evidence"]["provider_job_id_exact_match"])
        self.assertTrue(candidate["evidence"]["provider_job_id_mismatch"])
        self.assertFalse(candidate["would_destroy"])

    def test_ignores_matching_running_job(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.save(_job("running-job", status="running", provider_job_id="105"))

            result = vast_orphan_inventory(
                store=store,
                instances=[_instance("105", "gpu-job:running-job")],
                checked_at=1,
            )

        self.assertEqual(result["candidates"], [])

    def test_all_candidates_are_dry_run_only(self) -> None:
        with TemporaryDirectory() as tmp:
            result = vast_orphan_inventory(
                store=JobStore(Path(tmp)),
                instances=[
                    _instance("106", "gpu-job:missing-a"),
                    _instance("107", "gpu-job:missing-b"),
                ],
                checked_at=1,
            )

        self.assertTrue(result["dry_run"])
        self.assertTrue(all(candidate["would_destroy"] is False for candidate in result["candidates"]))
        self.assertEqual(result["summary"]["job_missing"], 2)

    def test_reaper_dry_run_marks_only_terminal_exact_cleanup_candidate_eligible(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.save(_add_cleanup_evidence(_job("done-job", status="failed", provider_job_id="201")))
            provider = FakeVastProvider([_instance("201", "gpu-job:done-job")])

            result = vast_orphan_reaper(store=store, provider=provider, checked_at=1)

        self.assertEqual(result["mode"], "dry_run")
        self.assertEqual(len(result["actions"]), 1)
        action = result["actions"][0]
        self.assertTrue(action["eligible"])
        self.assertTrue(action["would_destroy"])
        self.assertIsNone(action["destroy_result"])
        self.assertTrue(action["evidence"]["job_lifecycle"]["timing_present"])
        self.assertEqual(action["evidence"]["job_lifecycle"]["cleaning_up_spans"][0]["duration_seconds"], 1.0)
        self.assertEqual(provider.destroyed, [])

    def test_lifecycle_phase_classifies_vast_states(self) -> None:
        self.assertEqual(_vast_lifecycle_phase(_instance("1", "gpu-job:a", actual_status="starting", cur_state="loading")), "provisioning")
        self.assertEqual(_vast_lifecycle_phase(_instance("2", "gpu-job:a", actual_status="running", cur_state="loaded")), "running")
        self.assertEqual(_vast_lifecycle_phase(_instance("3", "gpu-job:a", actual_status="stopping", cur_state="running")), "stopping")
        self.assertEqual(_vast_lifecycle_phase(_instance("4", "gpu-job:a", actual_status="deleted", cur_state="running")), "terminal")

    def test_job_lifecycle_evidence_reports_open_and_closed_phases(self) -> None:
        job = _job("timing-job", status="failed", provider_job_id="300")
        job.metadata["timing_v2"] = {
            "version": "gpu-job-timing-v2",
            "events": [
                {"seq": 1, "event_id": "000000000001", "phase": "running_worker", "event": "enter", "at": 1.0},
                {"seq": 2, "event_id": "000000000002", "phase": "cleaning_up", "event": "enter", "at": 2.0},
                {"seq": 3, "event_id": "000000000003", "phase": "cleaning_up", "event": "exit", "at": 5.0, "status": "failed"},
            ],
        }

        evidence = _job_lifecycle_evidence(job)

        self.assertTrue(evidence["timing_present"])
        self.assertEqual(evidence["open_phases"], [{"phase": "running_worker", "attempt": 1}])
        self.assertEqual(evidence["last_closed_phase"], "cleaning_up")
        self.assertEqual(evidence["cleaning_up_spans"][0]["duration_seconds"], 3.0)

    def test_reaper_skips_provisioning_instance_even_with_terminal_job(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.save(_add_cleanup_evidence(_job("done-job", status="failed", provider_job_id="209")))
            provider = FakeVastProvider([_instance("209", "gpu-job:done-job", actual_status="starting", cur_state="loading")])

            result = vast_orphan_reaper(store=store, provider=provider, checked_at=1)

        action = result["actions"][0]
        self.assertEqual(action["evidence"]["provider_state"]["lifecycle_phase"], "provisioning")
        self.assertEqual(action["skip_reason"], "instance_lifecycle_not_destroyable:provisioning")
        self.assertFalse(action["eligible"])
        self.assertEqual(provider.destroyed, [])

    def test_reaper_skips_stopping_instance_even_with_terminal_job(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.save(_add_cleanup_evidence(_job("done-job", status="failed", provider_job_id="210")))
            provider = FakeVastProvider([_instance("210", "gpu-job:done-job", actual_status="stopping", cur_state="running")])

            result = vast_orphan_reaper(apply=True, principal="operator", store=store, provider=provider, checked_at=1)

        action = result["actions"][0]
        self.assertEqual(action["evidence"]["provider_state"]["lifecycle_phase"], "stopping")
        self.assertEqual(action["skip_reason"], "instance_lifecycle_not_destroyable:stopping")
        self.assertFalse(action["eligible"])
        self.assertEqual(provider.destroyed, [])

    def test_reaper_rejects_unsupported_mode_policy_without_inventory(self) -> None:
        provider = FakeVastProvider([_instance("211", "gpu-job:any")])

        result = vast_orphan_reaper(mode_policy="extended", provider=provider, checked_at=1)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "unsupported_reaper_mode_policy")
        self.assertEqual(result["supported_mode_policies"], ["conservative"])
        self.assertIsNone(result["inventory"])
        self.assertEqual(provider.destroyed, [])

    def test_reaper_keeps_job_missing_and_mismatch_report_only(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.save(_job("running-job", status="running", provider_job_id="expected"))
            provider = FakeVastProvider(
                [
                    _instance("202", "gpu-job:missing-job"),
                    _instance("203", "gpu-job:running-job"),
                ]
            )

            result = vast_orphan_reaper(store=store, provider=provider, checked_at=1)

        reasons = {item["reason"]: item for item in result["actions"]}
        self.assertFalse(reasons["job_missing"]["eligible"])
        self.assertEqual(reasons["job_missing"]["skip_reason"], "report_only_reason")
        self.assertFalse(reasons["provider_job_id_mismatch"]["eligible"])
        self.assertEqual(reasons["provider_job_id_mismatch"]["skip_reason"], "report_only_reason")
        self.assertEqual(provider.destroyed, [])

    def test_reaper_keeps_unreadable_job_report_only(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.ensure()
            store.job_path("broken-job").write_text("{not-json")
            provider = FakeVastProvider([_instance("208", "gpu-job:broken-job")])

            result = vast_orphan_reaper(store=store, provider=provider, checked_at=1)

        action = result["actions"][0]
        self.assertEqual(action["reason"], "job_unreadable")
        self.assertFalse(action["eligible"])
        self.assertEqual(action["skip_reason"], "report_only_reason")
        self.assertEqual(provider.destroyed, [])

    def test_reaper_requires_cleanup_evidence_for_terminal_job(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.save(_job("done-job", status="failed", provider_job_id="204"))
            provider = FakeVastProvider([_instance("204", "gpu-job:done-job")])

            result = vast_orphan_reaper(store=store, provider=provider, checked_at=1)

        self.assertEqual(result["actions"][0]["skip_reason"], "missing_cleanup_evidence")
        self.assertFalse(result["actions"][0]["eligible"])
        self.assertEqual(provider.destroyed, [])

    def test_reaper_requires_cleanup_exit_evidence_for_terminal_job(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.save(_add_cleanup_enter_only(_job("done-job", status="failed", provider_job_id="204")))
            provider = FakeVastProvider([_instance("204", "gpu-job:done-job")])

            result = vast_orphan_reaper(store=store, provider=provider, checked_at=1)

        action = result["actions"][0]
        self.assertEqual(action["skip_reason"], "missing_cleanup_evidence")
        self.assertFalse(action["eligible"])
        self.assertTrue(action["evidence"]["cleanup"]["enter_seen"])
        self.assertFalse(action["evidence"]["cleanup"]["exit_seen"])
        self.assertEqual(provider.destroyed, [])

    def test_reaper_dry_run_never_destroys_provider_instances(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.save(_add_cleanup_evidence(_job("done-a", status="failed", provider_job_id="301")))
            store.save(_add_cleanup_evidence(_job("done-b", status="succeeded", provider_job_id="302")))
            provider = FakeVastProvider(
                [
                    _instance("301", "gpu-job:done-a"),
                    _instance("302", "gpu-job:done-b"),
                ]
            )

            result = vast_orphan_reaper(store=store, provider=provider, checked_at=1)

        self.assertEqual(result["mode"], "dry_run")
        self.assertEqual(result["summary"]["eligible_count"], 2)
        self.assertEqual(provider.destroyed, [])

    def test_reaper_apply_requires_destructive_preflight(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.save(_add_cleanup_evidence(_job("done-job", status="failed", provider_job_id="205")))
            provider = FakeVastProvider([_instance("205", "gpu-job:done-job")])

            with patch("gpu_job.orphan_inventory.destructive_preflight", return_value={"ok": False, "reason": "blocked"}):
                result = vast_orphan_reaper(apply=True, principal="operator", store=store, provider=provider, checked_at=1)

        self.assertEqual(result["actions"][0]["skip_reason"], "destructive_preflight_failed")
        self.assertEqual(provider.destroyed, [])

    def test_reaper_apply_fresh_reads_before_destroy(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.save(_add_cleanup_evidence(_job("done-job", status="failed", provider_job_id="206")))
            provider = FakeVastProvider([_instance("206", "gpu-job:done-job")])

            with patch("gpu_job.orphan_inventory.destructive_preflight", return_value={"ok": True, "reason": "approved"}):
                result = vast_orphan_reaper(apply=True, principal="operator", store=store, provider=provider, checked_at=1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["actions"][0]["destroy_result"]["ok"], True)
        self.assertEqual(provider.destroyed, ["206"])

    def test_reaper_apply_skips_when_fresh_read_no_longer_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            store.save(_add_cleanup_evidence(_job("done-job", status="failed", provider_job_id="207")))
            provider = FakeVastProvider([_instance("207", "gpu-job:done-job")], fresh_instances=[])

            with patch("gpu_job.orphan_inventory.destructive_preflight", return_value={"ok": True, "reason": "approved"}):
                result = vast_orphan_reaper(apply=True, principal="operator", store=store, provider=provider, checked_at=1)

        self.assertEqual(result["actions"][0]["skip_reason"], "fresh_instance_not_found")
        self.assertEqual(provider.destroyed, [])


if __name__ == "__main__":
    unittest.main()
