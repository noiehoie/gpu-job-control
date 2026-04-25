from __future__ import annotations

from typing import Any
from urllib import request
import json


class PublicClient:
    def __init__(self, base_url: str, *, token: str = "", timeout_seconds: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/validate", payload)

    def route(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/route", payload)

    def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/plan", payload)

    def submit(self, payload: dict[str, Any], *, execute: bool = False) -> dict[str, Any]:
        return self._post(f"/submit?execute={1 if execute else 0}", payload)

    def status(self, job_id: str) -> dict[str, Any]:
        return self._get(f"/jobs/{job_id}")

    def verify(self, job_id: str) -> dict[str, Any]:
        return self._get(f"/verify/{job_id}")

    def caller_schema(self) -> dict[str, Any]:
        return self._get("/schemas/caller-request")

    def operation_catalog(self) -> dict[str, Any]:
        return self._get("/catalog/operations")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get(self, path: str) -> dict[str, Any]:
        req = request.Request(self.base_url + path, headers=self._headers(), method="GET")
        with request.urlopen(req, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode())

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        req = request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode(),
            headers=self._headers(),
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode())
