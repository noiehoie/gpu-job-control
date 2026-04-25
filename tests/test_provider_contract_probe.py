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
from gpu_job.provider_module_contracts import PROVIDER_MODULE_CANARY_OBSERVATION_CATEGORIES, PROVIDER_MODULE_CONTRACTS

ASR_PROVIDER_IMAGE = (
    "ghcr.io/noiehoie/gpu-job-control/asr-diarization-worker:"
    "large-v3-pyannote3.3.2-cuda12.4-cmd@"
    "sha256:b06fd86a4d43d8fec294675e3ac7d3135934c61d422df567a5976636fb240be8"
)
ASR_PROVIDER_IMAGE_DIGEST = "sha256:b06fd86a4d43d8fec294675e3ac7d3135934c61d422df567a5976636fb240be8"
RUNPOD_ASR_SERVERLESS_IMAGE = (
    "ghcr.io/noiehoie/gpu-job-control-runpod-asr@sha256:e73ac9bd5c99eb0d281b5527f35dd1062dcd5157b5057e2f0d05d95ee89ba920"
)
RUNPOD_ASR_SERVERLESS_IMAGE_DIGEST = "sha256:e73ac9bd5c99eb0d281b5527f35dd1062dcd5157b5057e2f0d05d95ee89ba920"


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
        self.assertIn("provider_module_probe_name", schema["required_top_level_fields"])
        self.assertIn("provider_module_contract", schema["required_top_level_fields"])
        self.assertIn("provider_module_canary_evidence", schema["required_top_level_fields"])
        self.assertIn("workspace_observation_categories", schema)
        self.assertEqual(WORKSPACE_OBSERVATION_CATEGORIES, PROVIDER_MODULE_CANARY_OBSERVATION_CATEGORIES)
        self.assertIn("startup_phases", schema["workspace_observation_categories"])
        self.assertIn("provider_residue", schema["workspace_observation_categories"])
        self.assertEqual(
            schema["workspace_observation_coverage"]["coverage_version"],
            "gpu-job-workspace-observation-coverage-v1",
        )
        self.assertIn("read-side observation", schema["workspace_observation_coverage"]["rule"])
        self.assertEqual(
            schema["provider_module_canary_evidence"]["provider_module_canary_evidence_version"],
            "gpu-job-provider-module-canary-evidence-v1",
        )
        self.assertEqual(
            schema["provider_module_canary_evidence"]["evidence_source"],
            "record.observed.workspace_observation_coverage",
        )
        self.assertIn("modal_function", schema["provider_module_canary_evidence"]["modules"])
        self.assertIn("admin-only", schema["canary_rule"])
        self.assertIn("modal.llm_heavy.qwen2_5_32b", listed["probes"])
        self.assertEqual(plan["execution_mode"], "planned")
        self.assertEqual(plan["spec"]["expected_model"], "Qwen/Qwen2.5-32B-Instruct")
        self.assertIn("provider_module_canary_evidence_schema", plan)
        self.assertIn("modal_function", plan["provider_module_canary_evidence_schema"]["modules"])
        modal_evidence_schema = plan["provider_module_canary_evidence_schema"]["modules"]["modal_function"]
        self.assertEqual(
            modal_evidence_schema["requirement_observation_mapping"]["volume_visibility"],
            ["artifact_contract"],
        )
        self.assertNotIn("workspace_cache", modal_evidence_schema["required_observation_categories"])

    def test_contract_probe_canary_jobs_use_dedicated_secret_scope(self) -> None:
        job = _canary_job(DEFAULT_CONTRACT_PROBES["runpod.asr_diarization.pyannote"])

        self.assertEqual(job.metadata["source_system"], "contract-probe")
        self.assertEqual(job.metadata["secret_refs"], ["hf_token"])

    def test_vast_asr_canary_uses_local_audio_fixture(self) -> None:
        job = _canary_job(DEFAULT_CONTRACT_PROBES["vast.asr_diarization.pyannote"])

        self.assertEqual(job.provider, "vast")
        self.assertEqual(job.job_type, "asr")
        self.assertTrue(job.input_uri.endswith("fixtures/audio/asr-ja.wav"))
        self.assertNotIn("text://", job.input_uri)
        self.assertEqual(job.metadata["min_vram_gb"], 24)
        self.assertEqual(job.metadata["min_compute_cap"], 800)

    def test_vast_instance_smoke_canary_uses_direct_smoke_path(self) -> None:
        job = _canary_job(DEFAULT_CONTRACT_PROBES["vast.instance_smoke.cuda"])

        self.assertEqual(job.provider, "vast")
        self.assertEqual(job.job_type, "smoke")
        self.assertEqual(job.gpu_profile, "smoke")
        self.assertTrue(job.metadata["allow_vast_direct_instance_smoke"])
        self.assertEqual(job.limits["max_cost_usd"], 0.25)
        self.assertEqual(job.limits["max_runtime_minutes"], 6)
        self.assertEqual(job.limits["max_startup_seconds"], 240)
        self.assertEqual(job.worker_image, "nvidia/cuda:12.4.1-base-ubuntu22.04")
        self.assertEqual(job.metadata["contract_probe"]["provider_module_id"], "vast_instance")

    def test_vast_pyworker_canary_does_not_use_direct_instance_fallback(self) -> None:
        plan = plan_contract_probe("vast", "vast.asr.serverless_template")
        job = _canary_job(DEFAULT_CONTRACT_PROBES["vast.asr.serverless_template"])

        self.assertEqual(plan["spec"]["expected_model"], "Qwen/Qwen2.5-0.5B-Instruct")
        self.assertEqual(plan["spec"]["expected_image"], "vastai/vllm")
        self.assertEqual(job.provider, "vast")
        self.assertEqual(job.job_type, "asr")
        self.assertEqual(job.gpu_profile, "asr_fast")
        self.assertEqual(job.metadata["vast_execution_mode"], "serverless_pyworker")
        self.assertTrue(job.metadata["vast_serverless_contract_required"])
        self.assertNotIn("allow_vast_direct_instance_smoke", job.metadata)
        self.assertEqual(job.metadata["contract_probe"]["provider_module_id"], "vast_pyworker_serverless")
        self.assertFalse(job.metadata["hardware_verification"]["require_gpu_utilization"])

    def test_runpod_serverless_asr_handler_probe_is_distinct(self) -> None:
        plan = plan_contract_probe("runpod", "runpod.asr_diarization.serverless_handler")
        job = _canary_job(DEFAULT_CONTRACT_PROBES["runpod.asr_diarization.serverless_handler"])

        self.assertEqual(plan["probe_name"], "runpod.asr_diarization.serverless_handler")
        self.assertEqual(
            plan["spec"]["image_contract_id"],
            "asr-diarization-runpod-serverless-large-v3-pyannote3.3.2-cuda12.4",
        )
        self.assertTrue(plan["spec"]["serverless_handler_contract_required"])
        self.assertEqual(job.limits["max_startup_seconds"], 900)

    def test_runpod_serverless_official_whisper_smoke_probe_is_distinct(self) -> None:
        plan = plan_contract_probe("runpod", "runpod.asr.official_whisper_smoke")

        self.assertEqual(plan["probe_name"], "runpod.asr.official_whisper_smoke")
        self.assertEqual(plan["spec"]["expected_image"], "runpod/ai-api-faster-whisper:0.4.1")
        self.assertFalse(plan["spec"]["workspace_contract_required"])
        self.assertTrue(plan["spec"]["official_template_smoke_required"])

    def test_runpod_serverless_asr_handler_artifact_passes_contract(self) -> None:
        path = self._artifact(
            result={
                "text": "GPU_JOB_ASR_DIARIZATION_WORKSPACE_CANARY_OK",
                "model": "pyannote/speaker-diarization-3.1",
                "worker_image": RUNPOD_ASR_SERVERLESS_IMAGE,
                "provider_image_digest": RUNPOD_ASR_SERVERLESS_IMAGE_DIGEST,
                "endpoint_id": "runpod-endpoint-123",
                "workspace_contract_ok": True,
                "hf_token_present": True,
                "image_contract_marker_present": True,
                "cache_hit": True,
                "worker_startup_ok": True,
                "actual_cost_guard": {"ok": True},
                "cleanup": {"ok": True},
            },
            metrics={"cache_hit": True},
            verify={"ok": True},
            probe_info={
                "provider_image": RUNPOD_ASR_SERVERLESS_IMAGE,
                "provider_image_digest": RUNPOD_ASR_SERVERLESS_IMAGE_DIGEST,
                "cache_hit": True,
                "gpu_probe": {"ok": True, "stdout": "NVIDIA"},
            },
        )

        record = parse_contract_probe_artifact(
            path,
            provider="runpod",
            probe_name="runpod.asr_diarization.serverless_handler",
        )

        self.assertTrue(record["ok"])
        self.assertEqual(record["probe_name"], "runpod.asr_diarization.serverless_handler")
        self.assertTrue(record["checks"]["workspace_contract_ok"])
        evidence = record["provider_module_canary_evidence"]
        self.assertEqual(evidence["provider_module_id"], "runpod_serverless")
        self.assertEqual(evidence["provider_module_probe_name"], "runpod_serverless.asr_diarization.serverless_handler")
        self.assertIn("endpoint_health", evidence["requirements"])
        self.assertEqual(set(evidence["observation_categories"]), set(WORKSPACE_OBSERVATION_CATEGORIES))

    def test_runpod_serverless_official_whisper_smoke_artifact_passes_contract(self) -> None:
        path = self._artifact(
            result={
                "text": "",
                "provider_job_id": "runpod-job-456",
                "worker_image": "runpod/ai-api-faster-whisper:0.4.1",
                "provider_image": "runpod/ai-api-faster-whisper:0.4.1",
                "endpoint_id": "runpod-endpoint-456",
                "workspace_contract_ok": False,
                "official_template_smoke_ok": True,
                "worker_startup_ok": False,
                "cleanup": {"ok": True},
                "actual_cost_guard": {"ok": True},
                "final_job_status": "COMPLETED",
            },
            metrics={
                "worker_image": "runpod/ai-api-faster-whisper:0.4.1",
                "provider_image": "runpod/ai-api-faster-whisper:0.4.1",
                "official_template_smoke_ok": True,
            },
            verify={"ok": True, "checks": {"official_template_smoke_ok": True}},
            probe_info={
                "provider_image": "runpod/ai-api-faster-whisper:0.4.1",
                "endpoint_id": "runpod-endpoint-456",
                "provider_job_id": "runpod-job-456",
                "official_template_smoke_ok": True,
            },
        )

        record = parse_contract_probe_artifact(
            path,
            provider="runpod",
            probe_name="runpod.asr.official_whisper_smoke",
        )

        self.assertTrue(record["ok"])
        self.assertTrue(record["checks"]["official_template_smoke_ok"])
        self.assertTrue(record["checks"]["workspace_contract_ok"])
        self.assertTrue(record["provider_module_canary_evidence"]["ok"])
        evidence = record["provider_module_canary_evidence"]
        self.assertEqual(evidence["provider_module_id"], "runpod_serverless")

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

    def test_active_vast_probe_records_preflight_secret_error(self) -> None:
        def fake_submit(job, provider_name: str, execute: bool):
            artifact = Path(os.environ["XDG_DATA_HOME"]) / "gpu-job-control" / "artifacts" / job.job_id
            artifact.mkdir(parents=True)
            (artifact / "stdout.log").write_text("")
            (artifact / "stderr.log").write_text("")
            job.status = "failed"
            job.error = "speaker diarization requires HF_TOKEN, HUGGINGFACE_TOKEN, or HUGGING_FACE_HUB_TOKEN before Vast GPU allocation"
            job.exit_code = 1
            self.assertEqual(provider_name, "vast")
            self.assertTrue(execute)
            return {"ok": False, "job": job.to_dict()}

        with patch("gpu_job.runner.submit_job", side_effect=fake_submit):
            result = active_contract_probe("vast", "vast.asr_diarization.pyannote")

        self.assertFalse(result["ok"])
        self.assertEqual(result["record"]["failure"]["class"], "secret_block")
        self.assertEqual(result["record"]["failure"]["retryable"], False)
        self.assertEqual(result["record"]["submit_result"]["provider_job_id"], "")
        self.assertTrue((Path(result["record"]["artifact_dir"]) / "submit_result.json").is_file())

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
            self.assertEqual(job.model, "pyannote/speaker-diarization-3.1")
            self.assertEqual(job.metadata["input"]["model"], "large-v3")
            self.assertEqual(job.metadata["input"]["speaker_model"], "pyannote/speaker-diarization-3.1")
            return {"ok": True, "job": job.to_dict()}

        with patch("gpu_job.runner.submit_job", side_effect=fake_submit):
            result = active_contract_probe("modal", "modal.asr_diarization.pyannote")

        self.assertTrue(result["ok"])
        self.assertTrue(result["record"]["ok"])

    def test_modal_asr_module_evidence_accepts_submit_job_id_and_gpu_hardware(self) -> None:
        path = self._artifact(
            result={
                "text": "GPU_JOB_ASR_DIARIZATION_CANARY_OK",
                "model": "pyannote/speaker-diarization-3.1",
                "worker_image": "gpu-job-modal-asr",
                "worker_startup_ok": True,
            },
            metrics={
                "model": "pyannote/speaker-diarization-3.1",
                "worker_image": "gpu-job-modal-asr",
                "gpu_name": "NVIDIA A10G",
            },
            verify={"ok": True, "checks": {"text_nonempty": True}},
            probe_info={
                "worker_image": "gpu-job-modal-asr",
                "workspace_contract_ok": True,
                "worker_startup_ok": True,
            },
            submit_result={
                "ok": True,
                "job_id": "contract-probe-asr-20260422-modal",
                "status": "succeeded",
                "post_submit_guard": {
                    "providers": {
                        "modal": {
                            "ok": True,
                            "estimated_hourly_usd": 0.0,
                            "billable_resources": [],
                            "reason": "no running Modal apps",
                        }
                    }
                },
            },
        )

        record = parse_contract_probe_artifact(path, provider="modal", probe_name="modal.asr_diarization.pyannote")
        evidence = record["provider_module_canary_evidence"]

        self.assertTrue(record["ok"], record["failure"])
        self.assertEqual(record["observed"]["workspace_contract"]["provider_job_id"], "contract-probe-asr-20260422-modal")
        self.assertTrue(record["observed"]["workspace_observation_coverage"]["categories"]["gpu_execution"]["ok"])
        self.assertTrue(evidence["ok"], evidence)
        self.assertEqual(evidence["missing_categories"], [])
        self.assertEqual(evidence["failed_categories"], [])

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
        evidence = record["provider_module_canary_evidence"]
        self.assertEqual(evidence["provider_module_id"], "runpod_pod")
        self.assertIn("terminate", evidence["requirements"])
        self.assertTrue(evidence["requirements"]["terminate"]["ok"])
        self.assertEqual(set(evidence["observation_categories"]), set(WORKSPACE_OBSERVATION_CATEGORIES))

    def test_runpod_serverless_gpu_execution_accepts_required_nvidia_smi_runtime_check(self) -> None:
        path = self._artifact(
            result={
                "text": "GPU_JOB_ASR_SERVERLESS_CANARY_OK",
                "model": "pyannote/speaker-diarization-3.1",
                "worker_image": RUNPOD_ASR_SERVERLESS_IMAGE,
                "provider_image_digest": RUNPOD_ASR_SERVERLESS_IMAGE_DIGEST,
                "endpoint_id": "runpod-endpoint-123",
                "provider_job_id": "rp-job-123",
                "workspace_contract_ok": True,
                "hf_token_present": True,
                "image_contract_marker_present": True,
                "cache_hit": True,
                "worker_startup_ok": True,
                "require_gpu": True,
                "runtime_checks": {
                    "faster_whisper_import": True,
                    "pyannote_import": True,
                    "matplotlib_import": True,
                    "image_contract_marker_present": True,
                    "nvidia_smi_present": True,
                },
                "cleanup": {"ok": True},
                "actual_cost_guard": {"ok": True},
            },
            metrics={
                "provider_image": RUNPOD_ASR_SERVERLESS_IMAGE,
                "provider_image_digest": RUNPOD_ASR_SERVERLESS_IMAGE_DIGEST,
            },
            verify={"ok": True, "checks": {"text_nonempty": True}},
        )

        record = parse_contract_probe_artifact(path, provider="runpod", probe_name="runpod.asr_diarization.serverless_handler")

        self.assertTrue(record["observed"]["workspace_observation_coverage"]["categories"]["gpu_execution"]["ok"])
        self.assertNotIn("gpu_execution", record["provider_module_canary_evidence"]["failed_categories"])

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

    def test_module_canary_evidence_fixture_artifacts_cover_every_provider_module(self) -> None:
        cases = [
            ("modal_function", "modal", "modal.llm_heavy.qwen2_5_32b"),
            ("runpod_serverless", "runpod", "runpod.asr_diarization.serverless_handler"),
            ("runpod_pod", "runpod", "runpod.asr_diarization.pyannote"),
            ("vast_instance", "vast", "vast.asr_diarization.pyannote"),
            ("vast_pyworker_serverless", "vast", "vast.asr.serverless_template"),
        ]

        for module_id, provider, probe_name in cases:
            with self.subTest(module_id=module_id):
                record = parse_contract_probe_artifact(
                    self._module_parity_artifact(module_id),
                    provider=provider,
                    probe_name=probe_name,
                )
                evidence = record["provider_module_canary_evidence"]

                self.assertTrue(record["ok"], record["failure"])
                self.assertEqual(evidence["provider_module_id"], module_id)
                self.assertEqual(evidence["module_canary_requirements"], PROVIDER_MODULE_CONTRACTS[module_id]["canary_requirements"])
                self.assertEqual(set(evidence["observation_categories"]), set(WORKSPACE_OBSERVATION_CATEGORIES))
                self.assertEqual(evidence["missing_categories"], [])
                self.assertEqual(evidence["failed_categories"], [])
                self.assertTrue(evidence["ok"])
                self.assertTrue(all(item["ok"] for item in evidence["requirements"].values()))

    def test_vast_smoke_module_evidence_uses_submit_guards_and_smoke_marker(self) -> None:
        path = self._artifact(
            result={
                "text": "GPU_JOB_SMOKE_DONE",
                "instance_id": "35397598",
                "provider_job_id": "35397598",
            },
            metrics={
                "worker_image": "nvidia/cuda:12.4.1-base-ubuntu22.04",
                "gpu_name": "NVIDIA GeForce RTX 5060 Ti, 16311 MiB, 570.195.03",
                "gpu_memory_used_mb": 1,
            },
            verify={"ok": True, "checks": {"text_nonempty": True}},
            probe_info={
                "provider_image": "nvidia/cuda:12.4.1-base-ubuntu22.04",
                "instance_id": "35397598",
            },
            stdout="GPU_JOB_SMOKE_START\nNVIDIA GeForce RTX 5060 Ti, 16311 MiB, 570.195.03\nGPU_JOB_SMOKE_DONE\n",
            submit_result={
                "ok": True,
                "provider": "vast",
                "provider_job_id": "35397598",
                "status": "succeeded",
                "pre_submit_guard": {
                    "providers": {
                        "vast": {
                            "ok": True,
                            "estimated_hourly_usd": 0.0,
                            "billable_resources": [],
                            "reason": "no Vast.ai billable resources",
                        }
                    }
                },
                "post_submit_guard": {
                    "providers": {
                        "vast": {
                            "ok": True,
                            "estimated_hourly_usd": 0.0,
                            "billable_resources": [],
                            "reason": "no Vast.ai billable resources",
                        }
                    }
                },
            },
        )

        record = parse_contract_probe_artifact(path, provider="vast", probe_name="vast.instance_smoke.cuda")
        evidence = record["provider_module_canary_evidence"]

        self.assertTrue(record["ok"], record["failure"])
        self.assertTrue(evidence["ok"], evidence["missing_categories"])
        self.assertEqual(evidence["missing_categories"], [])
        self.assertTrue(evidence["observation_categories"]["startup_phases"]["ok"])
        self.assertTrue(evidence["observation_categories"]["model_load"]["ok"])
        self.assertTrue(evidence["observation_categories"]["cost_guard"]["ok"])
        self.assertTrue(evidence["observation_categories"]["cleanup_result"]["ok"])
        self.assertTrue(evidence["observation_categories"]["provider_residue"]["ok"])

    def test_vast_pyworker_serverless_rejects_direct_instance_smoke_identity(self) -> None:
        path = self._artifact(
            result={
                "text": "GPU_JOB_ASR_SERVERLESS_CANARY_OK",
                "model": "whisper-large-v3",
                "instance_id": "35406597",
                "provider_job_id": "35406597",
                "workspace_contract_ok": True,
                "worker_startup_ok": True,
                "runtime_imports_ok": True,
                "cleanup": {"ok": True},
                "actual_cost_guard": {"ok": True},
            },
            metrics={
                "model": "whisper-large-v3",
                "worker_image": "gpu-job-asr-worker",
                "provider_image": "gpu-job-asr-worker",
                "gpu_utilization_percent": 12,
                "gpu_memory_used_mb": 12000,
            },
            verify={"ok": True, "checks": {"text_nonempty": True}},
            probe_info={
                "provider_image": "gpu-job-asr-worker",
                "instance_id": "35406597",
                "provider_job_id": "35406597",
                "workspace_contract_ok": True,
                "worker_startup_ok": True,
                "runtime_checks": {"faster_whisper_import": True},
                "gpu_probe": {"ok": True, "stdout": "NVIDIA GeForce RTX 5060 Ti"},
                "cleanup": {"ok": True},
                "actual_cost_guard": {"ok": True},
            },
        )

        record = parse_contract_probe_artifact(path, provider="vast", probe_name="vast.asr.serverless_template")
        evidence = record["provider_module_canary_evidence"]

        self.assertFalse(evidence["ok"])
        self.assertIn("provider_resource_identity", evidence["failed_categories"])
        self.assertIn("endpoint_id and workergroup_id", evidence["module_specific_failures"][0]["reason"])

    def test_vast_pyworker_serverless_accepts_endpoint_workers_as_gpu_evidence(self) -> None:
        path = self._artifact(
            result={
                "text": "GPU_JOB_ASR_SERVERLESS_CANARY_OK",
                "model": "Qwen/Qwen2.5-0.5B-Instruct",
                "endpoint_id": "21119",
                "workergroup_id": "27643",
                "provider_job_id": "bbab1656-1c75-429e-98ee-ab5fad47e417",
                "workspace_contract_ok": True,
                "worker_startup_ok": True,
                "cleanup": {"ok": True},
                "actual_cost_guard": {"ok": True},
                "worker_request": {
                    "endpoint_workers": [
                        {
                            "id": 35508355,
                            "status": "idle",
                            "perf": 2651.69,
                            "reqs_working": 1,
                        }
                    ]
                },
            },
            metrics={
                "model": "Qwen/Qwen2.5-0.5B-Instruct",
                "provider_image": "vastai/vllm:v0.11.0-cuda-12.8-mvc-cuda-12.0",
                "expected_image_ref": "vastai/vllm:v0.11.0-cuda-12.8-mvc-cuda-12.0",
            },
            verify={"ok": True, "checks": {"text_nonempty": True}},
            probe_info={
                "provider_image": "vastai/vllm:v0.11.0-cuda-12.8-mvc-cuda-12.0",
                "endpoint_id": "21119",
                "workergroup_id": "27643",
                "provider_job_id": "bbab1656-1c75-429e-98ee-ab5fad47e417",
                "workspace_contract_ok": True,
                "worker_startup_ok": True,
                "cleanup": {"ok": True},
                "actual_cost_guard": {"ok": True},
                "worker_request": {
                    "endpoint_workers": [
                        {
                            "id": 35508355,
                            "status": "idle",
                            "perf": 2651.69,
                            "reqs_working": 1,
                        }
                    ]
                },
            },
        )

        record = parse_contract_probe_artifact(path, provider="vast", probe_name="vast.asr.serverless_template")
        evidence = record["provider_module_canary_evidence"]

        self.assertTrue(record["ok"], record["failure"])
        self.assertTrue(evidence["ok"])
        self.assertEqual(evidence["failed_categories"], [])
        self.assertTrue(evidence["observation_categories"]["gpu_execution"]["ok"])

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
        submit_result: dict | None = None,
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
        if submit_result is not None:
            (path / "submit_result.json").write_text(json.dumps(submit_result, ensure_ascii=False) + "\n")
        return path

    def _module_parity_artifact(self, module_id: str) -> Path:
        common_probe_info = {
            "cache_hit": True,
            "workspace_contract_ok": True,
            "hf_token_present": True,
            "image_contract_marker_present": True,
            "worker_startup_ok": True,
            "runtime_checks": {
                "faster_whisper_import": True,
                "pyannote_import": True,
                "matplotlib_import": True,
            },
            "gpu_probe": {"ok": True, "stdout": "NVIDIA RTX A6000 CUDA"},
            "cleanup": {"ok": True},
            "actual_cost_guard": {"ok": True},
        }
        if module_id == "modal_function":
            return self._artifact(
                result={
                    "text": "GPU_JOB_CONTRACT_PROBE_OK",
                    "model": "Qwen/Qwen2.5-32B-Instruct",
                    "provider_job_id": "modal-call-123",
                    "workspace_contract_ok": True,
                    "cache_hit": True,
                    "worker_startup_ok": True,
                    "cleanup": {"ok": True},
                    "actual_cost_guard": {"ok": True},
                },
                metrics={
                    "model": "Qwen/Qwen2.5-32B-Instruct",
                    "worker_image": "gpu-job-modal-llm",
                    "gpu_utilization_percent": 15,
                    "gpu_memory_used_mb": 42000,
                    "cache_hit": True,
                },
                verify={"ok": True, "checks": {"text_nonempty": True}},
                probe_info={**common_probe_info, "worker_image": "gpu-job-modal-llm", "provider_job_id": "modal-call-123"},
            )
        if module_id == "runpod_serverless":
            return self._artifact(
                result={
                    "text": "GPU_JOB_ASR_DIARIZATION_WORKSPACE_CANARY_OK",
                    "model": "pyannote/speaker-diarization-3.1",
                    "provider_job_id": "runpod-job-123",
                    "worker_image": RUNPOD_ASR_SERVERLESS_IMAGE,
                    "provider_image_digest": RUNPOD_ASR_SERVERLESS_IMAGE_DIGEST,
                    "workspace_contract_ok": True,
                    "cache_hit": True,
                    "worker_startup_ok": True,
                    "cleanup": {"ok": True},
                    "actual_cost_guard": {"ok": True},
                },
                metrics={"cache_hit": True},
                verify={"ok": True, "checks": {"text_nonempty": True}},
                probe_info={
                    **common_probe_info,
                    "provider_image": RUNPOD_ASR_SERVERLESS_IMAGE,
                    "provider_image_digest": RUNPOD_ASR_SERVERLESS_IMAGE_DIGEST,
                    "endpoint_id": "runpod-endpoint-123",
                    "provider_job_id": "runpod-job-123",
                },
            )
        if module_id in {"runpod_pod", "vast_instance"}:
            resource_key = "pod_id" if module_id == "runpod_pod" else "instance_id"
            resource_id = "pod-runpod-123" if module_id == "runpod_pod" else "vast-instance-123"
            return self._artifact(
                result={
                    "text": "GPU_JOB_ASR_DIARIZATION_WORKSPACE_CANARY_OK",
                    "model": "pyannote/speaker-diarization-3.1",
                    "speaker_segments": [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}],
                    resource_key: resource_id,
                },
                metrics={
                    "model": "pyannote/speaker-diarization-3.1",
                    "worker_image": ASR_PROVIDER_IMAGE,
                    "provider_image": ASR_PROVIDER_IMAGE,
                    "provider_image_digest": ASR_PROVIDER_IMAGE_DIGEST,
                    "cache_hit": True,
                },
                verify={"ok": True, "checks": {"text_nonempty": True}},
                probe_info={
                    **common_probe_info,
                    "worker_image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                    "provider_image": ASR_PROVIDER_IMAGE,
                    "provider_image_digest": ASR_PROVIDER_IMAGE_DIGEST,
                    "loaded_model_id": "pyannote/speaker-diarization-3.1",
                    resource_key: resource_id,
                    "provider_job_id": resource_id,
                },
            )
        if module_id == "vast_pyworker_serverless":
            return self._artifact(
                result={
                    "text": "GPU_JOB_ASR_SERVERLESS_CANARY_OK",
                    "model": "Qwen/Qwen2.5-0.5B-Instruct",
                    "endpoint_id": "vast-endpoint-123",
                    "workergroup_id": "vast-workergroup-123",
                    "provider_job_id": "vast-pyworker-request-123",
                    "workspace_contract_ok": True,
                    "cache_hit": True,
                    "worker_startup_ok": True,
                    "cleanup": {"ok": True},
                    "actual_cost_guard": {"ok": True},
                },
                metrics={
                    "model": "Qwen/Qwen2.5-0.5B-Instruct",
                    "worker_image": "vastai/vllm:v0.11.0-cuda-12.8-mvc-cuda-12.0",
                    "provider_image": "vastai/vllm:v0.11.0-cuda-12.8-mvc-cuda-12.0",
                    "cache_hit": True,
                },
                verify={"ok": True, "checks": {"text_nonempty": True}},
                probe_info={
                    **common_probe_info,
                    "provider_image": "vastai/vllm:v0.11.0-cuda-12.8-mvc-cuda-12.0",
                    "endpoint_id": "vast-endpoint-123",
                    "workergroup_id": "vast-workergroup-123",
                    "provider_job_id": "vast-pyworker-request-123",
                },
            )
        raise AssertionError(f"unknown provider module fixture: {module_id}")

    def test_default_contract_probes_keep_required_shape_for_plan_and_canary_job(self) -> None:
        required_keys = {
            "provider",
            "provider_module_id",
            "workload_family",
            "job_type",
            "gpu_profile",
            "expected_model",
            "expected_image",
            "expected_image_digest",
            "forbidden_models",
            "required_files",
            "require_gpu_utilization",
            "cache_required",
        }
        conditional_keys = {
            "workspace_contract_required": bool,
            "image_contract_id": str,
            "serverless_handler_contract_required": bool,
            "official_template_smoke_required": bool,
        }

        for probe_name, spec in DEFAULT_CONTRACT_PROBES.items():
            # 2. required_keys.issubset(spec.keys()) を確認
            missing = required_keys - set(spec.keys())
            self.assertEqual(missing, set(), f"Probe {probe_name} is missing required keys: {missing}")

            # 3. 条件付きキーが存在する場合だけ型を固定する
            for key, expected_type in conditional_keys.items():
                if key in spec:
                    self.assertIsInstance(
                        spec[key], expected_type, f"Probe {probe_name} key {key} must be {expected_type}"
                    )

            # 4. planned = plan_contract_probe(spec["provider"], probe_name) を実行
            planned = plan_contract_probe(spec["provider"], probe_name)

            # 5. job = _canary_job(spec) を実行
            job = _canary_job(spec)

            # 6. planned["probe_name"] == probe_name
            self.assertEqual(planned["probe_name"], probe_name)

            # 7. planned["provider"] == spec["provider"]
            self.assertEqual(planned["provider"], spec["provider"])

            # 8. planned["spec"]["provider_module_id"] == spec["provider_module_id"]
            self.assertEqual(planned["spec"]["provider_module_id"], spec["provider_module_id"])

            # 9. job.provider == spec["provider"]
            self.assertEqual(job.provider, spec["provider"])

            # 10. job.gpu_profile == spec["gpu_profile"]
            self.assertEqual(job.gpu_profile, spec["gpu_profile"])


if __name__ == "__main__":
    unittest.main()
