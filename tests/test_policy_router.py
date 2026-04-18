from __future__ import annotations

import unittest

from gpu_job.models import Job
from gpu_job.policy_engine import validate_policy
from gpu_job.router import capability_policy_decision, startup_policy_decision, workload_policy_decision
from gpu_job.secrets_policy import secret_check


def make_job(**metadata) -> Job:
    return Job(
        job_id="llm_heavy-test",
        job_type="llm_heavy",
        input_uri="text://hello",
        output_uri="local://out",
        worker_image="local:test",
        gpu_profile="llm_heavy",
        limits={"max_runtime_minutes": 10},
        metadata=metadata,
    )


class PolicyAndRouterTest(unittest.TestCase):
    def test_policy_requires_provider_limits(self) -> None:
        result = validate_policy({"stale_seconds": {}})
        self.assertFalse(result["ok"])
        self.assertIn("provider_limits must be a non-empty object", result["errors"])

    def test_capability_rejects_unsupported_job_type(self) -> None:
        job = Job(
            job_id="asr-test",
            job_type="asr",
            input_uri="s3://example/input.mp4",
            output_uri="s3://example/output/",
            worker_image="worker:test",
            gpu_profile="asr_fast",
        )
        result = capability_policy_decision(job, "ollama")
        self.assertFalse(result["ok"])
        self.assertIn("provider does not execute job_type", result["reason"])

    def test_startup_policy_uses_amortized_fraction(self) -> None:
        job = make_job()
        profile = {"startup_policy": {"mode": "amortized", "max_startup_fraction": 0.2}}
        accepted = startup_policy_decision(job, profile, {"estimated_startup_seconds": 60})
        rejected = startup_policy_decision(job, profile, {"estimated_startup_seconds": 180})
        self.assertTrue(accepted["ok"])
        self.assertFalse(rejected["ok"])

    def test_ollama_rejects_large_burst(self) -> None:
        job = make_job(routing={"burst_size": 25, "estimated_gpu_runtime_seconds": 5})
        profile = {"burst_policy": {"ollama_max_burst_size": 1}}
        signal = {"provider": "ollama", "estimated_startup_seconds": 0}
        result = workload_policy_decision(job, profile, signal)
        self.assertFalse(result["ok"])
        self.assertIn("burst workload exceeds resident ollama concurrency", result["reason"])

    def test_modal_prefers_burst_fanout(self) -> None:
        job = make_job(routing={"burst_size": 25, "estimated_gpu_runtime_seconds": 5})
        profile = {"burst_policy": {"modal_preferred_burst_size": 5}}
        signal = {"provider": "modal", "estimated_startup_seconds": 10}
        result = workload_policy_decision(job, profile, signal)
        self.assertTrue(result["ok"])
        self.assertIn("modal preferred for burst fanout", result["preferences"])

    def test_quality_required_vlm_excludes_local_and_ollama(self) -> None:
        job = Job(
            job_id="vlm-quality-test",
            job_type="vlm_ocr",
            input_uri="text://vision",
            output_uri="local://out",
            worker_image="auto",
            gpu_profile="vlm_ocr",
            limits={"max_runtime_minutes": 10},
            metadata={
                "routing": {
                    "quality_requires_gpu": True,
                    "estimated_gpu_runtime_seconds": 30,
                    "estimated_cpu_runtime_seconds": 0,
                    "burst_size": 1,
                }
            },
        )
        profile = {"burst_policy": {"ollama_max_burst_size": 1}}
        for provider in ("local", "ollama"):
            result = workload_policy_decision(job, profile, {"provider": provider, "estimated_startup_seconds": 0})
            self.assertFalse(result["ok"])
            self.assertIn("quality_requires_gpu excludes", result["reason"])

    def test_modal_supports_quality_required_vlm_job_type(self) -> None:
        job = Job(
            job_id="vlm-quality-test",
            job_type="vlm_ocr",
            input_uri="text://vision",
            output_uri="local://out",
            worker_image="auto",
            gpu_profile="vlm_ocr",
            limits={"max_runtime_minutes": 10},
            metadata={
                "routing": {
                    "quality_requires_gpu": True,
                    "estimated_gpu_runtime_seconds": 30,
                    "estimated_cpu_runtime_seconds": 0,
                    "burst_size": 1,
                }
            },
        )
        result = capability_policy_decision(job, "modal")
        self.assertTrue(result["ok"])

    def test_secret_policy_denies_unlisted_refs(self) -> None:
        job = make_job(source_system="my-app", secret_refs=["allowed", "denied"])
        policy = {
            "provider_limits": {"modal": 1},
            "secret_policy": {"allowed_refs": {"modal:my-app:llm_heavy": ["allowed"]}},
        }
        result = secret_check(job, provider="modal", policy=policy)
        self.assertFalse(result["ok"])
        self.assertEqual(result["denied_secret_refs"], ["denied"])


if __name__ == "__main__":
    unittest.main()
