from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from gpu_job.execution_plan import build_execution_plan, execution_plan_schema
from gpu_job.models import Job
from gpu_job.providers.modal import ModalProvider
from gpu_job.providers.runpod import RunPodProvider
from gpu_job.providers.vast import VastProvider


class ExecutionPlanContractTest(unittest.TestCase):
    def test_execution_plan_schema_exposes_required_fields_and_invariants(self) -> None:
        schema = execution_plan_schema()

        self.assertEqual(schema["execution_plan_version"], "gpu-job-execution-plan-v1")
        self.assertIn("provider_support_contract", schema["required_fields"])
        self.assertIn("staging", schema["required_fields"])
        self.assertIn("command_shape", schema["required_fields"])
        self.assertTrue(any("command[0]" in item for item in schema["invariants"]))
        self.assertTrue(any("secret_refs is sorted" in item for item in schema["invariants"]))

    def test_execution_plan_is_deterministic_for_same_job_and_provider(self) -> None:
        job = _asr_job("execution-plan-deterministic")
        job.metadata["secret_refs"] = ["z_secret", "hf_token", "hf_token"]

        first = build_execution_plan(job, "runpod")
        second = build_execution_plan(job, "runpod")

        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertEqual(first["secret_refs"], sorted(set(first["secret_refs"])))
        self.assertEqual(first["entrypoint"], first["command"][0])
        self.assertTrue(first["command_shape"]["tokenized"])

    def test_asr_backend_matrix_is_deterministic(self) -> None:
        plain = _asr_job("execution-plan-asr-plain", diarize=False)
        diarized = _asr_job("execution-plan-asr-diarized", diarize=True)
        smoke = Job(
            job_id="execution-plan-smoke",
            job_type="smoke",
            input_uri="none://probe",
            output_uri="local://out",
            worker_image="auto",
            gpu_profile="embedding",
        )

        plain_plan = build_execution_plan(plain, "modal")
        diarized_plan = build_execution_plan(diarized, "modal")
        smoke_plan = build_execution_plan(smoke, "modal")

        self.assertEqual(plain_plan["required_backends"], ["faster_whisper"])
        self.assertEqual(diarized_plan["required_backends"], ["faster_whisper", "pyannote"])
        self.assertEqual(smoke_plan["required_backends"], [])
        self.assertEqual(smoke_plan["image_contract"]["status"], "not_required")
        self.assertIn("contract", smoke_plan["image_contract"])

    def test_provider_plans_share_execution_plan_required_shape(self) -> None:
        job = _asr_job("execution-plan-provider-shape")
        required = set(execution_plan_schema()["required_fields"])

        with (
            patch.object(RunPodProvider, "_api_snapshot", return_value={"endpoints": []}),
            patch.object(VastProvider, "offers", return_value={"query": "gpu_ram>=24", "offers": []}),
        ):
            plans = [ModalProvider().plan(job), RunPodProvider().plan(job), VastProvider().plan(job)]

        for provider_plan in plans:
            execution_plan = provider_plan["execution_plan"]
            self.assertTrue(required.issubset(execution_plan), provider_plan["provider"])
            self.assertEqual(execution_plan["provider"], provider_plan["provider"])
            self.assertEqual(execution_plan["entrypoint"], execution_plan["command"][0])
            self.assertEqual(execution_plan["staging"]["staged_input_placeholder"], "<staged-input-path>")
            self.assertIn("support_contract_version", execution_plan["provider_support_contract"])

    def test_worker_image_resolution_prefers_provider_image_when_available(self) -> None:
        job = _asr_job("execution-plan-provider-image")

        plan = build_execution_plan(job, "runpod")

        if plan["provider_image"]:
            self.assertEqual(plan["worker_image"], plan["provider_image"])
        else:
            self.assertTrue(plan["worker_image"])


def _asr_job(job_id: str, *, diarize: bool = True) -> Job:
    return Job(
        job_id=job_id,
        job_type="asr",
        input_uri="file:///tmp/input.mp4",
        output_uri="local://out",
        worker_image="gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
        gpu_profile="asr_diarization",
        model="large-v3",
        metadata={"input": {"diarize": diarize, "language": "ja"}, "secret_refs": ["hf_token"]},
    )


if __name__ == "__main__":
    unittest.main()
