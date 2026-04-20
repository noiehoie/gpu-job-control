from __future__ import annotations

import json
import unittest

from gpu_job.execution_record import build_execution_record
from gpu_job.models import Job


class ExecutionRecordContractTest(unittest.TestCase):
    def test_execution_record_derives_quote_from_workspace_plan_when_missing(self) -> None:
        job = Job(
            job_id="exec-record-derived-quote",
            job_type="asr",
            input_uri="file:///tmp/input.mp4",
            output_uri="local://out",
            worker_image="gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
            gpu_profile="asr_diarization",
            model="large-v3",
            metadata={
                "selected_provider": "runpod",
                "workspace_plan": {
                    "workspace_registry_version": "gpu-job-provider-workspace-registry-v1",
                    "workspace_plan_id": "workspace-test-derived",
                    "provider": "runpod",
                    "job_id": "exec-record-derived-quote",
                    "job_type": "asr",
                    "gpu_profile": "asr_diarization",
                    "catalog_version": "gpu-job-provider-catalog-v1",
                    "catalog_snapshot_id": "cat-test",
                    "decision": "ready",
                    "required_actions": [],
                    "provider_capability": {
                        "provider": "runpod",
                        "estimated_startup_seconds": 120,
                        "supported_job_types": ["asr"],
                    },
                    "provider_runtime": {
                        "contract_probe": "runpod.asr_diarization.pyannote",
                        "image_contract_id": "asr-diarization-large-v3-pyannote3.3.2-cuda12.4",
                    },
                },
            },
        )

        first = build_execution_record(job)
        second = build_execution_record(job)
        quote = first["plan_quote"]

        self.assertEqual(quote["quote_version"], "gpu-job-plan-quote-v1")
        self.assertTrue(quote["quote_id"].startswith("quote-"))
        self.assertEqual(quote["quote_hash"], second["plan_quote"]["quote_hash"])
        self.assertEqual(quote["catalog_snapshot_id"], "cat-test")
        self.assertTrue(quote["can_run_now"])
        self.assertEqual(quote["selected_option"]["provider"], "runpod")
        self.assertEqual(quote["selected_option"]["workspace_plan_id"], "workspace-test-derived")
        self.assertEqual(quote["action_requirements"]["decision"], "ready")

    def test_execution_record_derived_quote_hash_changes_with_workspace_plan(self) -> None:
        job = Job(
            job_id="exec-record-derived-quote-change",
            job_type="asr",
            input_uri="file:///tmp/input.mp4",
            output_uri="local://out",
            worker_image="gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
            gpu_profile="asr_diarization",
            model="large-v3",
            metadata={
                "selected_provider": "vast",
                "workspace_plan": {
                    "workspace_plan_id": "workspace-a",
                    "provider": "vast",
                    "decision": "requires_action",
                    "required_actions": [{"type": "run_contract_probe", "contract_probe": "vast.asr_diarization.pyannote"}],
                },
            },
        )

        first = build_execution_record(job)["plan_quote"]
        job.metadata["workspace_plan"]["workspace_plan_id"] = "workspace-b"
        second = build_execution_record(job)["plan_quote"]

        self.assertNotEqual(first["quote_hash"], second["quote_hash"])
        self.assertFalse(first["can_run_now"])
        self.assertEqual(first["approval"]["decision"], "requires_action")
        self.assertEqual(
            json.dumps(first["action_requirements"], sort_keys=True),
            json.dumps(
                {
                    "decision": "requires_action",
                    "required_actions": job.metadata["workspace_plan"]["required_actions"],
                },
                sort_keys=True,
            ),
        )


if __name__ == "__main__":
    unittest.main()
