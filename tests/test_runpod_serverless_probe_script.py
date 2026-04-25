from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _load_script_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "runpod-asr-serverless-contract-probe.py"
    spec = importlib.util.spec_from_file_location("runpod_asr_serverless_contract_probe", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load script module: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunPodServerlessProbeScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_script_module()

    def test_rest_gpu_type_ids_expand_pool_aliases_to_concrete_gpu_names(self) -> None:
        values = self.module._rest_gpu_type_ids("AMPERE_16,AMPERE_24,ADA_24")

        self.assertIn("NVIDIA RTX A4000", values)
        self.assertIn("NVIDIA RTX A5000", values)
        self.assertIn("NVIDIA GeForce RTX 3090", values)
        self.assertIn("NVIDIA GeForce RTX 4090", values)
        self.assertEqual(values.count("NVIDIA GeForce RTX 4090"), 1)

    def test_normalized_output_flattens_nested_handler_artifacts(self) -> None:
        normalized = self.module._normalized_output(
            {
                "ok": True,
                "runtime_seconds": 12.0,
                "result": {
                    "text": "hello",
                    "diarization_requested": True,
                    "diarization_error": "",
                    "diarization_model": "pyannote/speaker-diarization-3.1",
                    "cache_hit": True,
                    "image_contract_marker_present": True,
                    "gpu_probe": {"exit_code": 0, "stdout": "NVIDIA"},
                },
                "metrics": {"cache_hit": True},
                "verify": {"ok": True},
                "probe_info": {"cache_hit": True},
            }
        )

        self.assertTrue(normalized["ok"])
        self.assertTrue(normalized["workspace_contract_ok"])
        self.assertEqual(normalized["model"], "pyannote/speaker-diarization-3.1")
        self.assertTrue(normalized["hf_token_present"])
        self.assertTrue(normalized["cache_hit"])
        self.assertEqual(normalized["gpu_probe"]["exit_code"], 0)

    def test_audio_base64_reads_explicit_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"RIFF" + b"\x00" * 8)

            encoded = self.module._audio_base64(str(audio))

        self.assertTrue(isinstance(encoded, str) and encoded)

    def test_resolve_existing_endpoint_accepts_name(self) -> None:
        provider = mock.Mock()
        provider._api_snapshot.return_value = {
            "endpoints": [
                {"id": "ep-1", "name": "gpu-job-disabled", "templateId": "tpl-1"},
            ]
        }

        resolved = self.module._resolve_existing_endpoint(
            provider,
            endpoint_id="",
            endpoint_name="gpu-job-disabled",
            template_id="",
        )

        self.assertEqual("ep-1", resolved["id"])
        self.assertEqual("tpl-1", resolved["templateId"])

    def test_resolve_existing_endpoint_rejects_ambiguous_name(self) -> None:
        provider = mock.Mock()
        provider._api_snapshot.return_value = {
            "endpoints": [
                {"id": "ep-1", "name": "same-name"},
                {"id": "ep-2", "name": "same-name"},
            ]
        }

        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            self.module._resolve_existing_endpoint(
                provider,
                endpoint_id="",
                endpoint_name="same-name",
                template_id="",
            )

    def test_read_existing_template_uses_rest_surface(self) -> None:
        with mock.patch.object(
            self.module,
            "_runpod_rest_request",
            return_value={"id": "tpl-1", "imageName": "runpod/worker:latest"},
        ) as rest_request:
            resolved = self.module._read_existing_template(api_key="token", template_id="tpl-1")

        self.assertEqual("runpod/worker:latest", resolved["imageName"])
        rest_request.assert_called_once_with(
            api_key="token",
            path="/templates/tpl-1",
            method="GET",
            payload=None,
        )

    def test_prepare_managed_template_uses_existing_public_template(self) -> None:
        args = self.module._parse_args.__globals__["argparse"].Namespace(
            managed_template_id="tpl-public",
            managed_template_label="official-faster-whisper",
            managed_create_surface="rest",
        )
        plan = {"template": {"imageName": "ignored", "name": "gpu-job-asr"}}

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            self.module,
            "_read_existing_template",
            return_value={"id": "tpl-public", "name": "official", "imageName": "runpod/ai-api-faster-whisper:0.4.1"},
        ):
            template, template_id, provenance = self.module._prepare_managed_template(
                api_key="token",
                args=args,
                plan=plan,
                artifact_dir=Path(tmp),
            )

        self.assertEqual("tpl-public", template_id)
        self.assertEqual("tpl-public", template["id"])
        self.assertEqual("runpod/ai-api-faster-whisper:0.4.1", template["imageName"])
        self.assertEqual("managed_existing_template", provenance["mode"])
        self.assertEqual("official-faster-whisper", provenance["template_label"])

    def test_prepare_managed_template_tolerates_unreadable_public_template(self) -> None:
        args = self.module._parse_args.__globals__["argparse"].Namespace(
            managed_template_id="tpl-public",
            managed_template_label="official-faster-whisper",
            managed_template_image="runpod/ai-api-faster-whisper:0.4.1",
            managed_create_surface="rest",
        )
        plan = {"template": {"imageName": "fallback/image", "name": "gpu-job-asr"}}

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            self.module,
            "_read_existing_template",
            return_value={},
        ):
            template, template_id, provenance = self.module._prepare_managed_template(
                api_key="token",
                args=args,
                plan=plan,
                artifact_dir=Path(tmp),
            )

        self.assertEqual("tpl-public", template_id)
        self.assertEqual("runpod/ai-api-faster-whisper:0.4.1", template["imageName"])
        self.assertEqual("operator_supplied_template_id", provenance["source"])

    def test_apply_managed_template_endpoint_defaults_prefers_template_config(self) -> None:
        endpoint = {
            "gpuIds": "AMPERE_16,AMPERE_24,ADA_24",
            "gpuCount": 1,
            "idleTimeout": 10,
            "scalerType": "QUEUE_DELAY",
            "scalerValue": 4,
        }
        template = {
            "id": "bem34sz6ol",
            "imageName": "runpod/ai-api-faster-whisper:0.4.1",
            "template_record": {
                "config": {
                    "gpuIds": "AMPERE_24",
                    "gpuCount": 1,
                    "idleTimeout": 5,
                    "scalerType": "QUEUE_DELAY",
                    "scalerValue": 4,
                }
            },
        }

        updated = self.module._apply_managed_template_endpoint_defaults(endpoint, template)

        self.assertEqual("AMPERE_24", updated["gpuIds"])
        self.assertEqual(5, updated["idleTimeout"])

    def test_endpoint_resolution_snapshot_is_redacted_and_stable(self) -> None:
        provider = mock.Mock()
        provider._api_snapshot.return_value = {
            "endpoints": [
                {
                    "id": "ep-1",
                    "name": "gpu-job-disabled",
                    "templateId": "tpl-1",
                    "workersMax": 0,
                    "workersMin": 0,
                    "workersStandby": 0,
                    "secret": "do-not-emit",
                }
            ]
        }

        snapshot = self.module._endpoint_resolution_snapshot(provider)

        self.assertEqual(1, snapshot["count"])
        self.assertEqual("ep-1", snapshot["endpoints"][0]["id"])
        self.assertNotIn("secret", snapshot["endpoints"][0])

    def test_official_template_smoke_ok_requires_completed_status(self) -> None:
        self.assertTrue(self.module._official_template_smoke_ok({"ok": True, "status": "COMPLETED"}))
        self.assertFalse(self.module._official_template_smoke_ok({"ok": True, "status": "IN_QUEUE"}))

    def test_probe_name_for_contract_distinguishes_official_smoke(self) -> None:
        self.assertEqual(
            "runpod.asr.official_whisper_smoke",
            self.module._probe_name_for_contract("official_template_smoke"),
        )
        self.assertEqual(
            "runpod.asr_diarization.serverless_handler",
            self.module._probe_name_for_contract("custom_handler"),
        )


if __name__ == "__main__":
    unittest.main()
