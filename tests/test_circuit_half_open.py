from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
import os
from unittest.mock import patch

from gpu_job.circuit import provider_circuit_state
from gpu_job.models import Job, now_unix
from gpu_job.runner import submit_job
from gpu_job.store import JobStore


def _job(job_id: str, status: str, provider: str = "modal", updated_at: int | None = None, metadata: dict | None = None) -> Job:
    timestamp = updated_at if updated_at is not None else now_unix()
    job_metadata = {"selected_provider": provider}
    if metadata:
        job_metadata.update(metadata)
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
        metadata=job_metadata,
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

            store.save(_job("unrelated-success", "succeeded", updated_at=now + 10))
            still_open_state = provider_circuit_state("modal", store=store)
            self.assertFalse(still_open_state["ok"])
            self.assertEqual(still_open_state["state"], "open")
            self.assertTrue(still_open_state["latest_success_after_failure"])
            self.assertFalse(still_open_state["latest_success_is_circuit_probe"])

            store.save(_job("probe-success", "succeeded", updated_at=now + 11, metadata={"circuit_probe": open_state}))
            closed_state = provider_circuit_state("modal", store=store)
            self.assertTrue(closed_state["ok"])
            self.assertEqual(closed_state["state"], "closed")
            self.assertTrue(closed_state["latest_success_after_failure"])
            self.assertTrue(closed_state["latest_success_is_circuit_probe"])
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
                patch("gpu_job.runner.make_decision", return_value={"decision_hash": "test"}),
            ):
                result = submit_job(job, provider_name="modal", execute=False)

            self.assertTrue(result["ok"])
            self.assertIn("circuit_probe", result["job"]["metadata"])
        if old_data_home is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_data_home

    def test_execute_cost_guard_defaults_to_selected_provider_only(self) -> None:
        class FakeProvider:
            def submit(self, job: Job, store: JobStore, execute: bool = False) -> Job:
                job.status = "succeeded"
                job.exit_code = 0
                store.save(job)
                return job

        old_data_home = os.environ.get("XDG_DATA_HOME")
        with TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            job = _job("guard-scope", "planned", updated_at=now_unix())
            guard_calls: list[list[str] | None] = []

            def _guard(provider_names=None):
                guard_calls.append(provider_names)
                return {"ok": True, "providers": provider_names}

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
                patch("gpu_job.runner.make_decision", return_value={"decision_hash": "test"}),
                patch("gpu_job.runner.reserve_direct_execution_slot", return_value={"ok": True}),
                patch("gpu_job.runner.collect_cost_guard", side_effect=_guard),
                patch("gpu_job.runner.get_provider", return_value=FakeProvider()),
            ):
                result = submit_job(job, provider_name="modal", execute=True)

            self.assertTrue(result["ok"])
            self.assertEqual(guard_calls, [["modal"], ["modal"]])
        if old_data_home is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_data_home


if __name__ == "__main__":
    unittest.main()
