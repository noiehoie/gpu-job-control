from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import unittest

from gpu_job.provider_contract_probe import (
    DEFAULT_CONTRACT_PROBES,
    WORKSPACE_OBSERVATION_CATEGORIES,
    active_contract_probe,
    list_contract_probes,
    parse_contract_probe_artifact,
    plan_contract_probe,
    provider_contract_probe_schema,
    recent_contract_probe_summary,
    workspace_observation_coverage,
    _canary_job,
)

ASR_PROVIDER_IMAGE = (
    "ghcr.io/noiehoie/gpu-job-control/asr-diarization-worker:"
    "large-v3-pyannote3.3.2-cuda12.4-cmd@"
    "sha256:b06fd86a4d43d8fec294675e3ac7d3135934c61d422df567a5976636fb240be8"
)
ASR_PROVIDER_IMAGE_DIGEST = "sha256:b06fd86a4d43d8fec294675e3ac7d3135934c61d422df567a5976636fb240be8"


class ProviderContractProbeTest(unittest.TestCase):
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

    def test_schema_and_plan_are_provider_contract_specific(self) -> None:
        schema = provider_contract_probe_schema()
        listed = list_contract_probes()
        plan = plan_contract_probe("modal", "modal.llm_heavy.qwen2_5_32b")

        self.assertIn("modal.llm_heavy.qwen2_5_32b", schema["probe_names"])
        self.assertIn("workspace_observation_categories", schema)
        self.assertIn("startup_phases", schema["workspace_observation_categories"])
        self.assertIn("provider_residue", schema["workspace_observation_categories"])
        self.assertEqual(
            schema["workspace_observation_coverage"]["coverage_version"],
            "gpu-job-workspace-observation-coverage-v1",
        )
        self.assertIn("read-side observation", schema["workspace_observation_coverage"]["rule"])
        self.assertIn("admin-only", schema["canary_rule"])
        self.assertIn("modal.llm_heavy.qwen2_5_32b", listed["probes"])
        self.assertEqual(plan["execution_mode"], "planned")
        self.assertEqual(plan["spec"]["expected_model"], "Qwen/Qwen2.5-32B-Instruct")

    def test_contract_probe_canary_jobs_use_dedicated_secret_scope(self) -> None:
        job = _canary_job(DEFAULT_CONTRACT_PROBES["runpod.asr_diarization.pyannote"])

        self.assertEqual(job.metadata["source_system"], "contract-probe")
        self.assertEqual(job.metadata["secret_refs"], ["hf_token"])

    def test_modal_success_warm_cache_passes(self) -> None:
        path = self._artifact(
            result={"text": "ok", "model": "Qwen/Qwen2.5-32B-Instruct"},
            metrics={
                "model": "Qwen/Qwen2.5-32B-Instruct",
                "gpu_utilization_percent": 15,
                "gpu_memory_used_mb": 42000,
                "cache_hit": True,
                "worker_image": "gpu-job-modal-llm",
            },
            verify={"ok": True, "checks": {"text_nonempty": True}},
            stdout="Loaded model Qwen/Qwen2.5-32B-Instruct from cache\n",
        )

        record = parse_contract_probe_artifact(
            path,
            provider="modal",
            probe_name="modal.llm_heavy.qwen2_5_32b",
            append=True,
        )

        self.assertTrue(record["ok"])
        self.assertEqual(record["verdict"], "pass")
        self.assertEqual(record["observed"]["model"], "Qwen/Qwen2.5-32B-Instruct")
        self.assertTrue(recent_contract_probe_summary()["latest"]["modal.llm_heavy.qwen2_5_32b"]["ok"])

    def test_modal_forbidden_small_model_fails_contract(self) -> None:
        path = self._artifact(
            result={"text": "bad", "model": "Qwen/Qwen2.5-0.5B-Instruct"},
            metrics={
                "model": "Qwen/Qwen2.5-0.5B-Instruct",
                "gpu_utilization_percent": 1,
                "cache_hit": True,
                "worker_image": "gpu-job-modal-llm",
            },
            verify={"ok": True},
            stdout="result.json: model Qwen/Qwen2.5-0.5B-Instruct\n",
        )

        record = parse_contract_probe_artifact(path, provider="modal", probe_name="modal.llm_heavy.qwen2_5_32b")

        self.assertFalse(record["ok"])
        self.assertFalse(record["checks"]["model_match"])
        self.assertFalse(record["checks"]["forbidden_model_absent"])
        self.assertEqual(record["failure"]["class"], "model_contract_mismatch")

    def test_modal_missing_gptqmodel_is_image_dependency_failure(self) -> None:
        path = self._artifact(
            result={"text": "", "error": "ImportError"},
            metrics={"gpu_utilization_percent": 1, "cache_hit": True, "worker_image": "gpu-job-modal-llm"},
            verify={"ok": False, "checks": {"text_nonempty": False}},
            stderr="ImportError: Loading an AWQ quantized model requires gptqmodel. Please install it with pip install gptqmodel\n",
        )

        record = parse_contract_probe_artifact(path, provider="modal", probe_name="modal.llm_heavy.qwen2_5_32b")

        self.assertFalse(record["ok"])
        self.assertEqual(record["failure"]["class"], "image_missing_dependency")
        self.assertFalse(record["failure"]["retryable"])

    def test_modal_hf_download_timeout_is_cold_start_timeout(self) -> None:
        path = self._artifact(
            result={"text": "", "error": "provider HTTP 500"},
            metrics={"cache_hit": False},
            verify={"ok": False},
            stdout="Fetching 17 files: 71% | HF snapshot_download read timed out\n",
        )

        record = parse_contract_probe_artifact(path, provider="modal", probe_name="modal.llm_heavy.qwen2_5_32b")

        self.assertFalse(record["ok"])
        self.assertTrue(record["observed"]["cache"]["cold_start_observed"])
        self.assertEqual(record["failure"]["class"], "cold_start_timeout")
        self.assertTrue(record["failure"]["retryable"])

    def test_completed_empty_output_is_not_success(self) -> None:
        path = self._artifact(
            result={"text": "", "model": "Qwen/Qwen2.5-32B-Instruct"},
            metrics={
                "model": "Qwen/Qwen2.5-32B-Instruct",
                "gpu_utilization_percent": 1,
                "cache_hit": True,
                "worker_image": "gpu-job-modal-llm",
            },
            verify={"ok": True},
            stdout="completed successfully\n",
        )

        record = parse_contract_probe_artifact(path, provider="modal", probe_name="modal.llm_heavy.qwen2_5_32b")

        self.assertFalse(record["ok"])
        self.assertFalse(record["checks"]["text_nonempty"])
        self.assertEqual(record["failure"]["class"], "empty_output_success")

    def test_runpod_no_worker_maps_to_backpressure(self) -> None:
        path = self._artifact(
            result={"text": "", "error": "IN_QUEUE no worker available"},
            metrics={},
            verify={"ok": False},
            stdout="status=IN_QUEUE no worker active\n",
        )

        record = parse_contract_probe_artifact(path, provider="runpod", probe_name="runpod.llm_heavy.endpoint_openai")

        self.assertFalse(record["ok"])
        self.assertEqual(record["failure"]["class"], "provider_backpressure")
        self.assertTrue(record["failure"]["retryable"])

    def test_image_contract_mismatch_is_detected(self) -> None:
        path = self._artifact(
            result={"text": "ok", "model": "Qwen/Qwen2.5-32B-Instruct"},
            metrics={
                "model": "Qwen/Qwen2.5-32B-Instruct",
                "gpu_utilization_percent": 1,
                "cache_hit": True,
                "worker_image": "wrong-image",
            },
            verify={"ok": True},
        )

        record = parse_contract_probe_artifact(path, provider="modal", probe_name="modal.llm_heavy.qwen2_5_32b")

        self.assertFalse(record["checks"]["image_match"])
        self.assertEqual(record["failure"]["class"], "image_contract_mismatch")

    def test_active_contract_probe_generates_own_canary_job(self) -> None:
        def fake_submit(job, provider_name: str, execute: bool):
            artifact = Path(os.environ["XDG_DATA_HOME"]) / "gpu-job-control" / "artifacts" / job.job_id
            artifact.mkdir(parents=True)
            (artifact / "result.json").write_text(
                json.dumps({"text": "GPU_JOB_CONTRACT_PROBE_OK", "model": "Qwen/Qwen2.5-32B-Instruct"}) + "\n"
            )
            (artifact / "metrics.json").write_text(
                json.dumps(
                    {
                        "model": "Qwen/Qwen2.5-32B-Instruct",
                        "worker_image": "gpu-job-modal-llm",
                        "gpu_utilization_percent": 1,
                        "cache_hit": True,
                    }
                )
                + "\n"
            )
            (artifact / "verify.json").write_text(json.dumps({"ok": True}) + "\n")
            (artifact / "stdout.log").write_text("")
            (artifact / "stderr.log").write_text("")
            self.assertEqual(provider_name, "modal")
            self.assertTrue(execute)
            self.assertEqual(job.model, "Qwen/Qwen2.5-32B-Instruct")
            self.assertEqual(job.metadata["contract_probe"]["probe_name"], "modal.llm_heavy.qwen2_5_32b")
            self.assertTrue(job.metadata["hardware_verification"]["require_gpu_utilization"])
            return {"ok": True, "job": job.to_dict()}

        with patch("gpu_job.runner.submit_job", side_effect=fake_submit):
            result = active_contract_probe("modal", "modal.llm_heavy.qwen2_5_32b")

        self.assertTrue(result["ok"])
        self.assertTrue(result["record"]["ok"])

    def test_asr_diarization_contract_probe_does_not_require_gpu_utilization_counter(self) -> None:
        def fake_submit(job, provider_name: str, execute: bool):
            artifact = Path(os.environ["XDG_DATA_HOME"]) / "gpu-job-control" / "artifacts" / job.job_id
            artifact.mkdir(parents=True)
            (artifact / "result.json").write_text(
                json.dumps(
                    {
                        "text": "GPU_JOB_ASR_DIARIZATION_CANARY_OK",
                        "model": "pyannote/speaker-diarization-3.1",
                        "probe_info": {"worker_image": "gpu-job-modal-asr", "gpu_name": "NVIDIA A10"},
                    }
                )
                + "\n"
            )
            (artifact / "metrics.json").write_text(
                json.dumps({"model": "pyannote/speaker-diarization-3.1", "worker_image": "gpu-job-modal-asr"}) + "\n"
            )
            (artifact / "verify.json").write_text(json.dumps({"ok": True}) + "\n")
            (artifact / "stdout.log").write_text("")
            (artifact / "stderr.log").write_text("")
            self.assertFalse(job.metadata["hardware_verification"]["require_gpu_utilization"])
            return {"ok": True, "job": job.to_dict()}

        with patch("gpu_job.runner.submit_job", side_effect=fake_submit):
            result = active_contract_probe("modal", "modal.asr_diarization.pyannote")

        self.assertTrue(result["ok"])
        self.assertTrue(result["record"]["ok"])

    def test_runpod_asr_diarization_contract_probe_requires_cache_evidence(self) -> None:
        path = self._artifact(
            result={
                "text": "GPU_JOB_ASR_DIARIZATION_WORKSPACE_CANARY_OK",
                "model": "pyannote/speaker-diarization-3.1",
                "speaker_segments": [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}],
            },
            metrics={
                "model": "pyannote/speaker-diarization-3.1",
                "worker_image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                "provider_image": ASR_PROVIDER_IMAGE,
                "provider_image_digest": ASR_PROVIDER_IMAGE_DIGEST,
                "cache_hit": True,
            },
            verify={"ok": True, "checks": {"text_nonempty": True}},
            probe_info={
                "worker_image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                "provider_image": ASR_PROVIDER_IMAGE,
                "provider_image_digest": ASR_PROVIDER_IMAGE_DIGEST,
                "cache_hit": True,
                "execution_mode": "pod_http",
                "workspace_contract_ok": True,
                "hf_token_present": True,
                "image_contract_marker_present": True,
                "runtime_checks": {
                    "faster_whisper_import": True,
                    "pyannote_import": True,
                    "matplotlib_import": True,
                },
                "cleanup": {"ok": True},
                "actual_cost_guard": {"ok": True},
            },
        )

        record = parse_contract_probe_artifact(path, provider="runpod", probe_name="runpod.asr_diarization.pyannote")

        self.assertTrue(record["ok"])
        self.assertTrue(record["checks"]["cache_contract_ok"])
        self.assertTrue(record["checks"]["workspace_contract_ok"])
        self.assertTrue(record["checks"]["image_match"])
        self.assertEqual(record["observed"]["cache"]["cache_hit"], True)
        self.assertTrue(record["observed"]["workspace_contract"]["ok"])
        coverage = record["observed"]["workspace_observation_coverage"]
        self.assertEqual(set(coverage["categories"]), set(WORKSPACE_OBSERVATION_CATEGORIES))
        self.assertTrue(coverage["categories"]["secret_availability"]["ok"])
        self.assertTrue(coverage["categories"]["workspace_cache"]["ok"])
        self.assertTrue(coverage["categories"]["cleanup_result"]["ok"])

    def test_runpod_asr_diarization_contract_probe_rejects_cold_workspace(self) -> None:
        path = self._artifact(
            result={"text": "GPU_JOB_ASR_DIARIZATION_WORKSPACE_CANARY_OK", "model": "pyannote/speaker-diarization-3.1"},
            metrics={
                "model": "pyannote/speaker-diarization-3.1",
                "worker_image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                "cache_hit": False,
            },
            verify={"ok": True, "checks": {"text_nonempty": True}},
            probe_info={"workspace_contract_ok": False},
            stdout="workspace canary completed without warm cache evidence\n",
        )

        record = parse_contract_probe_artifact(path, provider="runpod", probe_name="runpod.asr_diarization.pyannote")

        self.assertFalse(record["ok"])
        self.assertFalse(record["checks"]["cache_contract_ok"])
        self.assertEqual(record["failure"]["class"], "cache_contract_missing")

    def test_runpod_asr_diarization_contract_probe_rejects_missing_hf_token(self) -> None:
        path = self._artifact(
            result={"text": "GPU_JOB_ASR_DIARIZATION_WORKSPACE_CANARY_OK", "model": "pyannote/speaker-diarization-3.1"},
            metrics={
                "model": "pyannote/speaker-diarization-3.1",
                "worker_image": ASR_PROVIDER_IMAGE,
                "provider_image": ASR_PROVIDER_IMAGE,
                "provider_image_digest": ASR_PROVIDER_IMAGE_DIGEST,
                "cache_hit": True,
            },
            verify={"ok": True, "checks": {"text_nonempty": True}},
            probe_info={
                "worker_image": ASR_PROVIDER_IMAGE,
                "provider_image": ASR_PROVIDER_IMAGE,
                "provider_image_digest": ASR_PROVIDER_IMAGE_DIGEST,
                "workspace_contract_ok": False,
                "hf_token_present": False,
                "image_contract_marker_present": True,
                "runtime_checks": {
                    "faster_whisper_import": True,
                    "pyannote_import": True,
                    "matplotlib_import": True,
                },
                "cleanup": {"ok": True},
                "actual_cost_guard": {"ok": True},
                "cache_hit": True,
            },
        )

        record = parse_contract_probe_artifact(path, provider="runpod", probe_name="runpod.asr_diarization.pyannote")

        self.assertFalse(record["ok"])
        self.assertFalse(record["checks"]["workspace_contract_ok"])
        self.assertEqual(record["failure"]["class"], "workspace_contract_missing")
        self.assertFalse(record["observed"]["workspace_contract"]["hf_token_present"])

    def test_runpod_asr_diarization_contract_probe_rejects_cleanup_failure(self) -> None:
        path = self._artifact(
            result={"text": "GPU_JOB_ASR_DIARIZATION_WORKSPACE_CANARY_OK", "model": "pyannote/speaker-diarization-3.1"},
            metrics={
                "model": "pyannote/speaker-diarization-3.1",
                "worker_image": ASR_PROVIDER_IMAGE,
                "provider_image": ASR_PROVIDER_IMAGE,
                "provider_image_digest": ASR_PROVIDER_IMAGE_DIGEST,
                "cache_hit": True,
            },
            verify={"ok": True, "checks": {"text_nonempty": True}},
            probe_info={
                "worker_image": ASR_PROVIDER_IMAGE,
                "provider_image": ASR_PROVIDER_IMAGE,
                "provider_image_digest": ASR_PROVIDER_IMAGE_DIGEST,
                "workspace_contract_ok": False,
                "hf_token_present": True,
                "image_contract_marker_present": True,
                "runtime_checks": {
                    "faster_whisper_import": True,
                    "pyannote_import": True,
                    "matplotlib_import": True,
                },
                "cleanup": {"ok": False, "error": "terminate failed"},
                "actual_cost_guard": {"ok": True},
                "cache_hit": True,
            },
        )

        record = parse_contract_probe_artifact(path, provider="runpod", probe_name="runpod.asr_diarization.pyannote")

        self.assertFalse(record["ok"])
        self.assertFalse(record["checks"]["workspace_contract_ok"])
        self.assertEqual(record["failure"]["class"], "workspace_contract_missing")
        self.assertFalse(record["observed"]["workspace_contract"]["cleanup_ok"])
        self.assertIn("cleanup_result", record["observed"]["workspace_observation_coverage"]["failed_categories"])

    def test_vast_asr_diarization_contract_probe_accepts_worker_cache_evidence(self) -> None:
        path = self._artifact(
            result={
                "text": "GPU_JOB_ASR_DIARIZATION_WORKER_OK",
                "model": "large-v3",
                "loaded_model_id": "pyannote/speaker-diarization-3.1",
                "diarization_model": "pyannote/speaker-diarization-3.1",
                "speaker_segments": [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}],
            },
            metrics={
                "model": "pyannote/speaker-diarization-3.1",
                "worker_image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                "provider_image": ASR_PROVIDER_IMAGE,
                "provider_image_digest": ASR_PROVIDER_IMAGE_DIGEST,
                "cache_hit": True,
            },
            verify={"ok": True, "checks": {"text_nonempty": True}},
            probe_info={
                "worker_image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                "provider_image": ASR_PROVIDER_IMAGE,
                "provider_image_digest": ASR_PROVIDER_IMAGE_DIGEST,
                "loaded_model_id": "pyannote/speaker-diarization-3.1",
                "cache_hit": True,
                "execution_mode": "asr_worker",
                "workspace_contract_ok": True,
            },
        )

        record = parse_contract_probe_artifact(path, provider="vast", probe_name="vast.asr_diarization.pyannote")

        self.assertTrue(record["ok"])
        self.assertTrue(record["checks"]["cache_contract_ok"])
        self.assertTrue(record["checks"]["image_match"])

    def test_runpod_and_vast_asr_observations_normalize_to_same_categories(self) -> None:
        runpod_observed = {
            "model": "pyannote/speaker-diarization-3.1",
            "image": {"name": ASR_PROVIDER_IMAGE, "digest": ASR_PROVIDER_IMAGE_DIGEST},
            "artifact_contract": {"files": [{"name": "result.json"}], "file_count": 1},
            "hardware": {"gpu_name": "NVIDIA GeForce RTX 3090"},
            "gpu_utilization_evidence": {},
            "cache": {"cache_hit": True},
            "workspace_contract": {
                "ok": True,
                "pod_id": "pod-runpod",
                "hf_token_present": True,
                "image_contract_marker_present": True,
                "runtime_imports_ok": True,
                "cache_hit": True,
                "worker_startup_ok": True,
                "gpu_probe": {"exit_code": 0, "stdout": "NVIDIA GeForce RTX 3090"},
                "cleanup_ok": True,
                "cleanup": {"ok": True},
                "cost_guard_ok": True,
                "actual_cost_guard": {"ok": True},
            },
        }
        vast_observed = {
            "model": "pyannote/speaker-diarization-3.1",
            "image": {"name": ASR_PROVIDER_IMAGE, "digest": ASR_PROVIDER_IMAGE_DIGEST},
            "artifact_contract": {"files": [{"name": "result.json"}], "file_count": 1},
            "hardware": {"gpu_name": "NVIDIA RTX A6000"},
            "gpu_utilization_evidence": {},
            "cache": {"cache_hit": True},
            "workspace_contract": {
                "ok": True,
                "instance_id": "vast-123",
                "provider_job_id": "vast-123",
                "hf_token_present": True,
                "image_contract_marker_present": True,
                "runtime_imports_ok": True,
                "cache_hit": True,
                "worker_startup_ok": True,
                "gpu_probe": {"exit_code": 0, "stdout": "NVIDIA RTX A6000"},
                "cleanup_ok": True,
                "cleanup": {"ok": True},
                "cost_guard_ok": True,
                "actual_cost_guard": {"ok": True},
            },
        }

        runpod_coverage = workspace_observation_coverage("runpod", runpod_observed, {"ok": True})
        vast_coverage = workspace_observation_coverage("vast", vast_observed, {"ok": True})

        self.assertEqual(runpod_coverage["missing_categories"], [])
        self.assertEqual(vast_coverage["missing_categories"], [])
        self.assertEqual(set(runpod_coverage["observed_categories"]), set(vast_coverage["observed_categories"]))
        self.assertEqual(runpod_coverage["failed_categories"], [])
        self.assertEqual(vast_coverage["failed_categories"], [])

    def test_active_runpod_asr_diarization_probe_uses_prebuilt_workspace_image(self) -> None:
        def fake_submit(job, provider_name: str, execute: bool):
            self.assertEqual(provider_name, "runpod")
            self.assertTrue(execute)
            self.assertEqual(job.gpu_profile, "asr_diarization")
            self.assertEqual(
                job.metadata["runpod_pod_image"],
                ASR_PROVIDER_IMAGE,
            )
            artifact = Path(os.environ["XDG_DATA_HOME"]) / "gpu-job-control" / "artifacts" / job.job_id
            artifact.mkdir(parents=True)
            (artifact / "result.json").write_text(
                json.dumps(
                    {
                        "text": "GPU_JOB_ASR_DIARIZATION_WORKSPACE_CANARY_OK",
                        "model": "pyannote/speaker-diarization-3.1",
                        "speaker_segments": [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}],
                    }
                )
                + "\n"
            )
            (artifact / "metrics.json").write_text(
                json.dumps(
                    {
                        "model": "pyannote/speaker-diarization-3.1",
                        "worker_image": ASR_PROVIDER_IMAGE,
                        "provider_image": ASR_PROVIDER_IMAGE,
                        "provider_image_digest": ASR_PROVIDER_IMAGE_DIGEST,
                        "cache_hit": True,
                        "workspace_contract_ok": True,
                        "hf_token_present": True,
                        "image_contract_marker_present": True,
                        "cleanup": {"ok": True},
                        "actual_cost_guard": {"ok": True},
                    }
                )
                + "\n"
            )
            (artifact / "probe_info.json").write_text(
                json.dumps(
                    {
                        "worker_image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                        "provider_image": ASR_PROVIDER_IMAGE,
                        "provider_image_digest": ASR_PROVIDER_IMAGE_DIGEST,
                        "cache_hit": True,
                    }
                )
                + "\n"
            )
            (artifact / "verify.json").write_text(json.dumps({"ok": True}) + "\n")
            (artifact / "stdout.log").write_text("")
            (artifact / "stderr.log").write_text("")
            return {"ok": True, "job": job.to_dict()}

        with patch("gpu_job.runner.submit_job", side_effect=fake_submit):
            result = active_contract_probe("runpod", "runpod.asr_diarization.pyannote")

        self.assertTrue(result["ok"])
        self.assertTrue(result["record"]["ok"])

    def _artifact(
        self,
        *,
        result: dict,
        metrics: dict,
        verify: dict,
        probe_info: dict | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> Path:
        path = Path(self.tmp.name) / f"artifact-{len(list(Path(self.tmp.name).glob('artifact-*')))}"
        path.mkdir()
        (path / "result.json").write_text(json.dumps(result, ensure_ascii=False) + "\n")
        (path / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False) + "\n")
        if probe_info is not None:
            (path / "probe_info.json").write_text(json.dumps(probe_info, ensure_ascii=False) + "\n")
        (path / "verify.json").write_text(json.dumps(verify, ensure_ascii=False) + "\n")
        (path / "stdout.log").write_text(stdout)
        (path / "stderr.log").write_text(stderr)
        return path


if __name__ == "__main__":
    unittest.main()
