from __future__ import annotations

from typing import Any
from urllib import error, request
from urllib.parse import urlencode
import json
import time


class PublicApiError(RuntimeError):
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self.payload = payload
        super().__init__(str(payload.get("error") or payload.get("class") or f"HTTP {status_code}"))


class PublicClient:
    def __init__(self, base_url: str, *, token: str = "", timeout_seconds: int = 30, max_retries: int = 0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def validate(self, payload: dict[str, Any], *, provider: str = "") -> dict[str, Any]:
        return self._post(_query_path("/validate", provider=provider), payload)

    def route(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/route", payload)

    def plan(self, payload: dict[str, Any], *, provider: str = "") -> dict[str, Any]:
        return self._post(_query_path("/plan", provider=provider), payload)

    def submit(self, payload: dict[str, Any], *, execute: bool = False, provider: str = "") -> dict[str, Any]:
        return self._post(_query_path("/submit", execute=1 if execute else 0, provider=provider), payload)

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
        return self._open_json(req)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        req = request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode(),
            headers=self._headers(),
            method="POST",
        )
        return self._open_json(req)

    def _open_json(self, req: request.Request) -> dict[str, Any]:
        last_error: PublicApiError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with request.urlopen(req, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode())
            except error.HTTPError as exc:
                payload = _decode_error_payload(exc)
                api_error = PublicApiError(exc.code, payload)
                if exc.code not in {429, 502, 503, 504} or attempt >= self.max_retries:
                    raise api_error from exc
                last_error = api_error
                time.sleep(_retry_delay_seconds(exc, attempt))
        if last_error is not None:
            raise last_error
        raise RuntimeError("unreachable public client retry state")


def _decode_error_payload(exc: error.HTTPError) -> dict[str, Any]:
    raw = exc.read().decode(errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"ok": False, "error": raw or exc.reason}
    if not isinstance(payload, dict):
        payload = {"ok": False, "error": str(payload)}
    payload.setdefault("status_code", exc.code)
    return payload


def _query_path(path: str, **params: Any) -> str:
    clean = {key: value for key, value in params.items() if value not in ("", None)}
    if not clean:
        return path
    return f"{path}?{urlencode(clean)}"


def _retry_delay_seconds(exc: error.HTTPError, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    return float(min(2**attempt, 5))
