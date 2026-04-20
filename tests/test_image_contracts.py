from __future__ import annotations

import unittest

from gpu_job.image_contracts import image_contract_status


class ImageContractRegistryTest(unittest.TestCase):
    def test_verified_contract_satisfies_required_backends(self) -> None:
        registry = {
            "registry_version": "gpu-job-image-contract-registry-v1",
            "image_contracts": {
                "asr-fast": {
                    "contract_id": "asr-fast",
                    "status": "verified",
                    "image": "gpu-job/asr-worker:test",
                    "provides_backends": ["faster_whisper"],
                }
            },
        }
        runtime = {"image_contract_id": "asr-fast"}

        result = image_contract_status(runtime, {"transcription": "faster_whisper"}, registry=registry)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["contract_id"], "asr-fast")

    def test_missing_contract_is_caller_visible_blocker(self) -> None:
        registry = {
            "registry_version": "gpu-job-image-contract-registry-v1",
            "image_contracts": {
                "asr-diarization": {
                    "contract_id": "asr-diarization",
                    "status": "missing",
                    "image": "gpu-job/asr-diarization-worker:test",
                    "provides_backends": ["faster_whisper", "pyannote"],
                }
            },
        }
        runtime = {"image_contract_id": "asr-diarization"}

        result = image_contract_status(
            runtime,
            {"transcription": "faster_whisper", "speaker_diarization": "pyannote"},
            registry=registry,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["contract_id"], "asr-diarization")
        self.assertIn("image contract is not verified", result["reason"])

    def test_contract_missing_backend_is_not_accepted(self) -> None:
        registry = {
            "registry_version": "gpu-job-image-contract-registry-v1",
            "image_contracts": {
                "asr-fast": {
                    "contract_id": "asr-fast",
                    "status": "verified",
                    "image": "gpu-job/asr-worker:test",
                    "provides_backends": ["faster_whisper"],
                }
            },
        }
        runtime = {"image_contract_id": "asr-fast"}

        result = image_contract_status(
            runtime,
            {"transcription": "faster_whisper", "speaker_diarization": "pyannote"},
            registry=registry,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "image_contract_missing_backend")
        self.assertEqual(result["missing_backends"], ["pyannote"])


if __name__ == "__main__":
    unittest.main()
