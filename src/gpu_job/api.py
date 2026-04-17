from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
import argparse
import json
import os
import re
import secrets

from .guard import collect_cost_guard
from .audit import verify_audit_chain
from .authz import approval_ok, approval_record, authorize, list_approvals, save_approval
from .capabilities import evaluate_model_capability, load_capabilities
from .circuit import all_circuits
from .cost import cost_estimate
from .decision import load_decision, replay_all_decisions, replay_decision
from .destructive import destructive_preflight
from .dlq import dlq_status
from .drain import clear_drain, drain_status, start_drain
from .error_class import classify_error
from .intake import intake_job, intake_status, plan_intake_groups
from .invariants import evaluate_invariants
from .metrics_export import metrics_prometheus, metrics_snapshot
from .models import Job
from .policy_engine import policy_activation_record
from .placement import placement_check
from .preemption import preemption_check
from .provenance import expected_attestation_hash
from .providers import PROVIDERS, get_provider
from .quota import quota_check
from .queue import cancel_group, cancel_job, enqueue_job, queue_status, replan_queued_jobs, retry_job
from .readiness import launch_readiness
from .remediation import remediation_decision
from .reconcile import reconcile_detect_only
from .retention import retention_report
from .router import route_job
from .runner import submit_job
from .secrets_policy import secret_check
from .selftest import run_selftest
from .stats import collect_stats
from .store import JobStore
from .timeout import timeout_contract
from .verify import verify_artifacts
from .wal import wal_recovery_plan, wal_recovery_status, wal_status
from .workflow import load_workflow, save_workflow

MAX_JSON_BODY_BYTES = int(os.getenv("GPU_JOB_MAX_JSON_BODY_BYTES", str(10 * 1024 * 1024)))
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _json_response(
    handler: BaseHTTPRequestHandler,
    status: int,
    data: object,
    headers: dict[str, str] | None = None,
) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    _cors_headers(handler)
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(payload)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _first(qs: dict[str, list[str]], key: str, default: str = "") -> str:
    values = qs.get(key)
    return values[0] if values else default


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    if length > MAX_JSON_BODY_BYTES:
        raise ValueError(f"json body too large: {length} > {MAX_JSON_BODY_BYTES}")
    raw = handler.rfile.read(length)
    return json.loads(raw.decode())


def _auth_token() -> str:
    return os.getenv("GPU_JOB_API_TOKEN", "").strip()


def _requires_auth() -> bool:
    return not _allow_unauthenticated()


def _allow_unauthenticated() -> bool:
    return _truthy(os.getenv("GPU_JOB_ALLOW_UNAUTHENTICATED"))


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    token = _auth_token()
    if not token:
        return _allow_unauthenticated()
    header = handler.headers.get("Authorization", "").strip()
    if header.startswith("Bearer "):
        return secrets.compare_digest(header.removeprefix("Bearer ").strip(), token)
    return secrets.compare_digest(handler.headers.get("X-GPU-Job-Token", "").strip(), token)


def _configured_cors_origins() -> set[str]:
    raw = os.getenv("GPU_JOB_CORS_ORIGINS", "").strip()
    return {item.strip() for item in raw.split(",") if item.strip()}


def _cors_headers(handler: BaseHTTPRequestHandler) -> None:
    origin = handler.headers.get("Origin", "").strip()
    allowed = _configured_cors_origins()
    if origin and origin in allowed:
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Vary", "Origin")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-GPU-Job-Token")


def _safe_id(value: str, *, field: str = "id") -> str:
    value = value.strip()
    if not SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def _artifact_dir_from_query(raw: str) -> Path:
    store = JobStore()
    base = store.artifacts_dir.resolve(strict=False)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base / path
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError("artifact_dir must be inside the artifact store") from exc
    return resolved


def _ensure_auth_token() -> dict[str, Any]:
    if _auth_token():
        return {"auth_required": True, "token_source": "environment"}
    if _allow_unauthenticated():
        return {"auth_required": False, "token_source": "disabled-by-GPU_JOB_ALLOW_UNAUTHENTICATED"}
    token = secrets.token_urlsafe(32)
    os.environ["GPU_JOB_API_TOKEN"] = token
    return {"auth_required": True, "token_source": "generated", "generated_token": token}


def _job_response(job: Job) -> dict[str, Any]:
    store = JobStore()
    data = _public_job_dict(job.to_dict())
    artifact_dir = store.artifact_dir(job.job_id)
    data["artifact_dir"] = str(artifact_dir)
    result_path = artifact_dir / "result.json"
    if result_path.is_file():
        try:
            data["result"] = json.loads(result_path.read_text())
        except json.JSONDecodeError:
            data["result"] = {"error": "result.json is not valid JSON"}
    return data


