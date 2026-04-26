from __future__ import annotations

from urllib.error import HTTPError
from email.message import Message
from io import BytesIO

from gpu_job.public_client import PublicApiError, PublicClient, _decode_error_payload, _query_path, _retry_delay_seconds


def test_public_client_binds_expected_paths() -> None:
    client = PublicClient("http://127.0.0.1:8765", token="secret", timeout_seconds=5, max_retries=1)
    assert client.base_url == "http://127.0.0.1:8765"
    assert client.timeout_seconds == 5
    assert client.max_retries == 1
    assert client._headers()["Authorization"] == "Bearer secret"


def test_public_client_binds_provider_as_transport_query() -> None:
    calls = []

    class CapturingClient(PublicClient):
        def _post(self, path, payload):  # type: ignore[no-untyped-def]
            calls.append((path, payload))
            return {"ok": True, "path": path}

    client = CapturingClient("http://127.0.0.1:8765")
    payload = {"contract_version": "gpu-job-caller-request-v1"}

    assert client.validate(payload, provider="ollama")["path"] == "/validate?provider=ollama"
    assert client.plan(payload, provider="ollama")["path"] == "/plan?provider=ollama"
    assert client.submit(payload, execute=True, provider="ollama")["path"] == "/submit?execute=1&provider=ollama"
    assert all(sent is payload for _, sent in calls)


def test_query_path_omits_empty_provider() -> None:
    assert _query_path("/submit", execute=0, provider="") == "/submit?execute=0"


def test_public_client_decodes_http_error_payload() -> None:
    exc = HTTPError(
        "http://127.0.0.1:8765/submit",
        429,
        "Too Many Requests",
        {},
        BytesIO(b'{"ok": false, "error": "backpressure", "class": "backpressure"}'),
    )
    payload = _decode_error_payload(exc)
    assert payload["status_code"] == 429
    assert payload["class"] == "backpressure"


def test_public_api_error_exposes_status_and_payload() -> None:
    err = PublicApiError(409, {"ok": False, "error": "quota_block"})
    assert err.status_code == 409
    assert err.payload["error"] == "quota_block"
    assert "quota_block" in str(err)


def test_public_client_respects_retry_after_header() -> None:
    headers = Message()
    headers["Retry-After"] = "3"
    exc = HTTPError("http://127.0.0.1:8765/submit", 429, "Too Many Requests", headers, BytesIO(b"{}"))
    assert _retry_delay_seconds(exc, 0) == 3.0
