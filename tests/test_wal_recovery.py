from __future__ import annotations

import os
import unittest
from tempfile import TemporaryDirectory

from gpu_job.models import Job
from gpu_job.store import JobStore
from gpu_job.wal import append_wal, wal_recovery_status


class WalRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_data_home = os.environ.get("XDG_DATA_HOME")
        self.tmp = TemporaryDirectory()
        os.environ["XDG_DATA_HOME"] = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()
        if self.old_data_home is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self.old_data_home

    def test_terminal_job_resolves_missing_provider_final_wal(self) -> None:
        store = JobStore()
        job = _job("wal-terminal")
        append_wal(job, transition="starting->provider_submit", intent="provider_submit", extra={"provider": "modal", "execute": True}, store=store)
        job.status = "failed"
        job.finished_at = job.created_at + 300
        job.exit_code = 124
        job.error = "stale running job exceeded 300s"
        store.save(job)

        status = wal_recovery_status(store=store)

        self.assertTrue(status["ok"])
        self.assertEqual(status["ambiguous_count"], 0)
        self.assertEqual(status["resolved_terminal_count"], 1)
        self.assertEqual(status["resolved_terminal_provider_submits"][0]["job_id"], "wal-terminal")

    def test_non_terminal_job_remains_ambiguous(self) -> None:
        store = JobStore()
        job = _job("wal-running")
        append_wal(job, transition="starting->provider_submit", intent="provider_submit", extra={"provider": "modal", "execute": True}, store=store)
        job.status = "running"
        store.save(job)

        status = wal_recovery_status(store=store)

        self.assertFalse(status["ok"])
        self.assertEqual(status["ambiguous_count"], 1)


def _job(job_id: str) -> Job:
    return Job(
        job_id=job_id,
        job_type="llm_heavy",
        input_uri="text://hello",
        output_uri="local://out",
        worker_image="auto",
        gpu_profile="llm_heavy",
        provider="modal",
        status="starting",
        metadata={"selected_provider": "modal"},
    )


if __name__ == "__main__":
    unittest.main()
