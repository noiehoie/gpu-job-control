from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
import os
from unittest.mock import patch

from gpu_job.circuit import provider_circuit_state
from gpu_job.models import Job, now_unix
from gpu_job.runner import submit_job
from gpu_job.store import JobStore


def _job(job_id: str, status: str, provider: str = "modal", updated_at: int | None = None) -> Job:
    timestamp = updated_at if updated_at is not None else now_unix()
    return Job(
        job_id=job_id,
        job_type="llm_heavy",
        input_uri="text://hello",
        output_uri="local://out",
        worker_image="auto",
        gpu_profile="llm_heavy",
        provider=provider,
        status=status,
        created_at=timestamp,
        updated_at=timestamp,
        metadata={"selected_provider": provider},
    )


class CircuitHalfOpenTest(unittest.TestCase):
    def test_success_after_failures_closes_circuit(self) -> None:
        old_data_home = os.environ.get("XDG_DATA_HOME")
        with TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            store = JobStore()
            now = now_unix()
            for idx in range(5):
                store.save(_job(f"failed-{idx}", "failed", updated_at=now + idx))
            open_state = provider_circuit_state("modal", store=store)
            self.assertFalse(open_state["ok"])
            self.assertTrue(open_state["half_open_probe_allowed"])

            store.save(_job("probe-success", "succeeded", updated_at=now + 10))
            closed_state = provider_circuit_state("modal", store=store)
            self.assertTrue(closed_state["ok"])
            self.assertEqual(closed_state["state"], "closed")
            self.assertTrue(closed_state["latest_success_after_failure"])
        if old_data_home is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_data_home

    def test_submit_allows_half_open_probe(self) -> None:
        old_data_home = os.environ.get("XDG_DATA_HOME")
        with TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            store = JobStore()
            now = now_unix()
            for idx in range(5):
                store.save(_job(f"failed-{idx}", "failed", updated_at=now + idx))

            job = _job("new-probe", "planned", updated_at=now + 20)
            with patch("gpu_job.runner.validate_policy", return_value={"ok": True}), \
                patch("gpu_job.runner.evaluate_provenance", return_value={"ok": True}), \
                patch("gpu_job.runner.evaluate_compliance", return_value={"ok": True}), \
                patch("gpu_job.runner.evaluate_model_capability", return_value={"ok": True}), \
                patch("gpu_job.runner.quota_check", return_value={"ok": True}), \
                patch("gpu_job.runner.cost_estimate", return_value={"ok": True}), \
                patch("gpu_job.runner.secret_check", return_value={"ok": True}), \
                patch("gpu_job.runner.placement_check", return_value={"ok": True}), \
                patch("gpu_job.runner.preemption_check", return_value={"ok": True}), \
                patch("gpu_job.runner.timeout_contract", return_value={"ok": True, "timeout_seconds": 60}), \
                patch("gpu_job.runner.make_decision", return_value={"decision_hash": "test"}):
                result = submit_job(job, provider_name="modal", execute=False)

            self.assertTrue(result["ok"])
            self.assertIn("circuit_probe", result["job"]["metadata"])
        if old_data_home is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_data_home


if __name__ == "__main__":
    unittest.main()
