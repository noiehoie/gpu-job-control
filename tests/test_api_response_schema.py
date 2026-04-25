from __future__ import annotations

import json
import os
import threading
import unittest
import urllib.request
import urllib.error
from tempfile import TemporaryDirectory
from http.server import ThreadingHTTPServer

from gpu_job import api
from gpu_job.models import Job
from gpu_job.store import JobStore


def make_job() -> Job:
    return Job(
        job_id="response-schema-test",
        job_type="llm_heavy",
        input_uri="text://hello",
        output_uri="local://out",
        worker_image="auto",
        gpu_profile="llm_heavy",
        provider="ollama",
        status="succeeded",
        runtime_seconds=3,
        artifact_count=5,
        metadata={"selected_provider": "ollama"},
    )


class ApiResponseSchemaTest(unittest.TestCase):
    def test_contract_schema_endpoints_are_public_and_stable(self) -> None:
        old_allow = os.environ.get("GPU_JOB_ALLOW_UNAUTHENTICATED")
        old_token = os.environ.get("GPU_JOB_API_TOKEN")
        os.environ["GPU_JOB_ALLOW_UNAUTHENTICATED"] = "1"
        os.environ.pop("GPU_JOB_API_TOKEN", None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), api.GPUJobHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            checks = {
                "/schemas/plan-quote": ("quote_version", "gpu-job-plan-quote-v1"),
                "/schemas/execution-record": ("execution_record_version", "gpu-job-execution-record-v1"),
                "/schemas/provider-workspace": ("workspace_registry_version", "gpu-job-provider-workspace-registry-v1"),
                "/schemas/contracts": ("contract_version", "gpu-job-contract-v1"),
                "/schemas/provider-module": ("provider_module_contract_version", "gpu-job-provider-module-contract-v1"),
                "/schemas/provider-contract-probe": ("contract_probe_version", "gpu-job-provider-contract-probe-v1"),
                "/schemas/caller-request": ("schema_bundle_version", "gpu-job-caller-schema-bundle-v1"),
            }
            for path, (key, expected) in checks.items():
                with self.subTest(path=path):
                    with urllib.request.urlopen(base + path, timeout=5) as response:
                        payload = json.loads(response.read().decode())
                    self.assertEqual(response.status, 200)
                    self.assertEqual(payload[key], expected)
                    if path == "/schemas/provider-module":
                        self.assertEqual(payload["provider_module_routing_flag"]["current_allowed_values"], [False])
                    if path == "/schemas/provider-contract-probe":
                        self.assertIn("provider_module_canary_evidence", payload["required_top_level_fields"])
                    if path == "/schemas/caller-request":
                        self.assertEqual(payload["properties"]["contract_version"]["const"], "gpu-job-caller-request-v1")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            if old_allow is None:
                os.environ.pop("GPU_JOB_ALLOW_UNAUTHENTICATED", None)
            else:
                os.environ["GPU_JOB_ALLOW_UNAUTHENTICATED"] = old_allow
            if old_token is None:
                os.environ.pop("GPU_JOB_API_TOKEN", None)
            else:
                os.environ["GPU_JOB_API_TOKEN"] = old_token

    def test_public_schemas_expose_requires_action_contract(self) -> None:
        old_allow = os.environ.get("GPU_JOB_ALLOW_UNAUTHENTICATED")
        old_token = os.environ.get("GPU_JOB_API_TOKEN")
        os.environ["GPU_JOB_ALLOW_UNAUTHENTICATED"] = "1"
        os.environ.pop("GPU_JOB_API_TOKEN", None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), api.GPUJobHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with urllib.request.urlopen(base + "/schemas/plan-quote", timeout=5) as response:
                plan_quote = json.loads(response.read().decode())
            with urllib.request.urlopen(base + "/schemas/provider-workspace", timeout=5) as response:
                workspace = json.loads(response.read().decode())

            self.assertIn("requires_action", plan_quote["approval_decisions"])
            self.assertEqual(
                plan_quote["requires_action"]["execution_rule"], "can_run_now must be false when approval.decision is requires_action"
            )
            self.assertIn("requires_action", workspace["decisions"])
            self.assertIn("build_image", workspace["required_action_types"])
            self.assertIn("run_contract_probe", workspace["required_action_types"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            if old_allow is None:
                os.environ.pop("GPU_JOB_ALLOW_UNAUTHENTICATED", None)
            else:
                os.environ["GPU_JOB_ALLOW_UNAUTHENTICATED"] = old_allow
            if old_token is None:
                os.environ.pop("GPU_JOB_API_TOKEN", None)
            else:
                os.environ["GPU_JOB_API_TOKEN"] = old_token

    def test_active_catalog_and_workflow_admin_endpoints_are_not_public_api(self) -> None:
        old_allow = os.environ.get("GPU_JOB_ALLOW_UNAUTHENTICATED")
        old_token = os.environ.get("GPU_JOB_API_TOKEN")
        os.environ["GPU_JOB_ALLOW_UNAUTHENTICATED"] = "1"
        os.environ.pop("GPU_JOB_API_TOKEN", None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), api.GPUJobHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            for path in ("/catalog/probe", "/catalog/contract-probe", "/workflows/advance"):
                with self.subTest(path=path):
                    request = urllib.request.Request(
                        base + path,
                        data=json.dumps({}).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(request, timeout=5)
                    self.assertEqual(caught.exception.code, 404)
                    payload = json.loads(caught.exception.read().decode())
                    self.assertEqual(payload["error"], f"unknown endpoint: {path}")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            if old_allow is None:
                os.environ.pop("GPU_JOB_ALLOW_UNAUTHENTICATED", None)
            else:
                os.environ["GPU_JOB_ALLOW_UNAUTHENTICATED"] = old_allow
            if old_token is None:
                os.environ.pop("GPU_JOB_API_TOKEN", None)
            else:
                os.environ["GPU_JOB_API_TOKEN"] = old_token

    def test_operation_catalog_endpoint_is_public_and_closed(self) -> None:
        old_allow = os.environ.get("GPU_JOB_ALLOW_UNAUTHENTICATED")
        old_token = os.environ.get("GPU_JOB_API_TOKEN")
        os.environ["GPU_JOB_ALLOW_UNAUTHENTICATED"] = "1"
        os.environ.pop("GPU_JOB_API_TOKEN", None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), api.GPUJobHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with urllib.request.urlopen(base + "/catalog/operations", timeout=5) as response:
                payload = json.loads(response.read().decode())
            self.assertEqual(response.status, 200)
            self.assertTrue(payload["ok"])
            self.assertFalse(payload["free_form_job_type_allowed"])
            self.assertIn("llm.generate", payload["operations"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            if old_allow is None:
                os.environ.pop("GPU_JOB_ALLOW_UNAUTHENTICATED", None)
            else:
                os.environ["GPU_JOB_ALLOW_UNAUTHENTICATED"] = old_allow
            if old_token is None:
                os.environ.pop("GPU_JOB_API_TOKEN", None)
            else:
                os.environ["GPU_JOB_API_TOKEN"] = old_token

    def test_caller_prompt_catalog_endpoint_is_public(self) -> None:
        old_allow = os.environ.get("GPU_JOB_ALLOW_UNAUTHENTICATED")
        old_token = os.environ.get("GPU_JOB_API_TOKEN")
        os.environ["GPU_JOB_ALLOW_UNAUTHENTICATED"] = "1"
        os.environ.pop("GPU_JOB_API_TOKEN", None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), api.GPUJobHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with urllib.request.urlopen(base + "/catalog/caller-prompt", timeout=5) as response:
                payload = json.loads(response.read().decode())
            self.assertEqual(response.status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["current_prompt_version"], "generic-system-integration-prompt-v1")
            self.assertTrue(payload["sha256"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            if old_allow is None:
                os.environ.pop("GPU_JOB_ALLOW_UNAUTHENTICATED", None)
            else:
                os.environ["GPU_JOB_ALLOW_UNAUTHENTICATED"] = old_allow
            if old_token is None:
                os.environ.pop("GPU_JOB_API_TOKEN", None)
            else:
                os.environ["GPU_JOB_API_TOKEN"] = old_token

    def test_validate_endpoint_accepts_caller_request_shape(self) -> None:
        old_allow = os.environ.get("GPU_JOB_ALLOW_UNAUTHENTICATED")
        old_token = os.environ.get("GPU_JOB_API_TOKEN")
        os.environ["GPU_JOB_ALLOW_UNAUTHENTICATED"] = "1"
        os.environ.pop("GPU_JOB_API_TOKEN", None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), api.GPUJobHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            payload = {
                "contract_version": "gpu-job-caller-request-v1",
                "operation": "llm.generate",
                "input": {"uri": "text://Return exactly: ok", "parameters": {"prompt": "Return exactly: ok"}},
                "output_expectation": {
                    "target_uri": "local://caller-validate",
                    "required_files": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
                },
                "limits": {"max_runtime_minutes": 5, "max_cost_usd": 1, "max_output_gb": 1},
                "idempotency": {"key": "api-caller-validate-001"},
                "caller": {
                    "system": "api-test",
                    "operation": "smoke",
                    "request_id": "api-caller-validate-001",
                    "version": "2026.04.25",
                },
            }
            request = urllib.request.Request(
                base + "/validate",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                result = json.loads(response.read().decode())
            self.assertEqual(response.status, 200)
            self.assertTrue(result["ok"])
            self.assertEqual(result["job"]["job_type"], "llm_heavy")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            if old_allow is None:
                os.environ.pop("GPU_JOB_ALLOW_UNAUTHENTICATED", None)
            else:
                os.environ["GPU_JOB_ALLOW_UNAUTHENTICATED"] = old_allow
            if old_token is None:
                os.environ.pop("GPU_JOB_API_TOKEN", None)
            else:
                os.environ["GPU_JOB_API_TOKEN"] = old_token

    def test_public_api_golden_validate_response_shape(self) -> None:
        old_allow = os.environ.get("GPU_JOB_ALLOW_UNAUTHENTICATED")
        old_token = os.environ.get("GPU_JOB_API_TOKEN")
        os.environ["GPU_JOB_ALLOW_UNAUTHENTICATED"] = "1"
        os.environ.pop("GPU_JOB_API_TOKEN", None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), api.GPUJobHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            payload = {
                "contract_version": "gpu-job-caller-request-v1",
                "operation": "llm.generate",
                "input": {"uri": "text://Return exactly: ok", "parameters": {"prompt": "Return exactly: ok"}},
                "output_expectation": {
                    "target_uri": "local://caller-validate",
                    "required_files": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
                },
                "limits": {"max_runtime_minutes": 5, "max_cost_usd": 1, "max_output_gb": 1},
                "idempotency": {"key": "api-caller-golden-001"},
                "caller": {
                    "system": "api-test",
                    "operation": "smoke",
                    "request_id": "api-caller-golden-001",
                    "version": "2026.04.25",
                },
            }
            request = urllib.request.Request(
                base + "/validate",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                result = json.loads(response.read().decode())
            self.assertEqual(response.status, 200)
            self.assertEqual(sorted(result.keys()), ["job", "ok", "providers"])
            self.assertTrue(result["ok"])
            self.assertEqual(result["job"]["job_type"], "llm_heavy")
            self.assertEqual(result["job"]["metadata"]["caller_request_id"], "api-caller-golden-001")
            self.assertIn("modal", result["providers"])
            self.assertIn("runpod", result["providers"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            if old_allow is None:
                os.environ.pop("GPU_JOB_ALLOW_UNAUTHENTICATED", None)
            else:
                os.environ["GPU_JOB_ALLOW_UNAUTHENTICATED"] = old_allow
            if old_token is None:
                os.environ.pop("GPU_JOB_API_TOKEN", None)
            else:
                os.environ["GPU_JOB_API_TOKEN"] = old_token

    def test_job_response_exposes_stable_artifact_fields(self) -> None:
        old_data_home = os.environ.get("XDG_DATA_HOME")
        with TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            store = JobStore()
            job = make_job()
            store.save(job)
            artifact_dir = store.artifact_dir(job.job_id)
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "result.json").write_text(json.dumps({"provider": "ollama", "text": "hello"}) + "\n")
            (artifact_dir / "metrics.json").write_text(json.dumps({"provider": "ollama", "runtime_seconds": 1.25}) + "\n")
            (artifact_dir / "verify.json").write_text(json.dumps({"ok": True, "missing": []}) + "\n")

            response = api._job_response(job)
            self.assertEqual(response["provider"], "ollama")
            self.assertEqual(response["selected_provider"], "ollama")
            self.assertEqual(response["result_text"], "hello")
            self.assertEqual(response["metrics_runtime_seconds"], 1.25)
            self.assertTrue(response["verify_ok"])
            self.assertEqual(response["result"]["text"], "hello")
            self.assertEqual(response["metrics"]["runtime_seconds"], 1.25)
            self.assertTrue(response["verify_result"]["ok"])
        if old_data_home is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_data_home

    def test_submit_response_exposes_top_level_provider(self) -> None:
        old_data_home = os.environ.get("XDG_DATA_HOME")
        with TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            store = JobStore()
            job = make_job()
            store.save(job)
            artifact_dir = store.artifact_dir(job.job_id)
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "result.json").write_text(json.dumps({"provider": "ollama", "text": "hello"}) + "\n")
            (artifact_dir / "metrics.json").write_text(json.dumps({"provider": "ollama", "runtime_seconds": 1.25}) + "\n")
            (artifact_dir / "verify.json").write_text(json.dumps({"ok": True, "missing": []}) + "\n")

            response = api._submit_response({"ok": True, "job": job.to_dict()})
            self.assertEqual(response["job_id"], job.job_id)
            self.assertEqual(response["provider"], "ollama")
            self.assertEqual(response["selected_provider"], "ollama")
            self.assertEqual(response["result_text"], "hello")
            self.assertEqual(response["metrics_runtime_seconds"], 1.25)
            self.assertTrue(response["verify_ok"])
        if old_data_home is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_data_home


if __name__ == "__main__":
    unittest.main()
