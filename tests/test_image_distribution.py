from __future__ import annotations

import unittest
from unittest.mock import patch

from gpu_job.image import (
    image_build,
    image_contract_build,
    image_contract_check,
    image_contract_plan,
    image_contract_probe,
    image_mirror,
    image_mirror_plan,
)
from gpu_job.workers.asr import probe_runtime


class ImageDistributionTest(unittest.TestCase):
    def test_mirror_plan_uses_remote_builder_when_given(self) -> None:
        result = image_mirror_plan(
            "ghcr.io/example/gpu-job@sha256:abc",
            "registry.example.com/gpu-job@sha256:abc",
            builder="netcup",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["builder"], "netcup")
        self.assertEqual(result["command"][0], "ssh")
        self.assertIn("operator-controlled registry", result["runtime_policy"])

    def test_mirror_without_execute_is_plan_only(self) -> None:
        result = image_mirror(
            "ghcr.io/example/gpu-job@sha256:abc",
            "registry.example.com/gpu-job@sha256:abc",
            builder="netcup",
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

    def test_runpod_serverless_asr_handler_contract_is_registered_and_verified(self) -> None:
        result = image_contract_plan("asr-diarization-runpod-serverless-large-v3-pyannote3.3.2-cuda12.4")
        check = image_contract_check("asr-diarization-runpod-serverless-large-v3-pyannote3.3.2-cuda12.4")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["image"].startswith("ghcr.io/noiehoie/gpu-job-control-runpod-asr@sha256:"))
        self.assertEqual(result["contract"]["entrypoint"], "python3.11 -u /rp_handler.py")
        self.assertEqual(result["dockerfile"], "docker/runpod-asr-worker.Dockerfile")
        self.assertTrue(check["ok"])
        self.assertTrue(check["dockerfile_check"]["ok"])

    def test_runpod_official_worker_vllm_reference_contract_is_registered(self) -> None:
        result = image_contract_plan("llm-vllm-runpod-worker-vllm-reference")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "reference_only")
        self.assertEqual(result["contract"]["provider_images"]["runpod"]["source_repo"], "https://github.com/runpod-workers/worker-vllm")
        self.assertEqual(result["image"], "registry.runpod.net/runpod-workers-worker-vllm-main-dockerfile:17efb0e7d")
        self.assertIn("openai_compatible", result["contract"]["provides_backends"])

    def test_image_contract_probe_without_execute_is_plan_only(self) -> None:
        result = image_contract_probe("asr-diarization-large-v3-pyannote3.3.2-cuda12.4", execute=False)

        self.assertTrue(result["ok"])
        self.assertTrue(result["planned"])
        self.assertIn("--probe-runtime", result["command"])

    def test_image_contract_build_fails_closed_when_local_docker_missing(self) -> None:
        with (
            patch("gpu_job.image.platform.system", return_value="Linux"),
            patch.dict("os.environ", {"GPU_JOB_ALLOW_LOCAL_DOCKER": "1"}, clear=False),
            patch("gpu_job.image.which", return_value=None),
        ):
            result = image_contract_build("asr-diarization-runpod-serverless-large-v3-pyannote3.3.2-cuda12.4", execute=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "docker binary not found")
        self.assertEqual(result["requires_action"], "install_docker_or_configure_remote_builder")

    def test_image_contract_probe_fails_closed_when_local_docker_missing(self) -> None:
        with (
            patch("gpu_job.image.platform.system", return_value="Linux"),
            patch.dict("os.environ", {"GPU_JOB_ALLOW_LOCAL_DOCKER": "1"}, clear=False),
            patch("gpu_job.image.which", return_value=None),
        ):
            result = image_contract_probe("asr-diarization-runpod-serverless-large-v3-pyannote3.3.2-cuda12.4", execute=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "docker binary not found")
        self.assertEqual(result["requires_action"], "install_docker_or_configure_remote_builder")

    def test_local_docker_build_is_forbidden_on_macos_even_when_enabled(self) -> None:
        with (
            patch("gpu_job.image.platform.system", return_value="Darwin"),
            patch.dict("os.environ", {"GPU_JOB_ALLOW_LOCAL_DOCKER": "1"}, clear=False),
        ):
            result = image_contract_build("asr-diarization-runpod-serverless-large-v3-pyannote3.3.2-cuda12.4", execute=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "local_docker_forbidden_on_macos")
        self.assertEqual(result["requires_action"], "use_remote_linux_builder_or_ci")

    def test_legacy_image_build_is_forbidden_on_macos_even_when_enabled(self) -> None:
        with (
            patch("gpu_job.image.platform.system", return_value="Darwin"),
            patch.dict("os.environ", {"GPU_JOB_ALLOW_LOCAL_DOCKER": "1"}, clear=False),
        ):
            result = image_build("asr", execute=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "local_docker_forbidden_on_macos")
        self.assertEqual(result["requires_action"], "use_remote_linux_builder_or_ci")

    def test_asr_runtime_probe_imports_fast_backend(self) -> None:
        result = probe_runtime(diarize=False, require_gpu=False)

        self.assertIn("faster_whisper_import", result["checks"])
        self.assertIn("ffmpeg_present", result["checks"])


if __name__ == "__main__":
    unittest.main()
