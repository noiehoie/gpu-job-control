from __future__ import annotations

import unittest
import os
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gpu_job.models import Job, now_unix
from gpu_job.policy import load_execution_policy
from gpu_job.policy_engine import validate_policy
from gpu_job.queue import next_runnable_job
from gpu_job.router import capability_policy_decision, route_job, startup_policy_decision, workload_policy_decision
from gpu_job.secrets_policy import secret_check
from gpu_job.store import JobStore


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

    def test_policy_accepts_profile_aware_provider_limits(self) -> None:
        result = validate_policy({"provider_limits": {"modal": {"llm_heavy": 1, "asr": 2, "*": 1}}, "stale_seconds": {}})
        self.assertTrue(result["ok"])

    def test_execution_policy_merges_private_provider_operations(self) -> None:
        with TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "execution-policy.json"
            ops_path = Path(tmp) / "provider-operations.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "provider_limits": {"runpod": {"asr": 1}},
                        "secret_policy": {"allowed_refs": {"*:*:*": []}},
                    }
                )
            )
            ops_path.write_text(
                json.dumps(
                    {
                        "persistent_storage": {
                            "runpod": {
                                "allowed_monthly_usd": 9.1,
                                "allowed_network_volumes": [{"id": "volume-1"}],
                            }
                        },
                        "secret_policy": {"allowed_refs": {"runpod:contract-probe:asr": ["hf_token"]}},
                    }
                )
            )
            with patch.dict(
                os.environ,
                {
                    "GPU_JOB_EXECUTION_POLICY": str(policy_path),
                    "GPU_JOB_PROVIDER_OPERATIONS_POLICY": str(ops_path),
                },
                clear=False,
            ):
                policy = load_execution_policy()

        self.assertEqual(policy["provider_limits"], {"runpod": {"asr": 1}})
        self.assertEqual(policy["persistent_storage"]["runpod"]["allowed_monthly_usd"], 9.1)
        self.assertEqual(policy["secret_policy"]["allowed_refs"]["*:*:*"], [])
        self.assertEqual(policy["secret_policy"]["allowed_refs"]["runpod:contract-probe:asr"], ["hf_token"])

    def test_explicit_execution_policy_path_does_not_merge_provider_operations(self) -> None:
        with TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "execution-policy.json"
            ops_path = Path(tmp) / "provider-operations.json"
            policy_path.write_text(json.dumps({"provider_limits": {"modal": 1}}))
            ops_path.write_text(json.dumps({"secret_policy": {"allowed_refs": {"modal:probe:asr": ["hf_token"]}}}))

            with patch.dict(os.environ, {"GPU_JOB_PROVIDER_OPERATIONS_POLICY": str(ops_path)}, clear=False):
                policy = load_execution_policy(policy_path)

        self.assertEqual(policy, {"provider_limits": {"modal": 1}})

    def test_queue_limits_are_provider_profile_aware(self) -> None:
        old_data_home = os.environ.get("XDG_DATA_HOME")
        try:
            with TemporaryDirectory() as tmp:
                os.environ["XDG_DATA_HOME"] = tmp
                store = JobStore()
                active = make_job()
                active.job_id = "active-llm"
                active.provider = "modal"
                active.status = "running"
                active.metadata["selected_provider"] = "modal"
                store.save(active)

                queued_llm = make_job()
                queued_llm.job_id = "queued-llm"
                queued_llm.provider = "modal"
                queued_llm.status = "queued"
                queued_llm.created_at = 1
                queued_llm.metadata["requested_provider"] = "modal"
                store.save(queued_llm)

                queued_asr = Job(
                    job_id="queued-asr",
                    job_type="asr",
                    input_uri="s3://example/audio.wav",
                    output_uri="s3://example/out",
                    worker_image="worker:asr",
                    gpu_profile="asr",
                    provider="modal",
                    status="queued",
                    created_at=2,
                    metadata={"requested_provider": "modal"},
                )
                store.save(queued_asr)

                job, scheduling = next_runnable_job(
                    store,
                    {"provider_limits": {"modal": {"llm_heavy": 1, "asr": 1}}},
                )

                self.assertIsNotNone(job)
                self.assertEqual(job.job_id, "queued-asr")
                self.assertEqual(scheduling["active"], {"modal:llm_heavy": 1})
                self.assertEqual(scheduling["skipped"][0]["profile_key"], "modal:llm_heavy")
        finally:
            if old_data_home is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = old_data_home

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

    def test_batch_size_does_not_imply_burst_concurrency(self) -> None:
        job = make_job(routing={"batch_size": 15, "burst_size": 1, "estimated_gpu_runtime_seconds": 5})
        profile = {"burst_policy": {"ollama_max_burst_size": 1}}
        signal = {"provider": "ollama", "estimated_startup_seconds": 0}
        result = workload_policy_decision(job, profile, signal)
        self.assertTrue(result["ok"])
        self.assertEqual(result["batch_size"], 15)
        self.assertEqual(result["burst_size"], 1)

    def test_modal_prefers_burst_fanout(self) -> None:
        job = make_job(routing={"burst_size": 25, "estimated_gpu_runtime_seconds": 5})
        profile = {"burst_policy": {"modal_preferred_burst_size": 5}}
        signal = {"provider": "modal", "estimated_startup_seconds": 10}
        result = workload_policy_decision(job, profile, signal)
        self.assertTrue(result["ok"])
        self.assertIn("modal preferred for burst fanout", result["preferences"])

    def test_modal_rejects_prompt_above_model_context_capability(self) -> None:
        from gpu_job.capabilities import evaluate_model_capability

        job = make_job(routing={"estimated_input_tokens": 44415, "estimated_gpu_runtime_seconds": 30})
        result = evaluate_model_capability(job, "modal")
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["tokens_ok"])

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

    def test_route_skips_provider_with_open_circuit(self) -> None:
        old_data_home = os.environ.get("XDG_DATA_HOME")
        try:
            with TemporaryDirectory() as tmp:
                os.environ["XDG_DATA_HOME"] = tmp
                store = JobStore()
                now = now_unix()
                for idx in range(5):
                    failed = make_job()
                    failed.job_id = f"modal-failed-{idx}"
                    failed.provider = "modal"
                    failed.status = "failed"
                    failed.created_at = now + idx
                    failed.updated_at = now + idx
                    failed.metadata["selected_provider"] = "modal"
                    store.save(failed)

                def _signal(name, _profile):
                    return {
                        "provider": name,
                        "available": True,
                        "estimated_startup_seconds": 1,
                    }

                with (
                    patch("gpu_job.router.provider_signal", side_effect=_signal),
                    patch("gpu_job.router.collect_stats", return_value={"ok": True, "groups": {}}),
                ):
                    result = route_job(make_job(routing={"estimated_gpu_runtime_seconds": 5}))

                self.assertEqual(result["selected_provider"], "ollama")
                self.assertFalse(result["provider_decisions"]["modal"]["eligible"])
                self.assertEqual(result["provider_decisions"]["modal"]["circuit"]["state"], "open")
        finally:
            if old_data_home is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = old_data_home


if __name__ == "__main__":
    unittest.main()
