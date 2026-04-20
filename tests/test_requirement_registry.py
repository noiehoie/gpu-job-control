from __future__ import annotations

import unittest
import os
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gpu_job.contracts import plan_workload, workload_to_workflow
from gpu_job.requirements import (
    evaluate_workload_requirements,
    load_requirement_registry,
    workload_gpu_profile,
)


class RequirementRegistryTest(unittest.TestCase):
    def _diarization_request(self, **overrides):
        request = {
            "workload_kind": "transcription.whisper",
            "job_type": "asr",
            "requirements": {"speaker_diarization": True},
            "hints": {"language": "ja", "speaker_model": "pyannote/speaker-diarization-3.1"},
            "business_context": {"app_id": "media-system"},
        }
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(request.get(key), dict):
                request[key].update(value)
            else:
                request[key] = value
        return request

    def test_registry_loads_official_capability_backend_runtime_mapping(self) -> None:
        registry = load_requirement_registry()

        self.assertEqual(registry["registry_version"], "gpu-job-requirement-registry-v1")
        self.assertIn("speaker_diarization", registry["capabilities"])
        self.assertEqual(
            registry["provider_runtimes"]["modal:asr_diarization"]["supports_backends"],
            ["faster_whisper", "pyannote"],
        )

    def test_diarization_request_is_resolved_deterministically_from_registry(self) -> None:
        request = self._diarization_request()

        with patch("gpu_job.provider_contract_probe.recent_contract_probe_summary", return_value={"latest": {}}):
            result = evaluate_workload_requirements(request, provider="modal")

        self.assertEqual(workload_gpu_profile(request), "asr_diarization")
        self.assertEqual(result["decision"], "requires_action")
        self.assertEqual(result["rule_id"], "transcription_whisper_speaker_diarization")
        self.assertEqual(result["backends"]["transcription"], "faster_whisper")
        self.assertEqual(result["backends"]["speaker_diarization"], "pyannote")
        self.assertEqual(result["provider_runtime"]["expected_gpu"], "A10G")
        actions = {item["action"] for item in result["required_actions"]}
        self.assertIn("authorize_secret", actions)
        self.assertIn("create_provider_secret", actions)
        self.assertIn("run_contract_probe", actions)
        self.assertEqual(result["image_contract"]["status"], "verified")

    def test_verified_image_contract_and_runtime_probe_satisfy_requirements(self) -> None:
        request = self._diarization_request()
        policy = {"secret_policy": {"allowed_refs": {"modal:media-system:asr": ["hf_token"]}}}
        probe_summary = {
            "latest": {
                "modal.asr_diarization.pyannote": {
                    "ok": True,
                    "verdict": "pass",
                }
            }
        }
        image_registry = {
            "registry_version": "gpu-job-image-contract-registry-v1",
            "image_contracts": {
                "asr-diarization-large-v3-pyannote3.3.2-cuda12.4": {
                    "contract_id": "asr-diarization-large-v3-pyannote3.3.2-cuda12.4",
                    "status": "verified",
                    "image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                    "provides_backends": ["faster_whisper", "pyannote"],
                }
            },
        }

        with (
            patch("gpu_job.provider_contract_probe.recent_contract_probe_summary", return_value=probe_summary),
            patch("gpu_job.image_contracts.load_image_contract_registry", return_value=image_registry),
        ):
            result = evaluate_workload_requirements(request, provider="modal", policy=policy)

        self.assertEqual(result["decision"], "can_run_now")
        statuses = {(item.get("type"), item.get("id") or item.get("secret_ref")): item["status"] for item in result["requirements"]}
        self.assertEqual(statuses[("secret", "hf_token")], "satisfied")
        self.assertEqual(statuses[("worker_dependency", "pyannote.audio")], "satisfied_by_image_contract")
        self.assertEqual(statuses[("provider_secret_binding", "hf_token")], "satisfied_by_contract_probe")

    def test_runpod_diarization_requires_runtime_contract_probe_even_with_verified_image(self) -> None:
        request = self._diarization_request()
        policy = {"secret_policy": {"allowed_refs": {"runpod:media-system:asr": ["hf_token"]}}}
        image_registry = {
            "registry_version": "gpu-job-image-contract-registry-v1",
            "image_contracts": {
                "asr-diarization-large-v3-pyannote3.3.2-cuda12.4": {
                    "contract_id": "asr-diarization-large-v3-pyannote3.3.2-cuda12.4",
                    "status": "verified",
                    "image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                    "provides_backends": ["faster_whisper", "pyannote"],
                }
            },
        }

        with (
            patch("gpu_job.provider_contract_probe.recent_contract_probe_summary", return_value={"latest": {}}),
            patch("gpu_job.image_contracts.load_image_contract_registry", return_value=image_registry),
        ):
            result = evaluate_workload_requirements(request, provider="runpod", policy=policy)

        self.assertEqual(result["decision"], "requires_action")
        actions = {item["action"] for item in result["required_actions"]}
        self.assertIn("run_contract_probe", actions)
        blockers = {(item.get("type"), item.get("id")): item["status"] for item in result["blockers"]}
        self.assertEqual(blockers[("runtime_contract_probe", "runpod.asr_diarization.pyannote")], "unverified")

    def test_runpod_diarization_probe_and_verified_image_satisfy_requirements(self) -> None:
        request = self._diarization_request()
        policy = {"secret_policy": {"allowed_refs": {"runpod:media-system:asr": ["hf_token"]}}}
        probe_summary = {
            "latest": {
                "runpod.asr_diarization.pyannote": {
                    "ok": True,
                    "verdict": "pass",
                    "checks": {"cache_contract_ok": True},
                }
            }
        }
        image_registry = {
            "registry_version": "gpu-job-image-contract-registry-v1",
            "image_contracts": {
                "asr-diarization-large-v3-pyannote3.3.2-cuda12.4": {
                    "contract_id": "asr-diarization-large-v3-pyannote3.3.2-cuda12.4",
                    "status": "verified",
                    "image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                    "provides_backends": ["faster_whisper", "pyannote"],
                }
            },
        }

        with (
            patch("gpu_job.provider_contract_probe.recent_contract_probe_summary", return_value=probe_summary),
            patch("gpu_job.image_contracts.load_image_contract_registry", return_value=image_registry),
        ):
            result = evaluate_workload_requirements(request, provider="runpod", policy=policy)

        self.assertEqual(result["decision"], "can_run_now")
        statuses = {(item.get("type"), item.get("id") or item.get("secret_ref")): item["status"] for item in result["requirements"]}
        self.assertEqual(statuses[("secret", "hf_token")], "satisfied")
        self.assertEqual(statuses[("provider_secret_binding", "hf_token")], "satisfied_by_contract_probe")

    def test_plain_transcription_uses_fast_asr_runtime_without_secret_actions(self) -> None:
        request = {
            "workload_kind": "transcription.whisper",
            "job_type": "asr",
            "requirements": {},
            "hints": {"language": "ja"},
            "business_context": {"app_id": "media-system"},
        }

        result = evaluate_workload_requirements(request, provider="modal")

        self.assertEqual(workload_gpu_profile(request), "asr_fast")
        self.assertEqual(result["decision"], "can_run_now")
        self.assertEqual(result["rule_id"], "transcription_whisper")
        self.assertEqual(result["backends"], {"transcription": "faster_whisper"})
        self.assertEqual(result["provider_runtime"]["expected_gpu"], "T4")
        self.assertEqual(result["required_actions"], [])

    def test_unknown_speaker_model_requires_backend_registration(self) -> None:
        request = self._diarization_request(hints={"speaker_model": "unknown/speaker-model"})

        result = evaluate_workload_requirements(request, provider="modal")

        self.assertEqual(result["decision"], "requires_backend_registration")
        self.assertEqual(result["rule_id"], "transcription_whisper_speaker_diarization")
        self.assertEqual(result["requested_model"], "unknown/speaker-model")
        self.assertEqual(result["required_actions"], [{"action": "register_backend"}])

    def test_unknown_runtime_profile_requires_runtime_registration(self) -> None:
        request = self._diarization_request(hints={"gpu_profile": "asr_future_gpu"})

        result = evaluate_workload_requirements(request, provider="modal")

        self.assertEqual(result["decision"], "requires_backend_registration")
        self.assertEqual(result["required_actions"], [{"action": "register_provider_runtime"}])

    def test_plan_workload_does_not_run_unregistered_backend(self) -> None:
        result = plan_workload(
            {
                "workload_kind": "transcription.whisper",
                "inputs": [{"uri": "/tmp/video.mp4", "duration_seconds": 60}],
                "requirements": {"speaker_diarization": True},
                "hints": {"speaker_model": "unknown/speaker-model"},
                "business_context": {"app_id": "media-system", "budget_class": "critical"},
            }
        )

        plan = result["plan"]
        self.assertFalse(result["ok"])
        self.assertFalse(plan["can_run_now"])
        self.assertEqual(plan["approval"]["decision"], "requires_backend_registration")
        self.assertEqual(plan["action_requirements"]["decision"], "requires_backend_registration")

    def test_workload_to_workflow_unknown_runtime_is_rejected_before_queueing(self) -> None:
        from gpu_job.workflow import execute_workflow, plan_workflow

        old_home = os.environ.get("XDG_DATA_HOME")
        with TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            try:
                workflow = workload_to_workflow(
                    {
                        "workload_kind": "transcription.whisper",
                        "inputs": [{"uri": "/tmp/video.mp4", "duration_seconds": 60}],
                        "requirements": {"speaker_diarization": True},
                        "hints": {"gpu_profile": "asr_future_gpu"},
                        "business_context": {"app_id": "media-system", "budget_class": "critical"},
                    }
                )

                planned = plan_workflow(workflow)
                executed = execute_workflow(workflow)

                self.assertFalse(planned["ok"])
                self.assertFalse(planned["plan"]["can_run_now"])
                self.assertEqual(planned["approval"]["decision"], "requires_backend_registration")
                self.assertFalse(executed["ok"])
                self.assertEqual(executed["workflow"]["status"], "rejected")
            finally:
                if old_home is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
