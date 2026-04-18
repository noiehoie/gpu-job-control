from __future__ import annotations

import os
import unittest
from tempfile import TemporaryDirectory

from gpu_job.models import Job, now_unix
from gpu_job.queue import cancel_job, recover_stale_jobs
from gpu_job.store import JobStore


def running_job(*, started_delta: int = 600, max_runtime_minutes: float = 5) -> Job:
    now = now_unix()
    return Job(
        job_id="active-timeout-test",
        job_type="vlm_ocr",
        input_uri="text://image",
        output_uri="local://out",
        worker_image="auto",
        gpu_profile="vlm_ocr",
        provider="modal",
        status="running",
        limits={"max_runtime_minutes": max_runtime_minutes},
        created_at=now - started_delta,
        updated_at=now - started_delta,
        started_at=now - started_delta,
        metadata={"selected_provider": "modal"},
    )


class ActiveTimeoutRecoveryTest(unittest.TestCase):
    def test_recover_stale_jobs_uses_job_timeout_contract(self) -> None:
        old_data_home = os.environ.get("XDG_DATA_HOME")
        with TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            store = JobStore()
            store.save(running_job(started_delta=600, max_runtime_minutes=5))

            recovered = recover_stale_jobs({"stale_seconds": {"running": 14400}})
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["status"], "failed")
            self.assertEqual(recovered[0]["exit_code"], 124)
            self.assertIn("stale running job exceeded 300s", recovered[0]["error"])
            saved = store.load("active-timeout-test")
            self.assertEqual(saved.status, "failed")
            self.assertEqual(saved.metadata["stale_recovery"]["threshold_seconds"], 300)
        if old_data_home is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_data_home

    def test_force_cancel_active_job_only_after_timeout(self) -> None:
        old_data_home = os.environ.get("XDG_DATA_HOME")
        with TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            store = JobStore()
            store.save(running_job(started_delta=600, max_runtime_minutes=5))

            result = cancel_job("active-timeout-test", force=True, reason="orphaned pipeline job")
            self.assertTrue(result["ok"])
            self.assertTrue(result["forced"])
            self.assertEqual(result["job"]["status"], "failed")
            self.assertEqual(result["job"]["exit_code"], 124)
            self.assertEqual(result["job"]["error"], "orphaned pipeline job")
        if old_data_home is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_data_home

    def test_force_cancel_active_job_before_timeout_is_rejected(self) -> None:
        old_data_home = os.environ.get("XDG_DATA_HOME")
        with TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            JobStore().save(running_job(started_delta=10, max_runtime_minutes=5))

            with self.assertRaisesRegex(ValueError, "before timeout"):
                cancel_job("active-timeout-test", force=True)
        if old_data_home is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_data_home


if __name__ == "__main__":
    unittest.main()
