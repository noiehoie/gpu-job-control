from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import unittest

from gpu_job.models import Job
from gpu_job.providers.runpod import RunPodProvider
from gpu_job.providers.runpod import _actual_pod_cost_guard
from gpu_job.providers.runpod import _pod_http_worker_docker_args
from gpu_job.providers.runpod import _pod_has_runtime
from gpu_job.providers.runpod import _openai_chat_payload
from gpu_job.providers.runpod import _runpod_api_key


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
        env = {item["key"]: item["value"] for item in plan["template"]["env"]}
        self.assertEqual(env["MODEL_NAME"], "Qwen/Qwen2.5-0.5B-Instruct")
        self.assertEqual(env["HF_TOKEN"], "{{ RUNPOD_SECRET_gpu_job_hf_read }}")
        self.assertNotIn("hf_D", json.dumps(plan))

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

    def test_vllm_endpoint_invariant_allows_observed_standby_without_workers_min(self) -> None:
        provider = RunPodProvider()
        result = provider._endpoint_scale_to_zero_invariant({"workersMin": 0, "workersStandby": 1, "workersMax": 1})
        self.assertTrue(result["ok"])

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

    def test_actual_pod_cost_guard_uses_allocated_cost(self) -> None:
        self.assertTrue(_actual_pod_cost_guard({"costPerHr": 0.46}, max_uptime_seconds=60, max_estimated_cost_usd=0.02)["ok"])
        self.assertFalse(_actual_pod_cost_guard({"costPerHr": 0.46}, max_uptime_seconds=180, max_estimated_cost_usd=0.02)["ok"])


if __name__ == "__main__":
    unittest.main()
