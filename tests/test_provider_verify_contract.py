from __future__ import annotations

import json
import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gpu_job.models import Job
from gpu_job.providers.ollama import OllamaProvider
from gpu_job.store import JobStore
from gpu_job.verify import application_verify_payload


def make_job(job_id: str = "verify-contract") -> Job:
    return Job(
        job_id=job_id,
        job_type="llm_heavy",
        input_uri="text://hello",
        output_uri="local://out",
        worker_image="auto",
        gpu_profile="llm_heavy",
        provider="ollama",
        model="qwen2.5:32b",
        limits={"max_runtime_minutes": 1},
        metadata={"input": {"prompt": "hello"}},
    )


class ProviderVerifyContractTest(unittest.TestCase):
    def test_application_verify_rejects_empty_llm_text(self) -> None:
        payload = application_verify_payload("llm_heavy", {"text": ""})
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["checks"]["text_nonempty"])

    def test_ollama_empty_text_fails_job_and_verify(self) -> None:
        old_data_home = os.environ.get("XDG_DATA_HOME")
        try:
            with TemporaryDirectory() as tmp:
                os.environ["XDG_DATA_HOME"] = tmp
                store = JobStore()
                job = make_job()

                with patch("gpu_job.providers.ollama._ollama_json", return_value={"response": "", "done": True}):
                    saved = OllamaProvider().submit(job, store=store, execute=True)

                self.assertEqual(saved.status, "failed")
                verify = json.loads((store.artifact_dir(job.job_id) / "verify.json").read_text())
                self.assertFalse(verify["ok"])
                self.assertFalse(verify["application_verify"]["checks"]["text_nonempty"])
        finally:
            if old_data_home is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = old_data_home


if __name__ == "__main__":
    unittest.main()
