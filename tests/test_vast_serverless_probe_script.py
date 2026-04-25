from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _load_script_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "vast-pyworker-serverless-contract-probe.py"
    spec = importlib.util.spec_from_file_location("vast_pyworker_serverless_contract_probe", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load script module: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VastServerlessProbeScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_script_module()

    def test_parse_args_allows_existing_resource_mode_without_template_flags(self) -> None:
        with mock.patch("sys.argv", ["probe", "--existing-endpoint-id", "21064", "--existing-workergroup-id", "27597"]):
            args = self.module._parse_args()

        self.assertEqual("21064", args.existing_endpoint_id)
        self.assertEqual("27597", args.existing_workergroup_id)

    def test_resolve_template_record_prefers_workergroup_template_hash(self) -> None:
        args = self.module.argparse.Namespace(
            template_hash="",
            template_id="",
            discover_template_query="",
            discover_template_limit=20,
            discover_template_image_substring="",
            discover_template_bootstrap_substring="",
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            self.module,
            "_lookup_template",
            return_value={"hash_id": "hash-1", "image": "vastai/vllm", "tag": "0.8.4"},
        ):
            record, resolution = self.module._resolve_template_record(
                args=args,
                workergroup_record={"template_hash": "hash-1", "template_id": "322483"},
                artifact_dir=Path(tmp),
            )

        self.assertEqual("hash-1", record["hash_id"])
        self.assertEqual("workergroup_record", resolution["source"])
        self.assertEqual("322483", resolution["template_id"])

    def test_discover_templates_filters_image_and_bootstrap(self) -> None:
        payload = {
            "json": [
                {"id": 1, "hash_id": "a", "image": "vastai/base-image", "bootstrap_script": ""},
                {
                    "id": 2,
                    "hash_id": "b",
                    "image": "vastai/vllm",
                    "bootstrap_script": "https://raw.githubusercontent.com/vast-ai/pyworker/main/start_server.sh",
                },
            ]
        }
        with mock.patch.object(self.module, "_run_vast", return_value=payload):
            rows = self.module._discover_templates(
                query="creator_id == 62897",
                limit=10,
                image_substring="vastai/vllm",
                bootstrap_substring="pyworker",
            )

        self.assertEqual(1, len(rows))
        self.assertEqual("b", rows[0]["hash_id"])

    def test_template_candidate_sort_prefers_gpu_ram_and_fixed_tag(self) -> None:
        older = {
            "id": 321529,
            "hash_id": "old",
            "name": "vLLM (Serverless)",
            "image": "vastai/vllm",
            "tag": "v0.11.0-cuda-12.8-mvc-cuda-12.0",
            "created_at": 1767006399.0,
            "onstart": "curl https://raw.githubusercontent.com/vast-ai/pyworker/main/start_server.sh | bash",
            "extra_filters": "{\"inet_down\":{\"gt\":500}}",
        }
        newer_better = {
            "id": 322483,
            "hash_id": "better",
            "name": "vLLM (Serverless)",
            "image": "vastai/vllm",
            "tag": "@vastai-automatic-tag",
            "created_at": 1767205458.0,
            "onstart": "curl https://raw.githubusercontent.com/vast-ai/pyworker/main/start_server.sh | bash",
            "extra_filters": "{\"gpu_ram\":{\"gte\":16000}}",
        }

        rows = sorted([older, newer_better], key=self.module._template_candidate_sort_key, reverse=True)

        self.assertEqual("better", rows[0]["hash_id"])


if __name__ == "__main__":
    unittest.main()
