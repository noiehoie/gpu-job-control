from __future__ import annotations

from gpu_job.public_client import PublicClient


def test_public_client_binds_expected_paths() -> None:
    client = PublicClient("http://127.0.0.1:8765", token="secret", timeout_seconds=5)
    assert client.base_url == "http://127.0.0.1:8765"
    assert client.timeout_seconds == 5
    assert client._headers()["Authorization"] == "Bearer secret"
