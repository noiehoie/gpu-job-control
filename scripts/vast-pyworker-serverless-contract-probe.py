from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from gpu_job.models import app_data_dir, now_unix
from gpu_job.providers.vast import VastProvider


PROBE_NAME = "vast.asr.serverless_template"
SUCCESS_TEXT = "GPU_JOB_ASR_SERVERLESS_CANARY_OK"
DEFAULT_ROUTE_URL = "https://run.vast.ai/route/"


def main() -> int:
    args = _parse_args()
    artifact_dir = Path(args.artifact_dir) if args.artifact_dir else _default_artifact_dir()
    artifact_dir.mkdir(parents=True, exist_ok=True)

    provider = VastProvider()
    _ensure_vast_api_key_env()
    started_at = now_unix()
    pre_guard = provider.cost_guard()
    endpoint_id = ""
    workergroup_id = ""
    workergroup_api_key = ""
    endpoint_name = args.endpoint_name or f"gpu-job-vast-pyworker-{time.strftime('%Y%m%d%H%M%S', time.gmtime())}"
    cleanup: dict[str, Any] = {"ok": False, "steps": []}
    route_attempts: list[dict[str, Any]] = []
    worker_request: dict[str, Any] = {}
    errors: list[str] = []

    endpoint_record: dict[str, Any] = {}
    workergroup_record: dict[str, Any] = {}
    template_record: dict[str, Any] = {}
    template_resolution: dict[str, Any] = {}
    managed_resources = True
    endpoint_logs_data: dict[str, Any] = {}
    workergroup_logs_data: dict[str, Any] = {}
    try:
        if bool(args.existing_endpoint_id) != bool(args.existing_workergroup_id):
            raise RuntimeError("--existing-endpoint-id and --existing-workergroup-id must be provided together")
        if not pre_guard.get("ok"):
            raise RuntimeError(f"Vast pre-guard is not clean: {pre_guard}")
        if args.existing_endpoint_id:
            managed_resources = False
            endpoint_id = str(args.existing_endpoint_id)
            workergroup_id = str(args.existing_workergroup_id)
            if args.existing_endpoint_name:
                endpoint_name = args.existing_endpoint_name
        selected_template_hash = str(args.template_hash or "").strip()
        selected_template_id = str(args.template_id or "").strip()
        if managed_resources and not selected_template_hash and not selected_template_id and args.discover_template_query:
            template_record, template_resolution = _resolve_template_record(
                args=args,
                workergroup_record={},
                artifact_dir=artifact_dir,
            )
            selected_template_hash = str(template_resolution.get("template_hash") or "").strip()
            selected_template_id = str(template_resolution.get("template_id") or "").strip()
            if not selected_template_hash and not selected_template_id:
                raise RuntimeError(f"no Vast template candidates matched discovery query: {args.discover_template_query}")

        endpoints = _run_vast(["vastai", "show", "endpoints", "--raw"])
        _write_json(artifact_dir / "vast_endpoints_snapshot.json", endpoints)
        if managed_resources:
            created_endpoint = _run_vast(
                [
                    "vastai",
                    "create",
                    "endpoint",
                    "--endpoint_name",
                    endpoint_name,
                    "--cold_workers",
                    str(args.cold_workers),
                    "--max_workers",
                    str(args.max_workers),
                    "--min_load",
                    str(args.min_load),
                    "--min_cold_load",
                    str(args.min_cold_load),
                    "--inactivity_timeout",
                    str(args.inactivity_timeout),
                    "--raw",
                ]
            )
            _write_json(artifact_dir / "vast_endpoint_create.json", created_endpoint)
            endpoint_id = _extract_first_id(created_endpoint, key="result")
            if not endpoint_id:
                raise RuntimeError(f"could not parse Vast endpoint id: {created_endpoint}")
            endpoints = _run_vast(["vastai", "show", "endpoints", "--raw"])
            _write_json(artifact_dir / "vast_endpoints_snapshot_after_create.json", endpoints)
        endpoint_record = _find_record(endpoints.get("json"), "id", endpoint_id)
        endpoint_name = str(endpoint_record.get("endpoint_name") or endpoint_record.get("name") or endpoint_name)

        if managed_resources:
            workergroup_cmd = [
                "vastai",
                "create",
                "workergroup",
                "--endpoint_id",
                endpoint_id,
                "--test_workers",
                str(args.test_workers),
                "--cold_workers",
                str(args.cold_workers),
                "--gpu_ram",
                str(args.gpu_ram),
                "--raw",
            ]
            if selected_template_hash:
                workergroup_cmd.extend(["--template_hash", selected_template_hash])
            else:
                workergroup_cmd.extend(["--template_id", selected_template_id])
            if args.search_params:
                workergroup_cmd.extend(["--search_params", args.search_params])
            created_workergroup = _run_vast(workergroup_cmd)
            _write_json(artifact_dir / "vast_workergroup_create.json", created_workergroup)
            workergroup_id = _extract_first_id(created_workergroup, key="id")
            if not workergroup_id:
                raise RuntimeError(f"could not parse Vast workergroup id: {created_workergroup}")

        workergroups = _run_vast(["vastai", "show", "workergroups", "--raw"])
        _write_json(artifact_dir / "vast_workergroups_snapshot.json", workergroups)
        workergroup_record = _find_record(workergroups.get("json"), "id", workergroup_id)
        workergroup_api_key = str(workergroup_record.get("api_key") or "")
        if not template_record:
            template_record, template_resolution = _resolve_template_record(
                args=args,
                workergroup_record=workergroup_record,
                artifact_dir=artifact_dir,
            )

        sdk_request = _sdk_request(
            endpoint_id=endpoint_id,
            worker_route=args.worker_route,
            worker_payload=_read_payload(args.worker_request_file or args.route_payload_file),
            cost=args.request_cost,
            timeout=args.request_timeout,
        )
        route_attempts = sdk_request.get("route_attempts") if isinstance(sdk_request.get("route_attempts"), list) else []
        if not route_attempts and _should_skip_route_probe(sdk_request):
            route_attempts = [_synthetic_sdk_timeout_attempt(sdk_request, endpoint_name=endpoint_name, endpoint_id=endpoint_id)]
        if not route_attempts:
            route_attempts = _route_probe(
                endpoint_name=endpoint_name,
                endpoint_id=endpoint_id,
                workergroup_api_key=workergroup_api_key,
                account_api_key=os.getenv("VAST_API_KEY", "").strip() or os.getenv("VASTAI_API_KEY", "").strip(),
                route_url=args.route_url,
                attempts=args.route_attempts,
                poll_seconds=args.route_poll_seconds,
                route_payload=_read_payload(args.route_payload_file),
            )
        _write_json(artifact_dir / "vast_route_attempts.json", route_attempts)

        if sdk_request.get("response") or sdk_request.get("url") or sdk_request.get("route_attempts"):
            worker_request = sdk_request
            _write_json(artifact_dir / "vast_worker_request.json", worker_request)
        else:
            routed = _first_route_with_url(route_attempts)
            if routed and args.worker_request_file:
                worker_request = _worker_request(routed["body"]["url"], _read_payload(args.worker_request_file))
                _write_json(artifact_dir / "vast_worker_request.json", worker_request)
            elif routed:
                worker_request = {"ok": False, "reason": "route succeeded but worker_request_file not provided", "route": routed}
                _write_json(artifact_dir / "vast_worker_request.json", worker_request)
            else:
                worker_request = {"ok": False, "reason": "no route response returned a worker url", "sdk_request": sdk_request}
                _write_json(artifact_dir / "vast_worker_request.json", worker_request)

    except Exception as exc:
        errors.append(str(exc))
    finally:
        if endpoint_id:
            endpoint_logs_data = _run_vast(["vastai", "get", "endpt-logs", endpoint_id, "--tail", "200"], allow_failure=True)
            _write_json(artifact_dir / "vast_endpoint_logs.json", endpoint_logs_data)
        if workergroup_id:
            workergroup_logs_data = _run_vast(["vastai", "get", "wrkgrp-logs", workergroup_id, "--tail", "200"], allow_failure=True)
            _write_json(artifact_dir / "vast_workergroup_logs.json", workergroup_logs_data)
        if workergroup_id and managed_resources:
            cleanup["steps"].append(
                {"delete_workergroup": _run_vast(["vastai", "delete", "workergroup", workergroup_id, "--raw"], allow_failure=True)}
            )
        if endpoint_id and managed_resources:
            cleanup["steps"].append(
                {"delete_endpoint": _run_vast(["vastai", "delete", "endpoint", endpoint_id, "--raw"], allow_failure=True)}
            )
        cleanup["steps"].append({"show_endpoints": _run_vast(["vastai", "show", "endpoints", "--raw"], allow_failure=True)})
        cleanup["steps"].append({"show_workergroups": _run_vast(["vastai", "show", "workergroups", "--raw"], allow_failure=True)})
        post_guard = provider.cost_guard()
        cleanup["post_guard"] = post_guard
        cleanup["ok"] = ((not managed_resources) or _cleanup_ok(cleanup)) and bool(post_guard.get("ok"))
        cleanup["endpoint_id"] = endpoint_id
        cleanup["workergroup_id"] = workergroup_id
        cleanup["managed_resources"] = managed_resources
        cleanup["cleanup_skipped"] = bool(endpoint_id or workergroup_id) and not managed_resources

    wg_stdout = workergroup_logs_data.get("stdout", "")
    image_pull_status, image_pull_error, image_pull_log_line = _detect_image_pull_issue(wg_stdout)
    worker_statuses = _worker_statuses(sdk_request)
    startup_seconds_observed = _startup_seconds_observed(sdk_request)
    blocker_chain = _blocker_chain(
        image_pull_error=image_pull_error,
        worker_statuses=worker_statuses,
        startup_seconds_observed=startup_seconds_observed,
        route_ok=bool(_first_route_with_url(route_attempts)),
        request_ok=bool(worker_request.get("ok")),
    )
    blocker_type = blocker_chain[0] if blocker_chain else ""

    if image_pull_error:
        errors.append(image_pull_error)

    gpu_metrics = _gpu_metrics(worker_request)
    gpu_probe = _gpu_probe_from_request(worker_request, gpu_metrics=gpu_metrics)
    provider_job_id = _first_string(
        worker_request.get("provider_job_id"),
        _nested_first(worker_request, ("response", "auth_data", "__request_id")),
        _nested_first(worker_request, ("response", "auth_data", "request_idx")),
        _nested_first(worker_request, ("body", "provider_job_id")),
        _nested_first(worker_request, ("body", "request_id")),
        _nested_first(worker_request, ("body", "id")),
    )
    route_ok = bool(_first_route_with_url(route_attempts))
    request_ok = _request_ok(worker_request)
    expected_image_ref = _expected_image_ref(template_record)
    result = {
        "ok": route_ok and request_ok and bool(cleanup.get("ok")) and not image_pull_error,
        "text": SUCCESS_TEXT if route_ok and request_ok else "",
        "provider": "vast",
        "probe_name": PROBE_NAME,
        "execution_mode": "serverless_pyworker_direct_route",
        "model": args.model,
        "endpoint_id": endpoint_id,
        "workergroup_id": workergroup_id,
        "provider_job_id": provider_job_id,
        "workspace_contract_ok": route_ok and request_ok,
        "worker_startup_ok": route_ok,
        "blocker_type": blocker_type,
        "blocker_chain": blocker_chain,
        "worker_status": worker_statuses[0] if worker_statuses else "",
        "worker_statuses": worker_statuses,
        "startup_seconds_observed": startup_seconds_observed,
        "gpu_probe": gpu_probe,
        "image_pull_status": image_pull_status,
        "image_pull_error": image_pull_error,
        "image_pull_log_line": image_pull_log_line,
        "expected_image_ref": expected_image_ref,
        "template_resolution": template_resolution,
        "cache_hit": bool(_nested_first(worker_request, ("body", "cache_hit"))),
        "cleanup": cleanup,
        "managed_resources": managed_resources,
        "cleanup_skipped": bool(endpoint_id or workergroup_id) and not managed_resources,
        "actual_cost_guard": {
            "ok": bool(cleanup.get("post_guard", {}).get("ok")),
            "source": "vast_post_guard",
            "guard": cleanup.get("post_guard", {}),
        },
        "route_attempts": route_attempts,
        "worker_request": worker_request,
        "started_at": started_at,
        "finished_at": now_unix(),
        "errors": errors,
    }
    metrics = {
        "model": args.model,
        "provider_image": expected_image_ref or str(workergroup_record.get("template_hash") or args.template_hash or args.template_id),
        "provider_job_id": provider_job_id,
        "endpoint_id": endpoint_id,
        "workergroup_id": workergroup_id,
        "cache_hit": result["cache_hit"],
        "expected_image_ref": expected_image_ref,
        "worker_status": worker_statuses[0] if worker_statuses else "",
        "startup_seconds_observed": startup_seconds_observed,
        "gpu_probe": gpu_probe,
        "image_pull_status": image_pull_status,
        "image_pull_error": image_pull_error,
        **gpu_metrics,
    }
    verify = {
        "ok": bool(result["ok"]),
        "checks": {
            "route_ok": route_ok,
            "worker_request_ok": request_ok,
            "cleanup_ok": bool(cleanup.get("ok")),
            "post_guard_clean": bool(cleanup.get("post_guard", {}).get("ok")),
            "endpoint_id_present": bool(endpoint_id),
            "workergroup_id_present": bool(workergroup_id),
            "image_pull_ok": not image_pull_error,
            "startup_timeout_observed": startup_seconds_observed > 0,
            "expected_image_ref_present": bool(expected_image_ref),
        },
    }
    probe_info = {
        **result,
        "provider_image": metrics["provider_image"],
        "gpu_probe": gpu_probe,
        "diagnostics": {
            "endpoint_record": _redact(endpoint_record),
            "workergroup_record": _redact(workergroup_record),
            "template_record": _redact(template_record),
            "route_attempts": route_attempts,
            "worker_request": worker_request,
            "worker_statuses": worker_statuses,
            "startup_seconds_observed": startup_seconds_observed,
            "image_pull_status": image_pull_status,
            "image_pull_error": image_pull_error,
            "image_pull_log_line": image_pull_log_line,
            "expected_image_ref": expected_image_ref,
        },
    }

    _write_json(artifact_dir / "result.json", result)
    _write_json(artifact_dir / "metrics.json", metrics)
    _write_json(artifact_dir / "verify.json", verify)
    _write_json(artifact_dir / "probe_info.json", probe_info)
    (artifact_dir / "stdout.log").write_text((SUCCESS_TEXT + "\n") if result["ok"] else "", encoding="utf-8")
    (artifact_dir / "stderr.log").write_text("\n".join(errors) + ("\n" if errors else ""), encoding="utf-8")

    print(json.dumps({"ok": bool(result["ok"]), "artifact_dir": str(artifact_dir)}, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Vast pyworker serverless contract probe without touching provider adapters.")
    parser.add_argument("--artifact-dir", default="")
    parser.add_argument("--endpoint-name", default="")
    parser.add_argument("--template-hash", default="")
    parser.add_argument("--template-id", default="")
    parser.add_argument("--discover-template-query", default="")
    parser.add_argument("--discover-template-limit", type=int, default=20)
    parser.add_argument("--discover-template-image-substring", default="")
    parser.add_argument("--discover-template-bootstrap-substring", default="")
    parser.add_argument("--model", default="whisper-large-v3")
    parser.add_argument("--test-workers", type=int, default=1)
    parser.add_argument("--cold-workers", type=int, default=0)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--gpu-ram", type=float, default=1.0)
    parser.add_argument("--min-load", type=float, default=0.0)
    parser.add_argument("--min-cold-load", type=float, default=0.0)
    parser.add_argument("--inactivity-timeout", type=int, default=60)
    parser.add_argument("--route-attempts", type=int, default=8)
    parser.add_argument("--route-poll-seconds", type=int, default=10)
    parser.add_argument("--route-url", default=DEFAULT_ROUTE_URL)
    parser.add_argument("--route-payload-file", default="")
    parser.add_argument("--worker-request-file", default="")
    parser.add_argument("--worker-route", default="/health")
    parser.add_argument("--request-cost", type=int, default=100)
    parser.add_argument("--request-timeout", type=float, default=60.0)
    parser.add_argument("--search-params", default="")
    parser.add_argument(
        "--existing-endpoint-id",
        default="",
        help="Use an already-existing official/provider-created endpoint instead of creating one in this sidecar run.",
    )
    parser.add_argument(
        "--existing-workergroup-id",
        default="",
        help="Use an already-existing official/provider-created workergroup together with --existing-endpoint-id.",
    )
    parser.add_argument(
        "--existing-endpoint-name",
        default="",
        help="Optional endpoint name override for route/auth experiments when using existing Vast resources.",
    )
    args = parser.parse_args()
    if not args.existing_endpoint_id and not args.template_hash and not args.template_id and not args.discover_template_query:
        raise SystemExit("--template-hash or --template-id is required")
    return args


def _default_artifact_dir() -> Path:
    return app_data_dir() / "provider-contract-probes" / f"vast-pyworker-direct-{time.strftime('%Y%m%d-%H%M%S')}"


def _ensure_vast_api_key_env() -> None:
    if os.getenv("VAST_API_KEY", "").strip() or os.getenv("VASTAI_API_KEY", "").strip():
        return
    candidates = [
        Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "vastai" / "vast_api_key",
        Path.home() / ".vast_api_key",
    ]
    for key_path in candidates:
        if key_path.is_file():
            api_key = key_path.read_text(encoding="utf-8").strip()
            if api_key:
                os.environ["VAST_API_KEY"] = api_key
                return


def _run_vast(cmd: list[str], *, allow_failure: bool = False) -> dict[str, Any]:
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=180)
    result = {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout": _redact(proc.stdout),
        "stderr": _redact(proc.stderr),
        "ok": proc.returncode == 0,
    }
    parsed = _try_json(proc.stdout)
    if parsed is not None:
        result["json"] = _redact(parsed)
    if proc.returncode != 0 and not allow_failure:
        raise RuntimeError(f"command failed: {cmd}: {proc.stderr.strip() or proc.stdout.strip()}")
    return result


def _route_probe(
    *,
    endpoint_name: str,
    endpoint_id: str,
    workergroup_api_key: str,
    account_api_key: str,
    route_url: str,
    attempts: int,
    poll_seconds: int,
    route_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = []
    for endpoint_value in (endpoint_name, endpoint_id):
        if endpoint_value:
            for api_key in (workergroup_api_key, account_api_key, ""):
                payload = dict(route_payload)
                payload.setdefault("cost", 1.0)
                payload["endpoint"] = endpoint_value
                if api_key:
                    payload["api_key"] = api_key
                candidates.append(payload)
    responses: list[dict[str, Any]] = []
    for attempt in range(attempts):
        for payload in candidates:
            safe_payload = _redact(payload)
            req = urllib.request.Request(
                route_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = _decode_json(resp.read())
                    responses.append({"t": attempt, "payload": safe_payload, "http_status": resp.status, "body": _redact(body)})
            except urllib.error.HTTPError as exc:
                body = _decode_json(exc.read())
                responses.append({"t": attempt, "payload": safe_payload, "http_status": exc.code, "body": _redact(body)})
            except Exception as exc:
                responses.append({"t": attempt, "payload": safe_payload, "error": str(exc)})
        if _first_route_with_url(responses):
            break
        time.sleep(poll_seconds)
    return responses


def _sdk_request(
    *,
    endpoint_id: str,
    worker_route: str,
    worker_payload: dict[str, Any],
    cost: int,
    timeout: float,
) -> dict[str, Any]:
    async def runner() -> dict[str, Any]:
        try:
            from vastai.serverless.client.client import Serverless
            from vastai.serverless.client.managed import ManagedEndpoint
        except Exception as exc:
            return {"ok": False, "error": f"vastai serverless sdk import failed: {exc}"}

        request_result: dict[str, Any] = {"ok": False}
        async with Serverless() as client:
            endpoint = ManagedEndpoint(id=int(endpoint_id), client=client)
            try:
                response = await endpoint.request(route=worker_route, payload=worker_payload, cost=cost, timeout=timeout)
                request_result = {"ok": _sdk_response_ok(response), "response": _redact(response)}
                if isinstance(response, dict):
                    if response.get("url"):
                        request_result["url"] = response.get("url")
                        request_result["route_attempts"] = [
                            {"http_status": 200, "payload": {"sdk": True}, "body": {"url": response.get("url")}}
                        ]
                    if isinstance(response.get("response"), dict):
                        request_result["body"] = _redact(response.get("response"))
                    if response.get("status") is not None:
                        request_result["http_status"] = response.get("status")
            except Exception as exc:
                request_result = {"ok": False, "error": str(exc)}
            try:
                routing_endpoint = await endpoint._get_routing_endpoint()
                workers = await client.get_endpoint_workers(routing_endpoint)
                request_result["endpoint_workers"] = _redact([worker.__dict__ for worker in workers])
            except Exception as exc:
                request_result["endpoint_workers_error"] = str(exc)
        return request_result

    return asyncio.run(runner())


def _worker_request(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = _decode_json(resp.read())
            return {"ok": 200 <= resp.status < 300, "http_status": resp.status, "body": _redact(body)}
    except urllib.error.HTTPError as exc:
        body = _decode_json(exc.read())
        return {"ok": False, "http_status": exc.code, "body": _redact(body)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _sdk_response_ok(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    if response.get("ok") is False:
        return False
    status = response.get("status")
    try:
        if status is not None and int(status) >= 400:
            return False
    except (TypeError, ValueError):
        pass
    body = response.get("response")
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return False
    return True


def _request_ok(worker_request: dict[str, Any]) -> bool:
    if not isinstance(worker_request, dict):
        return False
    if worker_request.get("ok") is False:
        return False
    status = worker_request.get("http_status")
    try:
        if status is not None and int(status) >= 400:
            return False
    except (TypeError, ValueError):
        pass
    body = worker_request.get("body")
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return False
    response = worker_request.get("response")
    if isinstance(response, dict):
        nested_ok = response.get("ok")
        if nested_ok is False:
            return False
        nested_status = response.get("status")
        try:
            if nested_status is not None and int(nested_status) >= 400:
                return False
        except (TypeError, ValueError):
            pass
        nested_body = response.get("response")
        if isinstance(nested_body, dict) and isinstance(nested_body.get("error"), dict):
            return False
    return bool(worker_request.get("url") or _first_route_with_url(worker_request.get("route_attempts") or []))


def _cleanup_ok(cleanup: dict[str, Any]) -> bool:
    post_guard = cleanup.get("post_guard")
    if not isinstance(post_guard, dict) or not post_guard.get("ok"):
        return False
    endpoints_empty = False
    workergroups_empty = False
    for step in cleanup.get("steps") or []:
        if not isinstance(step, dict):
            continue
        endpoints = step.get("show_endpoints")
        if isinstance(endpoints, dict) and endpoints.get("json") == []:
            endpoints_empty = True
        workergroups = step.get("show_workergroups")
        if isinstance(workergroups, dict) and workergroups.get("json") == []:
            workergroups_empty = True
    return endpoints_empty and workergroups_empty


def _extract_first_id(text: str, *, key: str) -> str:
    if isinstance(text, dict):
        direct = text.get("json")
        if isinstance(direct, dict):
            value = direct.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        if isinstance(direct, list) and direct:
            first = direct[0]
            if isinstance(first, dict):
                value = first.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()
        text = str(text.get("stdout", ""))
    patterns = [
        rf'"{re.escape(key)}"\s*:\s*(\d+)',
        rf"'{re.escape(key)}'\s*:\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return str(match.group(1))
    return ""


def _lookup_template(template_hash: str) -> dict[str, Any]:
    query = f"hash_id == {template_hash}"
    result = _run_vast(["vastai", "search", "templates", query, "--raw"], allow_failure=True)
    payload = result.get("json")
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            return first
    return {}


def _lookup_template_by_id(template_id: str) -> dict[str, Any]:
    query = f"id == {template_id}"
    result = _run_vast(["vastai", "search", "templates", query, "--raw"], allow_failure=True)
    payload = result.get("json")
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            return first
    return {}


def _discover_templates(
    *,
    query: str,
    limit: int,
    image_substring: str,
    bootstrap_substring: str,
) -> list[dict[str, Any]]:
    result = _run_vast(["vastai", "search", "templates", query, "--raw"], allow_failure=False)
    payload = result.get("json")
    if not isinstance(payload, list):
        return []
    image_filter = image_substring.strip().lower()
    bootstrap_filter = bootstrap_substring.strip().lower()
    filtered: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        image = str(item.get("image") or item.get("repo") or "").lower()
        bootstrap = str(item.get("bootstrap_script") or item.get("onstart") or "").lower()
        if image_filter and image_filter not in image:
            continue
        if bootstrap_filter and bootstrap_filter not in bootstrap:
            continue
        filtered.append(item)
    ranked = sorted(filtered, key=_template_candidate_sort_key, reverse=True)
    return ranked[: max(1, limit)]


def _resolve_template_record(
    *,
    args: argparse.Namespace,
    workergroup_record: dict[str, Any],
    artifact_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if args.template_hash:
        template = _lookup_template(args.template_hash)
        if template:
            _write_json(artifact_dir / "vast_template_lookup.json", template)
        return template, {"source": "template_hash_arg", "template_hash": args.template_hash}
    if args.template_id:
        template = _lookup_template_by_id(args.template_id)
        if template:
            _write_json(artifact_dir / "vast_template_lookup.json", template)
        return template, {"source": "template_id_arg", "template_id": args.template_id}
    inferred_hash = str(workergroup_record.get("template_hash") or "").strip()
    inferred_id = str(workergroup_record.get("template_id") or "").strip()
    if inferred_hash:
        template = _lookup_template(inferred_hash)
        if template:
            _write_json(artifact_dir / "vast_template_lookup.json", template)
        return template, {"source": "workergroup_record", "template_hash": inferred_hash, "template_id": inferred_id}
    if inferred_id:
        template = _lookup_template_by_id(inferred_id)
        if template:
            _write_json(artifact_dir / "vast_template_lookup.json", template)
        return template, {"source": "workergroup_record", "template_id": inferred_id}
    query = str(args.discover_template_query or "").strip()
    if not query:
        return {}, {"source": "none"}
    candidates = _discover_templates(
        query=query,
        limit=args.discover_template_limit,
        image_substring=args.discover_template_image_substring,
        bootstrap_substring=args.discover_template_bootstrap_substring,
    )
    _write_json(artifact_dir / "vast_template_candidates.json", candidates)
    if not candidates:
        return {}, {"source": "discovery", "query": query, "selected": False}
    selected = candidates[0]
    _write_json(artifact_dir / "vast_template_lookup.json", selected)
    return selected, {
        "source": "discovery",
        "query": query,
        "selected": True,
        "template_hash": str(selected.get("hash_id") or ""),
        "template_id": str(selected.get("id") or ""),
    }


def _template_candidate_sort_key(template: dict[str, Any]) -> tuple[int, float]:
    score = 0
    onstart = str(template.get("bootstrap_script") or template.get("onstart") or "").lower()
    extra_filters = str(template.get("extra_filters") or "").lower()
    name = str(template.get("name") or "").lower()
    tag = str(template.get("tag") or "").lower()
    if "pyworker" in onstart:
        score += 40
    if "gpu_ram" in extra_filters:
        score += 20
    if "serverless" in name:
        score += 10
    if tag and "automatic" not in tag:
        score += 10
    if str(template.get("image") or template.get("repo") or "").lower() == "vastai/vllm":
        score += 5
    created_at = float(template.get("created_at") or 0.0)
    return (score, created_at)


def _expected_image_ref(template_record: dict[str, Any]) -> str:
    image = str(template_record.get("image") or template_record.get("repo") or "").strip()
    tag = str(template_record.get("tag") or "").strip()
    if image and tag:
        return f"{image}:{tag}"
    return image


def _detect_image_pull_issue(log_text: str) -> tuple[str, str, str]:
    for line in log_text.splitlines():
        lowered = line.lower()
        if "manifest for" in lowered and "not found" in lowered:
            return ("manifest_not_found", "Vast worker error: image manifest not found", line.strip())
        if "while pulling" in lowered and "503" in lowered:
            return ("registry_pull_failed", "Vast worker error: 503 while pulling image", line.strip())
        if "pull access denied" in lowered or "repository does not exist" in lowered:
            return ("image_pull_failed", "Vast worker error: image pull failed", line.strip())
    return ("", "", "")


def _blocker_chain(
    *,
    image_pull_error: str,
    worker_statuses: list[str],
    startup_seconds_observed: float,
    route_ok: bool,
    request_ok: bool,
) -> list[str]:
    blockers: list[str] = []
    if image_pull_error:
        blockers.append("image_pull")
    if startup_seconds_observed > 0 and any(status in {"created", "loading", "model_loading"} for status in worker_statuses):
        blockers.append("startup_timeout")
    if not route_ok:
        blockers.append("route")
    elif not request_ok:
        blockers.append("worker_request")
    return blockers


def _worker_statuses(sdk_request: dict[str, Any]) -> list[str]:
    workers = sdk_request.get("endpoint_workers")
    if not isinstance(workers, list):
        return []
    statuses: list[str] = []
    for worker in workers:
        if not isinstance(worker, dict):
            continue
        status = str(worker.get("status") or "").strip()
        if status:
            statuses.append(status)
    return statuses


def _startup_seconds_observed(sdk_request: dict[str, Any]) -> float:
    error = str(sdk_request.get("error") or "")
    match = re.search(r"Timed out after ([0-9]+(?:\.[0-9]+)?)s", error)
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0


def _should_skip_route_probe(sdk_request: dict[str, Any]) -> bool:
    error_text = str(sdk_request.get("error") or "").lower()
    endpoint_workers = sdk_request.get("endpoint_workers")
    if "waiting for worker to become ready" not in error_text:
        return False
    return isinstance(endpoint_workers, list) and len(endpoint_workers) > 0


def _synthetic_sdk_timeout_attempt(sdk_request: dict[str, Any], *, endpoint_name: str, endpoint_id: str) -> dict[str, Any]:
    return {
        "t": 0,
        "payload": {
            "source": "sdk_timeout",
            "endpoint": endpoint_name or endpoint_id,
        },
        "body": {
            "status": "sdk_request_timed_out_waiting_for_ready_worker",
            "error": str(sdk_request.get("error") or ""),
            "endpoint_workers": _redact(sdk_request.get("endpoint_workers") or []),
        },
    }


def _find_record(payload: Any, key: str, value: str) -> dict[str, Any]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and str(item.get(key) or "") == str(value):
                return item
    return {}


def _read_payload(path_text: str) -> dict[str, Any]:
    if not path_text:
        return {}
    payload_path = Path(path_text)
    return json.loads(payload_path.read_text(encoding="utf-8"))


def _first_route_with_url(route_attempts: list[dict[str, Any]]) -> dict[str, Any]:
    for attempt in route_attempts:
        body = attempt.get("body")
        if isinstance(body, dict) and body.get("url"):
            return attempt
    return {}


def _gpu_metrics(worker_request: dict[str, Any]) -> dict[str, Any]:
    body = worker_request.get("body") if isinstance(worker_request.get("body"), dict) else {}
    candidates = [
        body,
        body.get("metrics") if isinstance(body.get("metrics"), dict) else {},
        body.get("gpu") if isinstance(body.get("gpu"), dict) else {},
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        metrics: dict[str, Any] = {}
        for key in ("gpu_utilization_percent", "gpu_utilization", "gpu_memory_used_mb", "gpu_memory_mb", "vram_used_mb"):
            value = candidate.get(key)
            if value is not None:
                metrics[key] = value
        if metrics:
            return metrics
    return {}


def _gpu_probe_from_request(worker_request: dict[str, Any], *, gpu_metrics: dict[str, Any]) -> dict[str, Any]:
    if gpu_metrics:
        return {
            "ok": True,
            "source": "worker_request_gpu_metrics",
            "metrics": gpu_metrics,
        }
    workers = worker_request.get("endpoint_workers")
    if isinstance(workers, list) and workers and _request_ok(worker_request):
        return {
            "ok": True,
            "source": "endpoint_workers",
            "gpu_count": len([worker for worker in workers if isinstance(worker, dict)]),
            "worker_statuses": _worker_statuses(worker_request),
        }
    return {}


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_redact(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _try_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _decode_json(data: bytes) -> Any:
    text = data.decode("utf-8", errors="replace")
    parsed = _try_json(text)
    return parsed if parsed is not None else {"raw": text}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key in {"api_key", "authorization", "Authorization"}:
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(api_key=)[^&\s]+", r"\1<redacted>", value)
        value = re.sub(r'"api_key"\s*:\s*"[^"]+"', '"api_key":"<redacted>"', value)
        return value
    return value


def _nested_first(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_string(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
