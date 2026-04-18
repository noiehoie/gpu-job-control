from __future__ import annotations

import json
import sys
import types
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from gpu_job.verify import verify_artifacts


class _FakeImage:
    @classmethod
    def debian_slim(cls, python_version: str = "") -> "_FakeImage":
        return cls()

    def pip_install(self, *packages: str) -> "_FakeImage":
        return self


class _FakeApp:
    def __init__(self, name: str) -> None:
        self.name = name

    def function(self, **kwargs):
        def decorator(fn):
            return fn

        return decorator

    def local_entrypoint(self):
        def decorator(fn):
            return fn

        return decorator


if "modal" not in sys.modules:
    fake_modal = types.SimpleNamespace(Image=_FakeImage, App=_FakeApp)
    sys.modules["modal"] = fake_modal

from gpu_job.modal_llm import CANARY_MODEL, DEFAULT_HEAVY_MODEL, MODAL_LLM_PACKAGES, _model_context_limit, _model_name


class ModalLlmQualityTest(unittest.TestCase):
    def test_quality_alias_maps_to_heavy_model(self) -> None:
        job = {
            "job_type": "llm_heavy",
            "model": "claude-sonnet-4-6",
            "metadata": {"routing": {"quality_requires_gpu": True}},
        }
        self.assertEqual(_model_name(job), DEFAULT_HEAVY_MODEL)

    def test_quality_job_rejects_canary_model(self) -> None:
        job = {
            "job_type": "llm_heavy",
            "model": CANARY_MODEL,
            "metadata": {"routing": {"quality_requires_gpu": True}},
        }
        with self.assertRaises(ValueError):
            _model_name(job)

    def test_non_quality_empty_model_uses_canary_model(self) -> None:
        job = {"job_type": "llm_heavy", "metadata": {"routing": {"quality_requires_gpu": False}}}
        self.assertEqual(_model_name(job), CANARY_MODEL)

    def test_quality_empty_model_uses_heavy_model(self) -> None:
        job = {"job_type": "llm_heavy", "metadata": {"routing": {"quality_requires_gpu": True}}}
        self.assertEqual(_model_name(job), DEFAULT_HEAVY_MODEL)

    def test_context_limit_reads_common_config_fields(self) -> None:
        class Config:
            max_position_embeddings = 32768

        class Model:
            config = Config()

        self.assertEqual(_model_context_limit(Model()), 32768)

    def test_awq_loader_dependency_is_installed_in_modal_image(self) -> None:
        self.assertIn("gptqmodel", MODAL_LLM_PACKAGES)


class VerifyPayloadTest(unittest.TestCase):
    def test_verify_artifacts_rejects_verify_json_ok_false(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "result.json").write_text(json.dumps({"text": ""}) + "\n")
            (path / "metrics.json").write_text(json.dumps({"runtime_seconds": 1}) + "\n")
            (path / "verify.json").write_text(json.dumps({"ok": False, "checks": {"text_nonempty": False}}) + "\n")
            (path / "stdout.log").write_text("")
            (path / "stderr.log").write_text("")

            result = verify_artifacts(path)
            self.assertFalse(result["ok"])
            self.assertEqual(result["missing"], [])


if __name__ == "__main__":
    unittest.main()
