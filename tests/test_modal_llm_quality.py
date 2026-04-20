from __future__ import annotations

import json
import sys
import types
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from gpu_job.verify import verify_artifacts
from gpu_job.manifest import write_manifest


class _FakeImage:
    @classmethod
    def debian_slim(cls, python_version: str = "") -> "_FakeImage":
        return cls()

    def pip_install(self, *packages: str) -> "_FakeImage":
        return self

    def run_commands(self, *commands: str) -> "_FakeImage":
        return self

    def env(self, values: dict[str, str]) -> "_FakeImage":
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


class _FakeVolume:
    @classmethod
    def from_name(cls, name: str, create_if_missing: bool = False) -> "_FakeVolume":
        instance = cls()
        instance.name = name
        instance.create_if_missing = create_if_missing
        return instance

    def commit(self) -> None:
        return None


if "modal" not in sys.modules:
    fake_modal = types.SimpleNamespace(Image=_FakeImage, App=_FakeApp, Volume=_FakeVolume)
    sys.modules["modal"] = fake_modal

from gpu_job.modal_llm import (  # noqa: E402
    CANARY_MODEL,
    DEFAULT_HEAVY_MODEL,
    MODAL_LLM_CACHE_MOUNT,
    MODAL_LLM_CACHE_VOLUME_NAME,
    MODAL_LLM_HF_HOME,
    MODAL_LLM_PACKAGES,
    MODAL_LLM_POST_INSTALL_COMMANDS,
    MODAL_LLM_PYTHON_VERSION,
    _known_context_limit,
    _model_context_limit,
    _model_name,
    _prompt,
)


