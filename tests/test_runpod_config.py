from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest

from gpu_job.models import Job
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


if __name__ == "__main__":
    unittest.main()
