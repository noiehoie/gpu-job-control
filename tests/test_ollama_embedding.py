from __future__ import annotations

import json
import unittest
from pathlib import Path

from gpu_job.models import Job
from gpu_job.providers import ollama
from gpu_job.router import capability_policy_decision


def embedding_job(**metadata) -> Job:
    return Job(
        job_id="embedding-test",
        job_type="embedding",
        input_uri="text://hello",
        output_uri="local://out",
        worker_image="auto",
        gpu_profile="embedding",
        model="bge-m3",
        limits={"max_runtime_minutes": 5},
        metadata=metadata,
    )


class OllamaEmbeddingTest(unittest.TestCase):
    def test_ollama_capability_supports_embedding(self) -> None:
        result = capability_policy_decision(embedding_job(), "ollama")
        self.assertTrue(result["ok"])
        self.assertIn("embedding", result["supported_job_types"])

    def test_embedding_profile_prefers_ollama_with_generic_cloud_fallbacks(self) -> None:
        config = json.loads(Path("config/gpu-profiles.json").read_text())
        profile = config["profiles"]["embedding"]
        self.assertEqual(profile["preferred_provider"], "ollama")
        self.assertEqual(profile["fallback_providers"], ["local", "modal", "runpod", "vast"])

    def test_bge_m3_capability_is_registered(self) -> None:
        config = json.loads(Path("config/model-capabilities.json").read_text())
        capability = config["models"]["ollama:bge-m3"]
        self.assertEqual(capability["provider"], "ollama")
        self.assertIn("embedding", capability["job_types"])
        self.assertEqual(capability["quality_tier"], "high")

    def test_embedding_input_accepts_texts_text_prompt_and_text_uri(self) -> None:
        self.assertEqual(ollama._embedding_input_texts(embedding_job(input={"texts": ["a", "b"]})), ["a", "b"])
        self.assertEqual(ollama._embedding_input_texts(embedding_job(input={"text": "one"})), ["one"])
        self.assertEqual(ollama._embedding_input_texts(embedding_job(input={"prompt": "two"})), ["two"])
        self.assertEqual(ollama._embedding_input_texts(embedding_job()), ["hello"])

    def test_embedding_vectors_accepts_current_and_legacy_ollama_schema(self) -> None:
        self.assertEqual(ollama._embedding_vectors({"embeddings": [[0, 1.5], [2, 3]]}), [[0.0, 1.5], [2.0, 3.0]])
        self.assertEqual(ollama._embedding_vectors({"embedding": [0, 1.5]}), [[0.0, 1.5]])


if __name__ == "__main__":
    unittest.main()