class ModalLlmQualityTest(unittest.TestCase):
    def test_quality_alias_maps_to_heavy_model(self) -> None:
        job = {
            "job_type": "llm_heavy",
            "model": "claude-sonnet-4-6",
            "metadata": {"routing": {"quality_requires_gpu": True}},
        }
        self.assertEqual(_model_name(job), DEFAULT_HEAVY_MODEL)

    def test_claude_haiku_alias_maps_to_heavy_model(self) -> None:
        job = {
            "job_type": "llm_heavy",
            "model": "claude-haiku-4-5-20251001",
            "metadata": {"routing": {"quality_requires_gpu": False}},
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

    def test_empty_model_uses_heavy_model_without_canary_fallback(self) -> None:
        job = {"job_type": "llm_heavy", "metadata": {"routing": {"quality_requires_gpu": False}}}
        self.assertEqual(_model_name(job), DEFAULT_HEAVY_MODEL)

    def test_quality_empty_model_uses_heavy_model(self) -> None:
        job = {"job_type": "llm_heavy", "metadata": {"routing": {"quality_requires_gpu": True}}}
        self.assertEqual(_model_name(job), DEFAULT_HEAVY_MODEL)

    def test_unknown_model_rejects_instead_of_canary_fallback(self) -> None:
        job = {
            "job_type": "llm_heavy",
            "model": "unknown-small-model",
            "metadata": {"routing": {"quality_requires_gpu": False}},
        }
        with self.assertRaises(ValueError):
            _model_name(job)

    def test_context_limit_reads_common_config_fields(self) -> None:
        class Config:
            max_position_embeddings = 32768

        class Model:
            config = Config()

        self.assertEqual(_model_context_limit(Model()), 32768)

    def test_known_context_limit_for_modal_heavy_model(self) -> None:
        self.assertEqual(_known_context_limit(DEFAULT_HEAVY_MODEL), 32768)

    def test_modal_heavy_model_avoids_awq_loader_dependency(self) -> None:
        self.assertEqual(MODAL_LLM_PYTHON_VERSION, "3.11")
        self.assertIn("torch", MODAL_LLM_PACKAGES)
        self.assertIn("huggingface_hub", MODAL_LLM_PACKAGES)
        self.assertNotIn("AWQ", DEFAULT_HEAVY_MODEL)
        self.assertFalse(any("gptqmodel" in command for command in MODAL_LLM_POST_INSTALL_COMMANDS))

    def test_modal_llm_uses_persistent_hf_cache_volume(self) -> None:
        self.assertEqual(MODAL_LLM_CACHE_VOLUME_NAME, "gpu-job-modal-llm-cache")
        self.assertEqual(MODAL_LLM_CACHE_MOUNT, "/cache")
        self.assertEqual(MODAL_LLM_HF_HOME, "/cache/huggingface")

    def test_prompt_includes_workflow_chunk_items(self) -> None:
        job = {
            "input_uri": "workflow://generic-map-reduce/chunks/0",
            "metadata": {
                "input": {
                    "prompt": "Rank these articles.",
                    "items": [{"article_id": "a1", "title": "Alpha"}],
                }
            },
        }

        prompt = _prompt(job)

        self.assertIn("Rank these articles.", prompt)
        self.assertIn("INPUT_JSON", prompt)
        self.assertIn("article_id", prompt)
        self.assertIn("Alpha", prompt)

    def test_prompt_uses_items_without_prompt(self) -> None:
        job = {
            "input_uri": "workflow://generic-map-reduce/chunks/0",
            "metadata": {"input": {"items": [{"article_id": "a1", "title": "Alpha"}]}},
        }

        prompt = _prompt(job)

        self.assertIn("items", prompt)
        self.assertIn("article_id", prompt)
        self.assertNotIn("workflow://", prompt)


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

    def test_verify_artifacts_rejects_verify_json_without_ok(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "result.json").write_text(json.dumps({"text": "ok"}) + "\n")
            (path / "metrics.json").write_text(json.dumps({"runtime_seconds": 1}) + "\n")
            (path / "verify.json").write_text(json.dumps({"checks": {"text_nonempty": True}}) + "\n")
            (path / "stdout.log").write_text("")
            (path / "stderr.log").write_text("")

            result = verify_artifacts(path)
            self.assertFalse(result["ok"])
            self.assertEqual(result["missing"], [])

    def test_verify_artifacts_can_require_manifest(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "result.json").write_text(json.dumps({"text": "ok"}) + "\n")
            (path / "metrics.json").write_text(json.dumps({"runtime_seconds": 1}) + "\n")
            (path / "verify.json").write_text(json.dumps({"ok": True, "checks": {"text_nonempty": True}}) + "\n")
            (path / "stdout.log").write_text("")
            (path / "stderr.log").write_text("")

            without_manifest = verify_artifacts(path, require_manifest=True)
            self.assertFalse(without_manifest["ok"])
            self.assertFalse(without_manifest["manifest"]["manifest_present"])

            write_manifest(path)
            with_manifest = verify_artifacts(path, require_manifest=True)
            self.assertTrue(with_manifest["ok"])
            self.assertTrue(with_manifest["manifest"]["manifest_present"])

    def test_verify_artifacts_rejects_gpu_bound_without_gpu_metrics(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "result.json").write_text(json.dumps({"text": "ok"}) + "\n")
            (path / "metrics.json").write_text(json.dumps({"provider": "modal", "runtime_seconds": 1}) + "\n")
            (path / "verify.json").write_text(json.dumps({"ok": True, "checks": {"text_nonempty": True}}) + "\n")
            (path / "stdout.log").write_text("")
            (path / "stderr.log").write_text("")
            write_manifest(path)

            result = verify_artifacts(path, require_manifest=True, require_gpu_utilization=True)

            self.assertFalse(result["ok"])
            self.assertFalse(result["hardware_verify"]["ok"])
            self.assertEqual(result["hardware_verify"]["reason"], "gpu utilization evidence missing")

    def test_verify_artifacts_accepts_gpu_bound_with_gpu_metrics(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "result.json").write_text(json.dumps({"text": "ok"}) + "\n")
            (path / "metrics.json").write_text(json.dumps({"provider": "modal", "gpu_samples": [{"gpu_utilization_percent": 7.5}]}) + "\n")
            (path / "verify.json").write_text(json.dumps({"ok": True, "checks": {"text_nonempty": True}}) + "\n")
            (path / "stdout.log").write_text("")
            (path / "stderr.log").write_text("")
            write_manifest(path)

            result = verify_artifacts(path, require_manifest=True, require_gpu_utilization=True)

            self.assertTrue(result["ok"])
            self.assertTrue(result["hardware_verify"]["ok"])


if __name__ == "__main__":
    unittest.main()
