from __future__ import annotations

import unittest
import os
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gpu_job import api
from gpu_job.models import Job
from gpu_job.runner import submit_job
from gpu_job.store import JobStore
from gpu_job.timing import enter_phase, ensure_received, exit_phase, instant_phase, public_timing, timing_summary


def make_job() -> Job:
    return Job(
        job_id="timing-v2-test",
        job_type="asr",
        input_uri="/tmp/input.mp4",
        output_uri="local://out",
        worker_image="auto",
        gpu_profile="asr_diarization",
        provider="vast",
    )


class TimingV2Test(unittest.TestCase):
    def test_summary_preserves_attempts_and_phase_totals(self) -> None:
        job = make_job()
        ensure_received(job)
        enter_phase(job, "image_materialization", at=100.0, attempt=1, provider="vast")
        exit_phase(job, "image_materialization", at=220.0, attempt=1, provider="vast", status="failed", error_class="startup_failed")
        enter_phase(job, "image_materialization", at=230.0, attempt=2, provider="vast")
        exit_phase(job, "image_materialization", at=290.0, attempt=2, provider="vast", status="ready")
        enter_phase(job, "running_worker", at=300.0, attempt=2, provider="vast")
        exit_phase(job, "running_worker", at=333.752, attempt=2, provider="vast", status="ok")
        instant_phase(job, "succeeded", at=340.0, attempt=2, provider="vast", status="succeeded")

        summary = timing_summary(job)

        self.assertEqual(summary["version"], "gpu-job-timing-v2")
        self.assertEqual(summary["phase_totals"]["image_materialization"], 180.0)
        self.assertEqual(summary["phase_totals"]["running_worker"], 33.752)
        materialization = [row for row in summary["phases"] if row["phase"] == "image_materialization"]
        self.assertEqual([row["attempt"] for row in materialization], [1, 2])
        self.assertEqual(materialization[0]["error_class"], "startup_failed")

    def test_summary_pairs_events_by_append_order_not_wall_clock_order(self) -> None:
        job = make_job()
        enter_phase(job, "running_worker", at=200.0, provider="vast")
        exit_phase(job, "running_worker", at=199.0, provider="vast", status="ok")

        summary = timing_summary(job)

        self.assertEqual(summary["phase_totals"]["running_worker"], 0.0)
        self.assertEqual(len([row for row in summary["phases"] if row["phase"] == "running_worker"]), 1)
        self.assertFalse(summary["phases"][0].get("open", False))

    def test_public_timing_contains_sanitized_events(self) -> None:
        job = make_job()
        enter_phase(job, "staging_input", at=10.0, provider="vast")
        exit_phase(job, "staging_input", at=12.0, provider="vast", status="ok")
        instant_phase(job, "failed", at=13.0, provider="vast", status="bad status with spaces", error_class="https://secret.example")

        timing = public_timing(job)

        self.assertEqual(timing["summary"]["phase_totals"]["staging_input"], 2.0)
        self.assertEqual([row["event_id"] for row in timing["events"]], ["000000000001", "000000000002", "000000000003"])
        self.assertEqual(timing["events"][0]["phase"], "staging_input")
        self.assertEqual(timing["events"][2]["status"], "other")
        self.assertEqual(timing["events"][2]["error_class"], "other")
        self.assertNotIn("input_uri", timing["events"][0])
        self.assertNotIn("error", timing["events"][0])

    def test_api_job_response_exposes_timing_v2(self) -> None:
        old_data_home = os.environ.get("XDG_DATA_HOME")
        with TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            try:
                store = JobStore()
                job = make_job()
                enter_phase(job, "running_worker", at=1.0, provider="vast")
                exit_phase(job, "running_worker", at=4.0, provider="vast", status="ok")
                store.save(job)

                response = api._job_response(job)

                self.assertEqual(response["timing_v2"]["summary"]["phase_totals"]["running_worker"], 3.0)
            finally:
                if old_data_home is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_data_home

    def test_blocked_submit_has_terminal_timing(self) -> None:
        old_data_home = os.environ.get("XDG_DATA_HOME")
        with TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            try:
                job = make_job()
                with patch("gpu_job.runner.validate_policy", return_value={"ok": False, "errors": ["bad policy"]}):
                    result = submit_job(job, provider_name="vast", execute=False)

                self.assertFalse(result["ok"])
                events = result["job"]["metadata"]["timing_v2"]["events"]
                self.assertEqual(events[-1]["phase"], "failed")
                self.assertEqual(events[-1]["status"], "failed")
            finally:
                if old_data_home is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_data_home

    def test_capacity_backpressure_has_terminal_timing(self) -> None:
        old_data_home = os.environ.get("XDG_DATA_HOME")
        with TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            try:
                job = make_job()
                capacity = {"ok": False, "error": "capacity exhausted", "retry_after_seconds": 30}
                with (
                    patch("gpu_job.runner.validate_policy", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_provenance", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_compliance", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_model_capability", return_value={"ok": True}),
                    patch("gpu_job.runner.quota_check", return_value={"ok": True}),
                    patch("gpu_job.runner.cost_estimate", return_value={"ok": True}),
                    patch("gpu_job.runner.secret_check", return_value={"ok": True}),
                    patch("gpu_job.runner.placement_check", return_value={"ok": True}),
                    patch("gpu_job.runner.preemption_check", return_value={"ok": True}),
                    patch("gpu_job.runner.timeout_contract", return_value={"ok": True, "timeout_seconds": 60}),
                    patch("gpu_job.runner.provider_workspace_plan", return_value={"decision": "ready"}),
                    patch("gpu_job.runner.make_decision", return_value={"decision_hash": "test"}),
                    patch("gpu_job.runner.collect_cost_guard", return_value={"ok": True}),
                    patch("gpu_job.runner.reserve_direct_execution_slot", return_value=capacity),
                ):
                    result = submit_job(job, provider_name="vast", execute=True)

                self.assertFalse(result["ok"])
                events = result["job"]["metadata"]["timing_v2"]["events"]
                self.assertEqual(events[-1]["phase"], "failed")
                self.assertEqual(events[-1]["status"], "failed")
                self.assertIn("reserving_workspace", [row["phase"] for row in events])
            finally:
                if old_data_home is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_data_home

    def test_plan_only_submit_does_not_emit_failed_terminal_phase(self) -> None:
        class FakeProvider:
            def submit(self, job: Job, store: JobStore, execute: bool = False) -> Job:
                job.status = "planned"
                store.save(job)
                return job

        old_data_home = os.environ.get("XDG_DATA_HOME")
        with TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            try:
                job = make_job()
                with (
                    patch("gpu_job.runner.validate_policy", return_value={"ok": True}),
                    patch("gpu_job.runner.provider_circuit_state", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_provenance", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_compliance", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_model_capability", return_value={"ok": True}),
                    patch("gpu_job.runner.quota_check", return_value={"ok": True}),
                    patch("gpu_job.runner.cost_estimate", return_value={"ok": True}),
                    patch("gpu_job.runner.secret_check", return_value={"ok": True}),
                    patch("gpu_job.runner.placement_check", return_value={"ok": True}),
                    patch("gpu_job.runner.preemption_check", return_value={"ok": True}),
                    patch("gpu_job.runner.timeout_contract", return_value={"ok": True, "timeout_seconds": 60}),
                    patch("gpu_job.runner.make_decision", return_value={"decision_hash": "test"}),
                    patch("gpu_job.runner.get_provider", return_value=FakeProvider()),
                ):
                    result = submit_job(job, provider_name="vast", execute=False)

                self.assertTrue(result["ok"])
                events = result["job"]["metadata"]["timing_v2"]["events"]
                self.assertIn("planned", [row["phase"] for row in events])
                self.assertNotIn("failed", [row["phase"] for row in events])
            finally:
                if old_data_home is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_data_home


if __name__ == "__main__":
    unittest.main()
