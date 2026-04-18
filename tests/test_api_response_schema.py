from __future__ import annotations

import json
import os
import unittest
from tempfile import TemporaryDirectory

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