def _submit_response(result: dict[str, Any]) -> dict[str, Any]:
    out = dict(result)
    job_data = result.get("job")
    if isinstance(job_data, dict):
        out["job"] = _public_job_dict(job_data)
        job_id = str(job_data.get("job_id") or "")
        if job_id:
            out["job_id"] = job_id
            out["artifact_dir"] = str(JobStore().artifact_dir(job_id))
            result_path = JobStore().artifact_dir(job_id) / "result.json"
            if result_path.is_file():
                try:
                    out["result"] = json.loads(result_path.read_text())
                except json.JSONDecodeError:
                    out["result"] = {"error": "result.json is not valid JSON"}
        for key in ("status", "artifact_count", "artifact_bytes", "exit_code", "runtime_seconds", "error"):
            if key in job_data:
                out[key] = job_data[key]
    return out


def _public_job_dict(data: dict[str, Any]) -> dict[str, Any]:
    public = dict(data)
    metadata = public.get("metadata")
    if isinstance(metadata, dict):
        public["metadata"] = _redact_payload(metadata)
    return public


def _redact_payload(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: _redact_payload(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_payload(item, key) for item in value[:20]]
    if isinstance(value, str):
        if key in {"image_base64", "audio_base64", "file_base64"}:
            return f"<omitted {key} chars={len(value)}>"
        if key in {"prompt", "system_prompt"} and len(value) > 1000:
            return f"{value[:1000]}...<truncated {key} chars={len(value)}>"
        if len(value) > 4000:
            return f"{value[:4000]}...<truncated chars={len(value)}>"
    return value


class GPUJobHandler(BaseHTTPRequestHandler):
    server_version = "gpu-job-control/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_OPTIONS(self) -> None:
        origin = self.headers.get("Origin", "").strip()
        if origin and origin not in _configured_cors_origins():
            _json_response(self, 403, {"ok": False, "error": "cors origin not allowed"})
            return
        _json_response(self, 204, {"ok": True})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if not _authorized(self):
                _json_response(self, 401, {"ok": False, "error": "unauthorized"})
                return
            if path in {"/", "/health"}:
                guard = collect_cost_guard()
                doctors = [provider.doctor() for provider in PROVIDERS.values()]
                _json_response(
                    self,
                    200 if guard["ok"] else 503,
                    {
                        "ok": guard["ok"] and all(item.get("ok") for item in doctors),
                        "service": "gpu-job-control",
                        "auth_required": _requires_auth(),
                        "guard": guard,
                        "providers": doctors,
                    },
                )
                return
            if path == "/guard":
                guard = collect_cost_guard()
                _json_response(self, 200 if guard["ok"] else 503, guard)
                return
            if path == "/stats":
                _json_response(self, 200, collect_stats())
                return
            if path == "/metrics":
                _json_response(self, 200, metrics_snapshot())
                return
            if path == "/metrics/prometheus":
                payload = metrics_prometheus().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if path == "/retention":
                _json_response(self, 200, retention_report())
                return
            if path == "/selftest":
                result = run_selftest()
                _json_response(self, 200 if result["ok"] else 500, result)
                return
            if path == "/drain":
                _json_response(self, 200, drain_status())
                return
            if path == "/reconcile":
                _json_response(self, 200, reconcile_detect_only())
                return
            if path == "/readiness":
                qs = parse_qs(parsed.query)
                limit = int(_first(qs, "limit", "100") or "100")
                result = launch_readiness(limit=limit)
                _json_response(self, 200 if result["ok"] else 503, result)
                return
            if path == "/policy":
                _json_response(self, 200, policy_activation_record())
                return
            if path == "/circuits":
                _json_response(self, 200, all_circuits())
                return
            if path == "/capabilities":
                _json_response(self, 200, {"ok": True, "registry": load_capabilities()})
                return
            if path == "/audit/verify":
                _json_response(self, 200, verify_audit_chain())
                return
            if path == "/wal":
                _json_response(self, 200, wal_status())
                return
            if path == "/wal/recovery":
                result = wal_recovery_status()
                _json_response(self, 200 if result["ok"] else 409, result)
                return
            if path == "/wal/recovery-plan":
                result = wal_recovery_plan()
                _json_response(self, 200 if result["ok"] else 409, result)
                return
            if path == "/authz":
                qs = parse_qs(parsed.query)
                principal = _first(qs, "principal")
                action = _first(qs, "action")
                scope = _first(qs, "scope")
                _json_response(self, 200, authorize(principal, action, scope=scope))
                return
            if path == "/approval/check":
                qs = parse_qs(parsed.query)
                result = approval_ok(_first(qs, "action"), _first(qs, "principal"))
                _json_response(self, 200 if result["ok"] else 403, result)
                return
            if path == "/approval":
                qs = parse_qs(parsed.query)
                limit = int(_first(qs, "limit", "100") or "100")
                _json_response(self, 200, list_approvals(limit=limit))
                return
            if path == "/dlq":
                _json_response(self, 200, dlq_status())
                return
            if path == "/error-class":
                qs = parse_qs(parsed.query)
                status_raw = _first(qs, "status_code") or _first(qs, "status")
                status_code = int(status_raw) if status_raw else None
                _json_response(
                    self,
                    200,
                    classify_error(_first(qs, "error"), status_code=status_code, provider=_first(qs, "provider")),
                )
                return
            if path == "/destructive/check":
                qs = parse_qs(parsed.query)
                result = destructive_preflight(
                    _first(qs, "action"),
                    _first(qs, "principal"),
                    target=_first(qs, "target"),
                    scope=_first(qs, "scope"),
                )
                _json_response(self, 200 if result["ok"] else 403, result)
                return
            if path.startswith("/decision/"):
                job_id = _safe_id(path.split("/", 2)[2], field="job_id")
                _json_response(self, 200, load_decision(job_id))
                return
            if path.startswith("/decision-replay/"):
                job_id = _safe_id(path.split("/", 2)[2], field="job_id")
                _json_response(self, 200, replay_decision(job_id))
                return
            if path == "/decision-replay":
                qs = parse_qs(parsed.query)
                limit = int(_first(qs, "limit", "1000") or "1000")
                result = replay_all_decisions(limit=limit)
                _json_response(self, 200 if result["ok"] else 409, result)
                return
            if path.startswith("/workflows/"):
                workflow_id = _safe_id(path.split("/", 2)[2], field="workflow_id")
                _json_response(self, 200, load_workflow(workflow_id))
                return
            if path in {"/queue", "/jobs"}:
                qs = parse_qs(parsed.query)
                limit = int(_first(qs, "limit", "100") or "100")
                _json_response(self, 200, queue_status(limit=limit, compact=True))
                return
            if path == "/intake":
                qs = parse_qs(parsed.query)
                limit = int(_first(qs, "limit", "100") or "100")
                _json_response(self, 200, intake_status(limit=limit, compact=True))
                return
            if path.startswith("/jobs/"):
                job_id = _safe_id(path.split("/", 2)[2], field="job_id")
                job = JobStore().load(job_id)
                _json_response(self, 200, _job_response(job))
                return
            if path == "/verify":
                qs = parse_qs(parsed.query)
                artifact_dir_raw = _first(qs, "artifact_dir") or _first(qs, "path")
                if not artifact_dir_raw:
                    raise ValueError("missing artifact_dir query parameter")
                artifact_dir = _artifact_dir_from_query(artifact_dir_raw)
                _json_response(self, 200, verify_artifacts(artifact_dir))
                return
            if path.startswith("/verify/"):
                job_id = _safe_id(path.split("/", 2)[2], field="job_id")
                artifact_dir = JobStore().artifact_dir(job_id)
                _json_response(self, 200, verify_artifacts(artifact_dir))
                return
            _json_response(self, 404, {"ok": False, "error": f"unknown endpoint: {path}"})
        except Exception as exc:
            _json_response(self, 500, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)
        try:
            if not _authorized(self):
                _json_response(self, 401, {"ok": False, "error": "unauthorized"})
                return
            payload = _read_json(self)
            if path == "/cancel":
                job_id = str(payload.get("job_id") or _first(qs, "job_id", ""))
                if job_id:
                    _json_response(self, 200, cancel_job(_safe_id(job_id, field="job_id")))
                else:
                    _json_response(
                        self,
                        200,
                        cancel_group(
                            source_system=str(payload.get("source_system") or ""),
                            workflow_id=str(payload.get("workflow_id") or ""),
                            task_family=str(payload.get("task_family") or ""),
                        ),
                    )
                return
            if path == "/replan":
                limit = int(payload.get("limit") or _first(qs, "limit", "1000") or "1000")
                _json_response(self, 200, replan_queued_jobs(limit=limit))
                return
            if path == "/retry":
                job_id = str(payload.get("job_id") or _first(qs, "job_id", ""))
                if not job_id:
                    raise ValueError("missing job_id")
                _json_response(self, 200, retry_job(_safe_id(job_id, field="job_id")))
                return
            if path == "/intake/plan":
                _json_response(self, 200, plan_intake_groups())
                return
            if path == "/workflows":
                _json_response(self, 202, save_workflow(payload))
                return
            if path == "/approval":
                action = str(payload.get("action") or "")
                principal = str(payload.get("principal") or "")
                approved = bool(payload.get("approved", False))
                expires_at_raw = payload.get("expires_at")
                expires_at = int(expires_at_raw) if expires_at_raw is not None else None
                reason = str(payload.get("reason") or "")
                result = save_approval(approval_record(action, principal, approved=approved, expires_at=expires_at, reason=reason))
                _json_response(self, 200 if result["ok"] else 400, result)
                return
            if path == "/drain/start":
                _json_response(self, 200, start_drain(str(payload.get("reason") or "")))
                return
            if path == "/drain/clear":
                _json_response(self, 200, clear_drain())
                return
            job_data = payload.get("job", payload)
            job = Job.from_dict(job_data)

            if path == "/validate":
                _json_response(self, 200, {"ok": True, "job": job.to_dict()})
                return
            if path == "/timeout":
                _json_response(self, 200, timeout_contract(job))
                return
            if path == "/attestation":
                _json_response(self, 200, {"ok": True, "subject_sha256": expected_attestation_hash(job)})
                return
            if path == "/route":
                _json_response(self, 200, route_job(job))
                return
            if path == "/invariants":
                provider_name = str(payload.get("provider") or _first(qs, "provider", "auto"))
                result = evaluate_invariants(job, provider_name=provider_name)
                _json_response(self, 200 if result["ok"] else 409, result)
                return
            if path == "/capabilities/check":
                provider_name = str(payload.get("provider") or _first(qs, "provider", ""))
                result = evaluate_model_capability(job, provider=provider_name)
                _json_response(self, 200 if result["ok"] else 409, result)
                return
            if path == "/eval/quota":
                result = quota_check(job)
                _json_response(self, 200 if result["ok"] else 409, result)
                return
            if path == "/eval/cost":
                result = cost_estimate(job)
                _json_response(self, 200 if result["ok"] else 409, result)
                return
            if path == "/eval/secrets":
                provider_name = str(payload.get("provider") or _first(qs, "provider", ""))
                result = secret_check(job, provider_name)
                _json_response(self, 200 if result["ok"] else 409, result)
                return
            if path == "/eval/placement":
                provider_name = str(payload.get("provider") or _first(qs, "provider", ""))
                result = placement_check(job, provider_name)
                _json_response(self, 200 if result["ok"] else 409, result)
                return
            if path == "/eval/preemption":
                result = preemption_check(job)
                _json_response(self, 200 if result["ok"] else 409, result)
                return
            if path == "/remediation":
                _json_response(self, 200, remediation_decision(job))
                return
            if path == "/plan":
                provider_name = str(payload.get("provider") or _first(qs, "provider", "auto"))
                selected = route_job(job)["selected_provider"] if provider_name == "auto" else provider_name
                _json_response(self, 200, get_provider(selected).plan(job))
                return
            if path == "/submit":
                provider_name = str(payload.get("provider") or _first(qs, "provider", "auto"))
                execute = bool(payload.get("execute", False)) or _truthy(_first(qs, "execute", ""))
                result = submit_job(job, provider_name=provider_name, execute=execute)
                status = int(result.get("status_code") or (200 if result.get("ok") else 500))
                if result.get("error") == "pre-submit cost guard failed":
                    status = 409
                headers = {}
                if status == 429:
                    headers["Retry-After"] = str(int(result.get("retry_after_seconds") or 30))
                _json_response(self, status, _submit_response(result), headers=headers)
                return
            if path == "/enqueue":
                provider_name = str(payload.get("provider") or _first(qs, "provider", "auto"))
                _json_response(self, 202, enqueue_job(job, provider_name=provider_name))
                return
            if path == "/intake":
                provider_name = str(payload.get("provider") or _first(qs, "provider", "auto"))
                _json_response(self, 202, intake_job(job, provider_name=provider_name))
                return
            _json_response(self, 404, {"ok": False, "error": f"unknown endpoint: {path}"})
        except ValueError as exc:
            _json_response(self, 400, {"ok": False, "error": str(exc)})
        except json.JSONDecodeError as exc:
            _json_response(self, 400, {"ok": False, "error": f"invalid json: {exc}"})
        except Exception as exc:
            _json_response(self, 500, {"ok": False, "error": str(exc)})


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    auth = _ensure_auth_token()
    httpd = ThreadingHTTPServer((host, port), GPUJobHandler)
    print(json.dumps({"ok": True, "service": "gpu-job-control", "host": host, "port": port, **auth}, ensure_ascii=False))
    httpd.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gpu-job-api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--require-token", action="store_true", help="fail startup when GPU_JOB_API_TOKEN is empty")
    args = parser.parse_args(argv)
    if args.require_token and not _auth_token():
        raise SystemExit("GPU_JOB_API_TOKEN is required")
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
