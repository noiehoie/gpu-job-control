from __future__ import annotations

import json
import os
import unittest
from tempfile import TemporaryDirectory

from gpu_job.models import Job
from gpu_job.providers.local import LocalProvider
from gpu_job.store import JobStore
from gpu_job.workflow_helpers import run_cpu_workflow_helper


class CpuWorkflowHelperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_data_home = os.environ.get("XDG_DATA_HOME")
        self.tmp = TemporaryDirectory()
        os.environ["XDG_DATA_HOME"] = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()
        if self.old_data_home is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self.old_data_home

    def test_page_result_merger_local_provider_artifacts(self) -> None:
        job = Job.from_dict(
            {
                "job_id": "cpu-helper-merge",
                "job_type": "cpu_workflow_helper",
                "input_uri": "workflow://merge/pages",
                "output_uri": "local://merge/pages",
                "worker_image": "local:cpu-workflow-helper",
                "gpu_profile": "cpu",
                "provider": "local",
                "metadata": {
                    "input": {
                        "action": "page_result_merger",
                        "items": [{"page": 1, "text": "alpha"}, {"page": 2, "text": "beta"}],
                    }
                },
            }
        )

        result = LocalProvider().submit(job, JobStore(), execute=True)

        self.assertEqual(result.status, "succeeded")
        artifact_dir = JobStore().artifact_dir(job.job_id)
        payload = json.loads((artifact_dir / "result.json").read_text())
        self.assertEqual(payload["action"], "page_result_merger")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["text"], "alpha\nbeta")

    def test_pdf_page_estimator_uses_token_scan_without_pdf_dependency(self) -> None:
        pdf = os.path.join(self.tmp.name, "sample.pdf")
        with open(pdf, "wb") as fh:
            fh.write(b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n2 0 obj << /Type /Page >> endobj\n%%EOF\n")
        job = Job.from_dict(
            {
                "job_id": "cpu-helper-pdf-estimate",
                "job_type": "cpu_workflow_helper",
                "input_uri": pdf,
                "output_uri": "local://pdf",
                "worker_image": "local:cpu-workflow-helper",
                "gpu_profile": "cpu",
                "metadata": {"input": {"action": "pdf_page_estimator", "path": pdf}},
            }
        )

        result = run_cpu_workflow_helper(job, JobStore().artifact_dir(job.job_id))

        self.assertTrue(result["ok"])
        self.assertEqual(result["page_count"], 2)

    def test_unknown_action_fails_closed(self) -> None:
        job = Job.from_dict(
            {
                "job_id": "cpu-helper-bad-action",
                "job_type": "cpu_workflow_helper",
                "input_uri": "workflow://bad/action",
                "output_uri": "local://bad",
                "worker_image": "local:cpu-workflow-helper",
                "gpu_profile": "cpu",
                "metadata": {"input": {"action": "no_such_plugin"}},
            }
        )

        with self.assertRaises(RuntimeError):
            run_cpu_workflow_helper(job, JobStore().artifact_dir(job.job_id))


if __name__ == "__main__":
    unittest.main()
