from __future__ import annotations

from io import BytesIO
from tempfile import TemporaryDirectory
import os
import unittest

from gpu_job import api
from gpu_job.authz import authorize


class DummyHandler:
    def __init__(self, headers: dict[str, str], body: bytes = b"") -> None:
        self.headers = headers
        self.rfile = BytesIO(body)


class SecurityDefaultsTest(unittest.TestCase):
    def test_api_auth_fails_closed_without_token(self) -> None:
        old_token = os.environ.pop("GPU_JOB_API_TOKEN", None)
        old_allow = os.environ.pop("GPU_JOB_ALLOW_UNAUTHENTICATED", None)
        try:
            self.assertFalse(api._authorized(DummyHandler({})))  # noqa: SLF001
        finally:
            if old_token is not None:
                os.environ["GPU_JOB_API_TOKEN"] = old_token
            if old_allow is not None:
                os.environ["GPU_JOB_ALLOW_UNAUTHENTICATED"] = old_allow

    def test_unknown_principal_is_anonymous(self) -> None:
        result = authorize("random-user", "submit")
        self.assertFalse(result["ok"])
        self.assertEqual(result["role"], "anonymous")

    def test_json_body_limit_is_enforced_before_read(self) -> None:
        too_large = str(api.MAX_JSON_BODY_BYTES + 1)  # noqa: SLF001
        with self.assertRaises(ValueError):
            api._read_json(DummyHandler({"Content-Length": too_large}, b"{}"))  # noqa: SLF001

    def test_verify_path_cannot_escape_artifact_store(self) -> None:
        old_xdg = os.environ.get("XDG_DATA_HOME")
        with TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            with self.assertRaises(ValueError):
                api._artifact_dir_from_query("/etc")  # noqa: SLF001
        if old_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_xdg

    def test_unsafe_job_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            api._safe_id("../etc/passwd", field="job_id")  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
