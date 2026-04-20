from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import unittest

from gpu_job.models import Job
from gpu_job.providers.runpod import RunPodProvider
from gpu_job.providers.runpod import _actual_pod_cost_guard
from gpu_job.providers.runpod import _pod_http_worker_docker_args
from gpu_job.providers.runpod import _pod_has_runtime
from gpu_job.providers.runpod import _openai_chat_payload
from gpu_job.providers.runpod import _runpod_asr_diarization_pod_defaults
from gpu_job.providers.runpod import _runpod_api_key
from gpu_job.store import JobStore


class RunPodConfigTest(unittest.TestCase):
    def test_env_key_wins(self) -> None:
        old_key = os.environ.get("RUNPOD_API_KEY")
        try:
            os.environ["RUNPOD_API_KEY"] = "env-key"
            self.assertEqual(_runpod_api_key(), "env-key")
        finally:
            if old_key is None:
                os.environ.pop("RUNPOD_API_KEY", None)
            else:
                os.environ["RUNPOD_API_KEY"] = old_key

    def test_config_key_is_used_without_env(self) -> None:
        old_key = os.environ.pop("RUNPOD_API_KEY", None)
        old_home = os.environ.get("HOME")
        with TemporaryDirectory() as tmp:
            try:
                os.environ["HOME"] = tmp
                config = Path(tmp) / ".runpod" / "config.toml"
                config.parent.mkdir(parents=True)
                config.write_text('[default]\napi_key = "config-key"\n', encoding="utf-8")
                self.assertEqual(_runpod_api_key(), "config-key")
            finally:
                if old_key is not None:
                    os.environ["RUNPOD_API_KEY"] = old_key
                if old_home is not None:
                    os.environ["HOME"] = old_home

    def test_openai_payload_uses_model_override(self) -> None:
        old_override = os.environ.get("RUNPOD_LLM_MODEL_OVERRIDE")
        try:
            os.environ["RUNPOD_LLM_MODEL_OVERRIDE"] = "Qwen/Qwen3-32B-AWQ"
            job = Job(
                job_id="llm_heavy-test",
                job_type="llm_heavy",
                input_uri="text://hello",
                output_uri="local://out",
                worker_image="runpod:public-openai",
                gpu_profile="llm_heavy",
                model="placeholder",
                limits={"max_runtime_minutes": 10},
                metadata={"input": {"system_prompt": "be brief", "prompt": "Say OK", "max_tokens": 4}},
            )
            payload = _openai_chat_payload(job)
            self.assertEqual(payload["model"], "Qwen/Qwen3-32B-AWQ")
            self.assertEqual(payload["temperature"], 0)
            self.assertEqual(payload["messages"][0], {"role": "system", "content": "be brief"})
            self.assertEqual(payload["messages"][1], {"role": "user", "content": "Say OK"})
        finally:
            if old_override is None:
                os.environ.pop("RUNPOD_LLM_MODEL_OVERRIDE", None)
            else:
                os.environ["RUNPOD_LLM_MODEL_OVERRIDE"] = old_override

    def test_vllm_plan_is_scale_to_zero_and_uses_secret_reference(self) -> None:
        provider = RunPodProvider()
        plan = provider.plan_vllm_endpoint(
            model="Qwen/Qwen2.5-0.5B-Instruct",
            image="runpod/worker-v1-vllm:v2.14.0",
            gpu_ids="ADA_24",
            network_volume_id="",
            locations="",
            hf_secret_name="gpu_job_hf_read",
            max_model_len=2048,
            gpu_memory_utilization=0.9,
            max_concurrency=1,
            idle_timeout=90,
            workers_max=1,
            scaler_value=15,
            quantization="",
            served_model_name="",
            flashboot=True,
        )
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["safety_invariants"]["workers_min"], 0)
        self.assertEqual(plan["safety_invariants"]["workers_standby"], 0)
        self.assertEqual(plan["endpoint"]["workersMin"], 0)
        self.assertEqual(plan["endpoint"]["workersMax"], 1)
        self.assertEqual(plan["endpoint"]["gpuCount"], 1)
        self.assertEqual(plan["endpoint"]["gpuIds"], "ADA_24")
        self.assertEqual(plan["gpu_selection"]["pool_ids"], ["ADA_24"])
        self.assertEqual(plan["endpoint"]["locations"], "")
        self.assertEqual(plan["endpoint"]["idleTimeout"], 90)
        self.assertEqual(plan["endpoint"]["scalerValue"], 15)
        self.assertEqual(plan["endpoint"]["flashBootType"], "FLASHBOOT")
        self.assertEqual(plan["template"]["ports"], "8000/http")
        env = {item["key"]: item["value"] for item in plan["template"]["env"]}
        self.assertEqual(env["MODEL_NAME"], "Qwen/Qwen2.5-0.5B-Instruct")
        self.assertEqual(env["HF_TOKEN"], "{{ RUNPOD_SECRET_gpu_job_hf_read }}")
        self.assertNotIn("hf_D", json.dumps(plan))

    def test_vllm_plan_rejects_workers_max_zero_for_creation(self) -> None:
        provider = RunPodProvider()
        plan = provider.plan_vllm_endpoint(
            model="Qwen/Qwen2.5-0.5B-Instruct",
            image="runpod/worker-v1-vllm:v2.14.0",
            gpu_ids="ADA_24",
            network_volume_id="",
            locations="",
            hf_secret_name="gpu_job_hf_read",
            max_model_len=2048,
            gpu_memory_utilization=0.9,
            max_concurrency=1,
            idle_timeout=90,
            workers_max=0,
            scaler_value=15,
            quantization="",
            served_model_name="",
            flashboot=False,
        )
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["error"], "invalid_workers_max")

    def test_vllm_plan_rejects_concrete_gpu_type_as_gpu_ids(self) -> None:
        provider = RunPodProvider()
        plan = provider.plan_vllm_endpoint(
            model="Qwen/Qwen2.5-0.5B-Instruct",
            image="runpod/worker-v1-vllm:v2.14.0",
            gpu_ids="NVIDIA L4",
            network_volume_id="",
            locations="",
            hf_secret_name="gpu_job_hf_read",
            max_model_len=2048,
            gpu_memory_utilization=0.9,
            max_concurrency=1,
            idle_timeout=90,
            workers_max=1,
            scaler_value=15,
            quantization="",
            served_model_name="",
            flashboot=False,
        )
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["error"], "invalid_runpod_gpu_ids")
        self.assertEqual(plan["gpu_selection"]["invalid_pool_ids"], ["NVIDIA L4"])

    def test_vllm_plan_allows_pool_with_concrete_gpu_exclusion(self) -> None:
        provider = RunPodProvider()
        plan = provider.plan_vllm_endpoint(
            model="Qwen/Qwen2.5-0.5B-Instruct",
            image="runpod/worker-v1-vllm:v2.14.0",
            gpu_ids="ADA_24,-NVIDIA L4",
            network_volume_id="",
            locations="",
            hf_secret_name="gpu_job_hf_read",
            max_model_len=2048,
            gpu_memory_utilization=0.9,
            max_concurrency=1,
            idle_timeout=90,
            workers_max=1,
            scaler_value=15,
            quantization="",
            served_model_name="",
            flashboot=False,
        )
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["gpu_selection"]["pool_ids"], ["ADA_24"])
        self.assertEqual(plan["gpu_selection"]["excluded_gpu_types"], ["NVIDIA L4"])

    def test_vllm_endpoint_invariant_rejects_warm_capacity(self) -> None:
        provider = RunPodProvider()
        result = provider._endpoint_scale_to_zero_invariant({"workersMin": 1, "workersStandby": 0, "workersMax": 1})
        self.assertFalse(result["ok"])

    def test_vllm_endpoint_invariant_rejects_observed_standby_without_workers_min(self) -> None:
        provider = RunPodProvider()
        result = provider._endpoint_scale_to_zero_invariant({"workersMin": 0, "workersStandby": 1, "workersMax": 1})
        self.assertFalse(result["ok"])

    def test_vllm_endpoint_invariant_rejects_unbounded_default_workers_max(self) -> None:
        provider = RunPodProvider()
        result = provider._endpoint_scale_to_zero_invariant({"workersMin": 0, "workersStandby": 0, "workersMax": 3})
        self.assertFalse(result["ok"])

    def test_pod_plan_blocks_cost_over_budget(self) -> None:
        class FakeRunPodProvider(RunPodProvider):
            def _gpu_type_info(self, gpu_type_id: str, *, gpu_count: int) -> dict:
                return {
                    "ok": True,
                    "id": gpu_type_id,
                    "lowestPrice": {"uninterruptablePrice": 1.0, "stockStatus": "High"},
                }

        provider = FakeRunPodProvider()
        plan = provider.plan_pod_worker(
            gpu_type_id="NVIDIA GeForce RTX 3090",
            image="runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
            cloud_type="ALL",
            gpu_count=1,
            volume_in_gb=0,
            container_disk_in_gb=20,
            min_vcpu_count=2,
            min_memory_in_gb=8,
            max_uptime_seconds=3600,
            max_estimated_cost_usd=0.02,
            docker_args="bash -lc 'nvidia-smi; sleep 300'",
        )
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["cost_guard"]["estimated_cost_usd"], 1.0)

    def test_pod_plan_accepts_small_bounded_cost(self) -> None:
        class FakeRunPodProvider(RunPodProvider):
            def _gpu_type_info(self, gpu_type_id: str, *, gpu_count: int) -> dict:
                return {
                    "ok": True,
                    "id": gpu_type_id,
                    "lowestPrice": {"uninterruptablePrice": 0.22, "stockStatus": "High"},
                }

        provider = FakeRunPodProvider()
        plan = provider.plan_pod_worker(
            gpu_type_id="NVIDIA GeForce RTX 3090",
            image="runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
            cloud_type="ALL",
            gpu_count=1,
            volume_in_gb=0,
            container_disk_in_gb=20,
            min_vcpu_count=2,
            min_memory_in_gb=8,
            max_uptime_seconds=90,
            max_estimated_cost_usd=0.02,
            docker_args="bash -lc 'nvidia-smi; sleep 300'",
        )
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["cost_guard"]["estimated_cost_usd"], 0.0055)

    def test_pod_runtime_detection(self) -> None:
        self.assertTrue(_pod_has_runtime({"runtime": {"uptimeInSeconds": 0}}))
        self.assertTrue(_pod_has_runtime({"desiredStatus": "RUNNING"}))
        self.assertTrue(_pod_has_runtime({"uptimeSeconds": 0}))
        self.assertFalse(_pod_has_runtime({"runtime": None}))

    def test_pod_http_worker_docker_args_contains_health_server(self) -> None:
        args = _pod_http_worker_docker_args()
        self.assertIn("bash -lc", args)
        self.assertIn("base64", args)
        self.assertNotIn("'", args)

    def test_pod_http_worker_docker_args_contains_asr_diarization_probe(self) -> None:
        args = _pod_http_worker_docker_args(worker_mode="asr_diarization")

        self.assertIn("GPU_JOB_WORKER_MODE=asr_diarization", args)
        self.assertIn("base64", args)
        self.assertNotIn("'", args)

    def test_runpod_asr_diarization_defaults_use_prebuilt_worker_image(self) -> None:
        defaults = _runpod_asr_diarization_pod_defaults()

        self.assertTrue(defaults["image"].startswith("ghcr.io/noiehoie/gpu-job-control/asr-diarization-worker:"))
        self.assertIn("@sha256:", defaults["image"])
        self.assertEqual(defaults["container_registry_auth_id"], "cmo69c98l00iyjj07z22tsrzm")
        self.assertEqual(defaults["container_disk_in_gb"], 80)
        self.assertLessEqual(defaults["max_estimated_cost_usd"], 0.15)

    def test_runpod_asr_serverless_plan_uses_prebuilt_image_and_scale_to_zero(self) -> None:
        provider = RunPodProvider()

        plan = provider.plan_asr_endpoint(
            gpu_ids="ADA_24",
            network_volume_id="vol-runpod-asr",
            locations="US",
            hf_secret_name="gpu_job_hf_read",
            idle_timeout=90,
            workers_max=1,
            scaler_value=4,
            flashboot=True,
        )

        self.assertTrue(plan["ok"])
        self.assertEqual(plan["plan_version"], "runpod-asr-endpoint-plan-v1")
        self.assertEqual(plan["safety_invariants"]["workers_min"], 0)
        self.assertEqual(plan["safety_invariants"]["workers_standby"], 0)
        self.assertEqual(plan["safety_invariants"]["workers_max"], 1)
        self.assertEqual(plan["safety_invariants"]["production_dispatch"], "blocked_until_contract_probe_passes")
        self.assertEqual(plan["endpoint"]["workersMin"], 0)
        self.assertEqual(plan["endpoint"]["workersMax"], 1)
        self.assertEqual(plan["endpoint"]["networkVolumeId"], "vol-runpod-asr")
        self.assertEqual(plan["endpoint"]["flashBootType"], "FLASHBOOT")
        self.assertEqual(plan["template"]["ports"], "8000/http")
        self.assertTrue(plan["template"]["imageName"].startswith("ghcr.io/noiehoie/gpu-job-control/asr-diarization-worker:"))
        env = {item["key"]: item["value"] for item in plan["template"]["env"]}
        self.assertEqual(env["GPU_JOB_WORKER_MODE"], "asr_diarization")
        self.assertEqual(env["HF_TOKEN"], "{{ RUNPOD_SECRET_gpu_job_hf_read }}")
        self.assertIn("provider_residue", plan["workspace_observation_contract"]["required_categories"])
        self.assertIn("cleanup_result", plan["workspace_observation_contract"]["required_categories"])

    def test_runpod_asr_serverless_plan_rejects_unbounded_creation_shape(self) -> None:
        provider = RunPodProvider()

        plan = provider.plan_asr_endpoint(gpu_ids="ADA_24", workers_max=0)

        self.assertFalse(plan["ok"])
        self.assertEqual(plan["error"], "invalid_workers_max")

    def test_runpod_asr_serverless_plan_rejects_concrete_gpu_name(self) -> None:
        provider = RunPodProvider()

        plan = provider.plan_asr_endpoint(gpu_ids="NVIDIA L4")

        self.assertFalse(plan["ok"])
        self.assertEqual(plan["error"], "invalid_runpod_gpu_ids")
        self.assertEqual(plan["gpu_selection"]["invalid_pool_ids"], ["NVIDIA L4"])

    def test_runpod_pod_plan_includes_registry_auth_when_private_image_requires_it(self) -> None:
        provider = RunPodProvider()

        with patch.object(
            provider,
            "_gpu_type_info",
            return_value={"ok": True, "lowestPrice": {"uninterruptablePrice": 0.5}},
        ):
            plan = provider.plan_pod_worker(
                gpu_type_id="NVIDIA GeForce RTX 3090",
                image="ghcr.io/noiehoie/gpu-job-control/asr-diarization-worker:test",
                cloud_type="ALL",
                gpu_count=1,
                volume_in_gb=0,
                container_disk_in_gb=80,
                min_vcpu_count=4,
                min_memory_in_gb=16,
                max_uptime_seconds=600,
                max_estimated_cost_usd=0.15,
                docker_args="sleep infinity",
                container_registry_auth_id="registry-auth-id",
            )

        self.assertTrue(plan["ok"])
        self.assertEqual(plan["pod_input"]["containerRegistryAuthId"], "registry-auth-id")
        self.assertEqual(plan["image_pull_credential"]["type"], "runpod_container_registry_auth")

    def test_get_pod_uses_graphql_without_runpod_sdk(self) -> None:
        provider = RunPodProvider()
        graph_result = {
            "data": {
                "pod": {
                    "id": "pod-123",
                    "desiredStatus": "RUNNING",
                    "runtime": {"uptimeInSeconds": 0},
                }
            }
        }

        with (
            patch.object(provider, "_run_graphql", return_value=graph_result) as graphql,
            patch.object(provider, "_run_runpod_python", side_effect=AssertionError("SDK path must not be used")),
        ):
            pod = provider._get_pod("pod-123")

        self.assertEqual(pod["id"], "pod-123")
        query = graphql.call_args.args[0]
        self.assertIn("pod(input:", query)
        self.assertIn('podId: "pod-123"', query)
        self.assertIn("runtime", query)

    def test_get_pod_rejects_missing_graphql_pod(self) -> None:
        provider = RunPodProvider()

        with patch.object(provider, "_run_graphql", return_value={"data": {"pod": None}}):
            with self.assertRaisesRegex(RuntimeError, "runpod pod not found"):
                provider._get_pod("pod-missing")

    def test_terminate_pod_uses_graphql_without_runpod_sdk(self) -> None:
        provider = RunPodProvider()
        graph_result = {
            "data": {
                "podStop": {
                    "id": "pod-123",
                    "desiredStatus": "EXITED",
                }
            }
        }

        with (
            patch.object(provider, "_run_graphql", return_value=graph_result) as graphql,
            patch.object(provider, "_run_runpod_python", side_effect=AssertionError("SDK path must not be used")),
        ):
            result = provider._terminate_pod("pod-123")

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["desiredStatus"], "EXITED")
        query = graphql.call_args.args[0]
        self.assertIn("podStop(input:", query)
        self.assertIn('podId: "pod-123"', query)

    def test_terminate_pod_reports_missing_graphql_result(self) -> None:
        provider = RunPodProvider()

        with patch.object(provider, "_run_graphql", return_value={"data": {"podStop": None}}):
            result = provider._terminate_pod("pod-missing")

        self.assertFalse(result["ok"])
        self.assertIn("podStop returned no pod", result["error"])

    def test_submit_asr_diarization_uses_workspace_canary_artifact_contract(self) -> None:
        provider = RunPodProvider()
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "store")
            job = Job(
                job_id="runpod-asr-diarization-canary",
                job_type="asr",
                input_uri="text://GPU_JOB_ASR_DIARIZATION_WORKSPACE_CANARY",
                output_uri="local://out",
                worker_image="gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                gpu_profile="asr_diarization",
                model="large-v3",
                metadata={"input": {"diarize": True, "language": "ja"}},
                limits={"max_runtime_minutes": 10, "max_cost_usd": 0.15},
            )
            output = {
                "ok": True,
                "observed_runtime": True,
                "observed_http_worker": True,
                "health_url": "https://pod-8000.proxy.runpod.net/health",
                "generate_url": "https://pod-8000.proxy.runpod.net/generate",
                "runtime_seconds": 12.3,
                "health_samples": [{"ok": True, "gpu_probe": {"exit_code": 0}}],
                "generate_result": {
                    "ok": True,
                    "asr_diarization_runtime_ok": True,
                    "model": "pyannote/speaker-diarization-3.1",
                    "diarization_model": "pyannote/speaker-diarization-3.1",
                    "text": "GPU_JOB_ASR_DIARIZATION_WORKSPACE_CANARY_OK",
                    "segments": [{"start": 0.0, "end": 1.0, "text": "GPU_JOB_ASR_DIARIZATION_WORKSPACE_CANARY_OK"}],
                    "speaker_segments": [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}],
                    "speaker_count": 1,
                    "diarization_error": "",
                    "cache_hit": True,
                    "hf_token_present": True,
                    "image_contract_id": "asr-diarization-large-v3-pyannote3.3.2-cuda12.4",
                    "image_contract_marker": "/opt/gpu-job-control/image-contracts/asr-diarization-large-v3-pyannote3.3.2-cuda12.4.json",
                    "image_contract_marker_present": True,
                    "checks": {
                        "hf_token_present": True,
                        "faster_whisper_import": True,
                        "pyannote_import": True,
                        "matplotlib_import": True,
                        "image_contract_marker_present": True,
                        "cache_hit": True,
                    },
                    "gpu_probe": {"exit_code": 0, "stdout": "NVIDIA GeForce RTX 3090, 24576 MiB"},
                    "volume_probe": {"ok": True},
                },
                "actual_cost_guard": {"ok": True, "estimated_cost_usd": 0.01},
                "cleanup": {"ok": True, "terminated": True},
                "pod": {"id": "pod-asr"},
            }

            with patch.object(provider, "canary_pod_http_worker", return_value=output) as canary:
                result = provider.submit(job, store, execute=True)

            self.assertEqual(result.status, "succeeded")
            kwargs = canary.call_args.kwargs
            self.assertEqual(kwargs["worker_mode"], "asr_diarization")
            self.assertTrue(kwargs["image"].startswith("ghcr.io/noiehoie/gpu-job-control/asr-diarization-worker:"))
            self.assertEqual(kwargs["container_registry_auth_id"], "cmo69c98l00iyjj07z22tsrzm")
            self.assertEqual(kwargs["hf_secret_name"], "gpu_job_hf_read")
            artifact_dir = store.artifact_dir(job.job_id)
            result_payload = json.loads((artifact_dir / "result.json").read_text())
            metrics_payload = json.loads((artifact_dir / "metrics.json").read_text())
            probe_payload = json.loads((artifact_dir / "probe_info.json").read_text())
            self.assertEqual(result_payload["speaker_count"], 1)
            self.assertEqual(result_payload["speaker_segments"][0]["speaker"], "SPEAKER_00")
            self.assertTrue(result_payload["diarization_requested"])
            self.assertTrue(metrics_payload["cache_hit"])
            self.assertTrue(probe_payload["cache_hit"])
            self.assertTrue(result_payload["workspace_contract_ok"])
            self.assertTrue(metrics_payload["workspace_contract_ok"])
            self.assertTrue(probe_payload["workspace_contract_ok"])
            self.assertTrue(probe_payload["hf_token_present"])
            self.assertTrue(probe_payload["image_contract_marker_present"])
            self.assertTrue(probe_payload["cleanup_ok"])

    def test_actual_pod_cost_guard_uses_allocated_cost(self) -> None:
        self.assertTrue(_actual_pod_cost_guard({"costPerHr": 0.46}, max_uptime_seconds=60, max_estimated_cost_usd=0.02)["ok"])
        self.assertFalse(_actual_pod_cost_guard({"costPerHr": 0.46}, max_uptime_seconds=180, max_estimated_cost_usd=0.02)["ok"])

    def test_llm_endpoint_requires_explicit_configuration(self) -> None:
        old_endpoint = os.environ.pop("RUNPOD_LLM_ENDPOINT_ID", None)
        old_mode = os.environ.pop("RUNPOD_LLM_ENDPOINT_MODE", None)
        try:
            provider = RunPodProvider()
            endpoints = [{"id": "ep-1", "name": "obvious-llm-endpoint"}]
            self.assertIsNone(provider._llm_endpoint(endpoints))
        finally:
            if old_endpoint is not None:
                os.environ["RUNPOD_LLM_ENDPOINT_ID"] = old_endpoint
            if old_mode is not None:
                os.environ["RUNPOD_LLM_ENDPOINT_MODE"] = old_mode

    def test_llm_endpoint_uses_explicit_configuration_only(self) -> None:
        old_endpoint = os.environ.get("RUNPOD_LLM_ENDPOINT_ID")
        old_mode = os.environ.get("RUNPOD_LLM_ENDPOINT_MODE")
        try:
            os.environ["RUNPOD_LLM_ENDPOINT_ID"] = "ep-configured"
            os.environ["RUNPOD_LLM_ENDPOINT_MODE"] = "openai"
            provider = RunPodProvider()
            endpoint = provider._llm_endpoint([{"id": "ep-other", "name": "llm"}])
            self.assertEqual(endpoint, {"id": "ep-configured", "name": "RUNPOD_LLM_ENDPOINT_ID", "mode": "openai"})
        finally:
            if old_endpoint is None:
                os.environ.pop("RUNPOD_LLM_ENDPOINT_ID", None)
            else:
                os.environ["RUNPOD_LLM_ENDPOINT_ID"] = old_endpoint
            if old_mode is None:
                os.environ.pop("RUNPOD_LLM_ENDPOINT_MODE", None)
            else:
                os.environ["RUNPOD_LLM_ENDPOINT_MODE"] = old_mode


if __name__ == "__main__":
    unittest.main()
