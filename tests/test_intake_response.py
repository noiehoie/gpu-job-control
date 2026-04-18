from __future__ import annotations

import os
import unittest
from tempfile import TemporaryDirectory

from gpu_job.intake import intake_job
from gpu_job.models import Job


def make_job() -> Job:
    return Job(
        job_id="intake-response-test",
        job_type="llm_heavy",
        input_uri="text://hello",
        output_uri="local://out",
        worker_image="auto",
        gpu_profile="llm_heavy",
        metadata={"source_system": "test", "task_family": "response_contract"},
    )


class IntakeResponseTest(unittest.TestCase):
    def test_intake_response_exposes_top_level_job_id(self) -> None:
        old_data_home = os.environ.get("XDG_DATA_HOME")
        with TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            result = intake_job(make_job(), provider_name="auto")
            self.assertTrue(result["ok"])
            self.assertEqual(result["job_id"], "intake-response-test")
            self.assertEqual(result["status"], "buffered")
            self.assertEqual(result["intake_state"], "buffered")
            self.assertEqual(result["group_key"], "test|llm_heavy|llm_heavy|response_contract")
            self.assertEqual(result["job"]["job_id"], "intake-response-test")
        if old_data_home is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_data_home


if __name__ == "__main__":
    unittest.main()
