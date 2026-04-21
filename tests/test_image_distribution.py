from __future__ import annotations

import unittest

from gpu_job.image import image_contract_check, image_contract_plan, image_contract_probe, image_mirror, image_mirror_plan
from gpu_job.workers.asr import probe_runtime


class ImageDistributionTest(unittest.TestCase):
    def test_mirror_plan_uses_remote_builder_when_given(self) -> None:
        result = image_mirror_plan(
            "ghcr.io/example/gpu-job@sha256:abc",
            "registry.example.com/gpu-job@sha256:abc",
            builder="gpu-builder",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["builder"], "gpu-builder")
        self.assertEqual(result["command"][0], "ssh")
        self.assertIn("operator-controlled registry", result["runtime_policy"])

    def test_mirror_without_execute_is_plan_only(self) -> None:
        result = image_mirror(
            "ghcr.io/example/gpu-job@sha256:abc",
            "registry.example.com/gpu-job@sha256:abc",
            builder="gpu-builder",
            execute=False,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["planned"])

    def test_image_contract_plan_uses_registered_contract(self) -> None:
        result = image_contract_plan("asr-diarization-large-v3-pyannote3.3.2-cuda12.4")

        self.assertTrue(result["ok"])
        self.assertEqual(result["image"], "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4")
        self.assertEqual(result["dockerfile"], "docker/asr-worker.Dockerfile")
        self.assertEqual(result["probe_command"], ["gpu-job-asr-worker", "--probe-runtime", "--diarize"])

    def test_image_contract_check_validates_dockerfile_and_required_fields(self) -> None:
        result = image_contract_check("asr-diarization-large-v3-pyannote3.3.2-cuda12.4")

        self.assertTrue(result["ok"])
        self.assertEqual(result["missing_fields"], [])
        self.assertTrue(result["dockerfile_check"]["ok"])

    def test_runpod_serverless_asr_handler_contract_is_registered_but_unverified(self) -> None:
        result = image_contract_plan("asr-diarization-runpod-serverless-large-v3-pyannote3.3.2-cuda12.4")
        check = image_contract_check("asr-diarization-runpod-serverless-large-v3-pyannote3.3.2-cuda12.4")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["image"], "gpu-job/asr-diarization-runpod-serverless:large-v3-pyannote3.3.2-cuda12.4")
        self.assertEqual(result["contract"]["entrypoint"], "gpu-job-runpod-asr-worker")
        self.assertEqual(result["dockerfile"], "docker/runpod-asr-worker.Dockerfile")
        self.assertTrue(check["ok"])
        self.assertTrue(check["dockerfile_check"]["ok"])

    def test_image_contract_probe_without_execute_is_plan_only(self) -> None:
        result = image_contract_probe("asr-diarization-large-v3-pyannote3.3.2-cuda12.4", execute=False)

        self.assertTrue(result["ok"])
        self.assertTrue(result["planned"])
        self.assertIn("--probe-runtime", result["command"])

    def test_asr_runtime_probe_imports_fast_backend(self) -> None:
        result = probe_runtime(diarize=False, require_gpu=False)

        self.assertIn("faster_whisper_import", result["checks"])
        self.assertIn("ffmpeg_present", result["checks"])


if __name__ == "__main__":
    unittest.main()
