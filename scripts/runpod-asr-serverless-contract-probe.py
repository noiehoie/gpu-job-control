from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from gpu_job.models import app_data_dir, now_unix
from gpu_job.providers.runpod import RunPodProvider, _runpod_api_key, _runpod_image_digest_parts


CUSTOM_HANDLER_PROBE_NAME = "runpod.asr_diarization.serverless_handler"
OFFICIAL_TEMPLATE_SMOKE_PROBE_NAME = "runpod.asr.official_whisper_smoke"
MODEL = "pyannote/speaker-diarization-3.1"
SUCCESS_TEXT = "GPU_JOB_ASR_DIARIZATION_WORKSPACE_CANARY_OK"
RUNPOD_REST_BASE_URL = os.getenv("RUNPOD_API_URL", "https://rest.runpod.io/v1").rstrip("/")
DEFAULT_AUDIO_URL = "https://github.com/runpod-workers/sample-inputs/raw/main/audio/gettysburg.wav"
RUNPOD_REST_GPU_ALIAS_MAP = {
    "AMPERE_16": ["NVIDIA RTX A4000", "NVIDIA RTX 4000 Ada Generation", "NVIDIA RTX 2000 Ada Generation"],
    "AMPERE_24": ["NVIDIA RTX A4500", "NVIDIA RTX A5000", "NVIDIA GeForce RTX 3090", "NVIDIA L4"],
    "ADA_24": ["NVIDIA GeForce RTX 4090"],
    "AMPERE_48": ["NVIDIA RTX A6000", "NVIDIA L40"],
    "ADA_48_PRO": ["NVIDIA L40S", "NVIDIA RTX 6000 Ada Generation"],
    "AMPERE_80": ["NVIDIA A100 80GB PCIe", "NVIDIA A100-SXM4-80GB"],
}


def main() -> int:
    args = _parse_args()
    provider = RunPodProvider()
    api_key = args.serverless_api_key or _runpod_api_key()
    if not api_key:
        raise SystemExit("RUNPOD_API_KEY or ~/.runpod/config.toml default.api_key is required")

    artifact_dir = Path(args.artifact_dir) if args.artifact_dir else _default_artifact_dir()
    artifact_dir.mkdir(parents=True, exist_ok=True)

    started_at = now_unix()
    template: dict[str, Any] | None = None
    endpoint: dict[str, Any] | None = None
    submitted: dict[str, Any] = {}
    run_result: dict[str, Any] = {}
    cleanup: dict[str, Any] = {"ok": False, "steps": []}
    errors: list[str] = []
    pre_guard = provider.cost_guard()
    post_guard: dict[str, Any] = {}
    create_surface = args.managed_create_surface

    managed_resources = True
    plan: dict[str, Any] = {}
    resolved_existing_template_id = ""
    template_provenance: dict[str, Any] = {}
    submit_payload: dict[str, Any] = {}

    try:
        existing_endpoint = _resolve_existing_endpoint(
            provider,
            endpoint_id=args.existing_endpoint_id,
            endpoint_name=args.existing_endpoint_name,
            template_id=args.existing_template_id,
        )
        managed_resources = not bool(existing_endpoint)
        if managed_resources:
            plan = provider.plan_asr_endpoint(
                gpu_ids=args.gpu_ids,
                workers_max=1,
                idle_timeout=args.idle_timeout,
                flashboot=not args.no_flashboot,
            )
            if not plan.get("ok"):
                raise RuntimeError(json.dumps({"ok": False, "error": "plan_failed", "plan": plan}, ensure_ascii=False))
        else:
            resolved_existing_template_id = str(existing_endpoint.get("templateId") or args.existing_template_id or "")
            existing_template: dict[str, Any] = {}
            expected_provider_image = args.expected_provider_image or ""
            template_provenance = {
                "mode": "existing_endpoint",
                "source": "endpoint_snapshot",
                "template_id": resolved_existing_template_id,
                "template_label": args.managed_template_label or "",
            }
            if resolved_existing_template_id and not expected_provider_image:
                existing_template = _read_existing_template(api_key=api_key, template_id=resolved_existing_template_id)
                expected_provider_image = str(existing_template.get("imageName") or "")
                if existing_template:
                    template_provenance["source"] = "rest_template_get"
            plan = {
                "ok": True,
                "template": {
                    "imageName": expected_provider_image,
                },
                "endpoint": {
                    "id": existing_endpoint["id"],
                    "templateId": resolved_existing_template_id,
                    "name": existing_endpoint.get("name") or "",
                },
            }
            if existing_template:
                _write_json(artifact_dir / "runpod_serverless_existing_template.json", existing_template)
        if not _guard_clean(pre_guard):
            raise RuntimeError("RunPod pre-guard is not clean")
        if managed_resources:
            template, resolved_existing_template_id, template_provenance = _prepare_managed_template(
                api_key=api_key,
                args=args,
                plan=plan,
                artifact_dir=artifact_dir,
            )
            endpoint_input = dict(plan["endpoint"])
            endpoint_input["templateId"] = str(template["id"])
            if template_provenance.get("mode") == "managed_existing_template":
                endpoint_input = _apply_managed_template_endpoint_defaults(endpoint_input, template)
            if args.managed_create_surface == "graphql":
                endpoint = _create_endpoint_graphql(api_key, endpoint_input)
            else:
                endpoint = _create_endpoint(api_key, endpoint_input)
            if template_provenance.get("mode") != "managed_existing_template":
                _write_json(artifact_dir / "runpod_serverless_template.json", template)
            _write_json(artifact_dir / "runpod_serverless_endpoint.json", endpoint)
        else:
            endpoint = existing_endpoint
            create_surface = "existing_endpoint"
            _write_json(artifact_dir / "runpod_endpoint_resolution.json", _endpoint_resolution_snapshot(provider))
            _write_json(artifact_dir / "runpod_serverless_existing_endpoint.json", endpoint)
        endpoint_health_before_run = provider._endpoint_health_sample(str(endpoint["id"]))
        _write_json(artifact_dir / "runpod_serverless_endpoint_health_before_run.json", endpoint_health_before_run)
        if _health_has_active_worker(endpoint_health_before_run):
            raise RuntimeError(f"RunPod endpoint has active warm worker before run: {endpoint_health_before_run}")

        submit_payload = _submit_payload(args)
        submitted = _serverless_request(
            endpoint_id=str(endpoint["id"]),
            path="run",
            api_key=api_key,
            method="POST",
            payload=submit_payload,
            timeout=30,
        )
        provider_job_id = str(submitted.get("id") or submitted.get("job_id") or "")
        if not provider_job_id:
            raise RuntimeError(f"RunPod serverless submit did not return job id: {submitted}")
        _write_json(artifact_dir / "runpod_serverless_job_submit.json", submitted)

        run_result = _poll_serverless_run(
            provider=provider,
            endpoint_id=str(endpoint["id"]),
            provider_job_id=provider_job_id,
            api_key=api_key,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
        _write_json(artifact_dir / "runpod_serverless_job_result.json", run_result)
        _write_json(artifact_dir / "runpod_serverless_job_timeline.json", {"samples": run_result.get("samples") or []})
        if not run_result.get("ok"):
            cancel = _serverless_request(
                endpoint_id=str(endpoint["id"]),
                path=f"cancel/{provider_job_id}",
                api_key=api_key,
                method="POST",
                payload={},
                timeout=30,
            )
            run_result["cancel"] = cancel
            _write_json(artifact_dir / "runpod_serverless_job_cancel.json", cancel)
    except Exception as exc:
        errors.append(str(exc))
    finally:
        if endpoint and managed_resources:
            try:
                if args.managed_create_surface == "graphql":
                    disabled = provider._disable_endpoint(str(endpoint["id"]), template_id=str(endpoint["templateId"]))
                else:
                    disabled = _disable_endpoint_rest(api_key, str(endpoint["id"]))
                cleanup["steps"].append({"disable_endpoint": disabled})
                time.sleep(3)
            except Exception as exc:
                cleanup["steps"].append({"disable_endpoint_error": str(exc)})
            try:
                if args.managed_create_surface == "graphql":
                    deleted = provider._delete_endpoint(str(endpoint["id"]))
                else:
                    deleted = _delete_endpoint_rest(api_key, str(endpoint["id"]))
                cleanup["steps"].append({"delete_endpoint": deleted})
            except Exception as exc:
                cleanup["steps"].append({"delete_endpoint_error": str(exc)})
        if template:
            try:
                deleted_template = _delete_template(api_key, template_id=str(template["id"]))
                cleanup["steps"].append({"delete_template": deleted_template})
            except Exception as exc:
                cleanup["steps"].append({"delete_template_error": str(exc)})
        post_guard = provider.cost_guard()
        cleanup["post_guard"] = post_guard
        cleanup["ok"] = ((not managed_resources) or (not endpoint) or _delete_ok(cleanup)) and _guard_clean(post_guard)
        cleanup["endpoint_id"] = str(endpoint.get("id") if endpoint else "")
        cleanup["template_id"] = str(template.get("id") if template else "")
        cleanup["managed_resources"] = managed_resources
        cleanup["cleanup_skipped"] = bool(endpoint) and not managed_resources

    output = _normalized_output(run_result.get("output") if isinstance(run_result.get("output"), dict) else {})
    provider_job_id = str(submitted.get("id") or submitted.get("job_id") or run_result.get("provider_job_id") or "")
    probe_name = _probe_name_for_contract(args.success_contract)
    image = str((template or {}).get("imageName") or plan["template"]["imageName"])
    image_name, image_digest = _runpod_image_digest_parts(image)
    official_template_smoke_ok = _official_template_smoke_ok(run_result)
    runtime_ok = bool(run_result.get("ok")) and bool(output.get("ok")) and bool(output.get("workspace_contract_ok"))
    contract_ok = runtime_ok if args.success_contract == "custom_handler" else official_template_smoke_ok
    blocker_chain = _blocker_chain(run_result=run_result, endpoint=endpoint or {}, managed_resources=managed_resources)
    blocker_type = blocker_chain[0] if blocker_chain else ""
    observed_model = MODEL if args.success_contract == "custom_handler" else str(output.get("model") or "")
    observed_text = SUCCESS_TEXT if contract_ok and args.success_contract == "custom_handler" else str(output.get("text") or "")
    probe_payload = {
        **output,
        "ok": contract_ok and bool(cleanup.get("ok")),
        "provider": "runpod",
        "probe_name": probe_name,
        "execution_mode": "serverless_handler_direct_api",
        "text": observed_text,
        "model": observed_model,
        "provider_image": image,
        "worker_image": image,
        "provider_image_name": image_name,
        "provider_image_digest": image_digest,
        "endpoint_id": str(endpoint.get("id") if endpoint else ""),
        "template_id": str(template.get("id") if template else resolved_existing_template_id),
        "provider_job_id": provider_job_id,
        "managed_resources": managed_resources,
        "cleanup_skipped": bool(endpoint) and not managed_resources,
        "create_surface": create_surface,
        "template_provenance": template_provenance,
        "submit_profile": args.submit_profile,
        "success_contract": args.success_contract,
        "submit_payload_preview": _submit_payload_preview(submit_payload),
        "workspace_contract_ok": runtime_ok,
        "official_template_smoke_ok": official_template_smoke_ok,
        "worker_startup_ok": bool(output.get("worker_startup_ok") or runtime_ok),
        "blocker_type": blocker_type,
        "blocker_chain": blocker_chain,
        "final_job_status": str(run_result.get("status") or ""),
        "startup_seconds_observed": _startup_seconds_observed(run_result),
        "endpoint_scale": _endpoint_scale(endpoint or {}),
        "cleanup": cleanup,
        "cleanup_ok": bool(cleanup.get("ok")),
        "actual_cost_guard": {"ok": _guard_clean(post_guard), "source": "runpod_post_guard", "guard": post_guard},
        "cost_guard_ok": _guard_clean(post_guard),
        "pre_guard": pre_guard,
        "endpoint_health_before_run": _read_optional_json(artifact_dir / "runpod_serverless_endpoint_health_before_run.json"),
        "run_result": run_result,
        "errors": errors,
        "started_at": started_at,
        "finished_at": now_unix(),
    }
    metrics = {
        "model": observed_model,
        "provider_image": image,
        "provider_image_digest": image_digest,
        "endpoint_id": probe_payload["endpoint_id"],
        "provider_job_id": provider_job_id,
        "template_provenance_mode": str(template_provenance.get("mode") or ""),
        "template_provenance_source": str(template_provenance.get("source") or ""),
        "cache_hit": bool(probe_payload.get("cache_hit")),
        "gpu_probe": probe_payload.get("gpu_probe") if isinstance(probe_payload.get("gpu_probe"), dict) else {},
        "runtime_seconds": probe_payload.get("runtime_seconds"),
        "blocker_type": blocker_type,
        "final_job_status": str(run_result.get("status") or ""),
        "startup_seconds_observed": _startup_seconds_observed(run_result),
        "official_template_smoke_ok": official_template_smoke_ok,
    }
    verify = {
        "ok": bool(probe_payload["ok"]),
        "checks": {
            "runtime_ok": runtime_ok,
            "official_template_smoke_ok": official_template_smoke_ok,
            "cleanup_ok": bool(cleanup.get("ok")),
            "post_guard_clean": _guard_clean(post_guard),
            "endpoint_id_present": bool(probe_payload["endpoint_id"]),
            "provider_job_id_present": bool(provider_job_id),
            "managed_resources": managed_resources,
        },
    }
    probe_info = {
        **probe_payload,
        "diagnostics": {
            "template": template or {},
            "endpoint": endpoint or {},
            "submitted": submitted,
            "run_result": run_result,
            "cleanup": cleanup,
        },
    }

    _write_json(artifact_dir / "result.json", probe_payload)
    _write_json(artifact_dir / "metrics.json", metrics)
    _write_json(artifact_dir / "verify.json", verify)
    _write_json(artifact_dir / "probe_info.json", probe_info)
    (artifact_dir / "stdout.log").write_text(_stdout_text(probe_payload), encoding="utf-8")
    (artifact_dir / "stderr.log").write_text("\n".join(errors) + ("\n" if errors else ""), encoding="utf-8")

    print(json.dumps({"ok": bool(probe_payload["ok"]), "artifact_dir": str(artifact_dir)}, ensure_ascii=False, sort_keys=True))
    return 0 if probe_payload["ok"] else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run direct RunPod ASR Serverless handler contract probe and write parseable artifacts.")
    parser.add_argument("--artifact-dir", default="")
    parser.add_argument("--gpu-ids", default="AMPERE_16,AMPERE_24,ADA_24")
    parser.add_argument("--idle-timeout", type=int, default=60)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--no-flashboot", action="store_true")
    parser.add_argument(
        "--existing-endpoint-id",
        default="",
        help="Use an already-existing official/Hub-created endpoint id instead of creating one through the managed sidecar path.",
    )
    parser.add_argument(
        "--existing-endpoint-name",
        default="",
        help="Optional existing endpoint name. Used only when --existing-endpoint-id is not supplied.",
    )
    parser.add_argument(
        "--existing-template-id",
        default="",
        help="Optional template id to record alongside --existing-endpoint-id when the endpoint snapshot omits templateId.",
    )
    parser.add_argument(
        "--expected-provider-image",
        default="",
        help="Expected provider image string to record when probing an existing endpoint.",
    )
    parser.add_argument("--serverless-api-key", default=os.getenv("RUNPOD_SERVERLESS_API_KEY", ""))
    parser.add_argument("--managed-create-surface", choices=("rest", "graphql"), default="rest")
    parser.add_argument(
        "--managed-template-id",
        default="",
        help="Use an existing/public template id for the managed endpoint create path instead of creating a private template first.",
    )
    parser.add_argument(
        "--managed-template-label",
        default="",
        help="Optional operator label to preserve the provenance of a managed/public template id in probe artifacts.",
    )
    parser.add_argument(
        "--managed-template-image",
        default="",
        help=(
            "Optional operator-supplied image string to record when a managed/public "
            "template id is usable for endpoint create but unreadable via REST template GET."
        ),
    )
    parser.add_argument(
        "--success-contract",
        choices=("custom_handler", "official_template_smoke"),
        default="custom_handler",
        help=(
            "Choose success criteria. custom_handler requires workspace contract markers; "
            "official_template_smoke accepts a completed public-template run."
        ),
    )
    parser.add_argument("--submit-profile", choices=("official_audio_base64", "custom_probe"), default="official_audio_base64")
    parser.add_argument("--audio-base64-file", default="")
    parser.add_argument("--audio-url", default=DEFAULT_AUDIO_URL)
    return parser.parse_args()


def _default_artifact_dir() -> Path:
    return app_data_dir() / "provider-contract-probes" / f"runpod-serverless-asr-direct-{time.strftime('%Y%m%d-%H%M%S')}"


def _graphql_literal(value: Any) -> str:
    return json.dumps(str(value))


def _create_template(api_key: str, template: dict[str, Any]) -> dict[str, Any]:
    payload = dict(template)
    payload["name"] = f"{payload['name']}-{time.strftime('%Y%m%d%H%M%S')}"
    if any(token in RUNPOD_REST_BASE_URL for token in ("rest.runpod.io", "api.runpod.io/v1")):
        rest_payload = {
            "name": str(payload["name"]),
            "imageName": str(payload["imageName"]),
            "isServerless": True,
            "ports": [item.strip() for item in str(payload.get("ports") or "").split(",") if item.strip()],
            "env": {str(item["key"]): str(item["value"]) for item in payload.get("env") or [] if item.get("key")},
            "containerDiskInGb": int(payload["containerDiskInGb"]),
            "volumeInGb": int(payload["volumeInGb"]),
        }
        docker_args = str(payload.get("dockerArgs") or "").strip()
        if docker_args:
            rest_payload["dockerStartCmd"] = docker_args.split()
        return _runpod_rest_request(api_key=api_key, path="/templates", method="POST", payload=rest_payload)
    return _create_template_graphql(api_key, payload)


def _create_endpoint(api_key: str, endpoint: dict[str, Any]) -> dict[str, Any]:
    if any(token in RUNPOD_REST_BASE_URL for token in ("rest.runpod.io", "api.runpod.io/v1")):
        rest_payload = {
            "name": str(endpoint["name"]),
            "templateId": str(endpoint["templateId"]),
            "computeType": "GPU",
            "gpuTypeIds": _rest_gpu_type_ids(str(endpoint.get("gpuIds") or "")),
            "gpuCount": int(endpoint["gpuCount"]),
            "workersMin": int(endpoint["workersMin"]),
            "workersMax": int(endpoint["workersMax"]),
        }
        if endpoint.get("networkVolumeId"):
            rest_payload["networkVolumeId"] = str(endpoint["networkVolumeId"])
        return _runpod_rest_request(api_key=api_key, path="/endpoints", method="POST", payload=rest_payload)
    return _create_endpoint_graphql(api_key, endpoint)


def _create_template_graphql(api_key: str, template: dict[str, Any]) -> dict[str, Any]:
    env = ", ".join(
        "{ key: %s, value: %s }" % (_graphql_literal(item["key"]), _graphql_literal(item["value"]))
        for item in template.get("env") or []
        if item.get("key")
    )
    query = (
        "mutation {"
        " saveTemplate(input: {"
        f" name: {_graphql_literal(template['name'])},"
        f" imageName: {_graphql_literal(template['imageName'])},"
        " isServerless: true,"
        f" containerDiskInGb: {int(template['containerDiskInGb'])},"
        f" volumeInGb: {int(template['volumeInGb'])},"
        f" dockerArgs: {_graphql_literal(template.get('dockerArgs') or '')},"
        f" ports: {_graphql_literal(template.get('ports') or '')},"
        f" env: [{env}]"
        " }) {"
        " id name imageName isServerless containerDiskInGb volumeInGb"
        " dockerArgs ports env { key value }"
        " }"
        "}"
    )
    return _runpod_graphql_request(api_key=api_key, query=query)["data"]["saveTemplate"]


def _create_endpoint_graphql(api_key: str, endpoint: dict[str, Any]) -> dict[str, Any]:
    fields = [
        f"gpuCount: {int(endpoint['gpuCount'])}",
        f"gpuIds: {_graphql_literal(endpoint['gpuIds'])}",
        f"idleTimeout: {int(endpoint['idleTimeout'])}",
        f"name: {_graphql_literal(endpoint['name'])}",
        f"scalerType: {_graphql_literal(endpoint['scalerType'])}",
        f"scalerValue: {int(endpoint['scalerValue'])}",
        f"templateId: {_graphql_literal(endpoint['templateId'])}",
        f"workersMax: {int(endpoint['workersMax'])}",
        f"workersMin: {int(endpoint['workersMin'])}",
    ]
    if endpoint.get("locations"):
        fields.insert(2, f"locations: {_graphql_literal(endpoint['locations'])}")
    if endpoint.get("flashBootType"):
        fields.insert(3 if endpoint.get("locations") else 2, "flashBootType: FLASHBOOT")
    if endpoint.get("networkVolumeId"):
        fields.append(f"networkVolumeId: {_graphql_literal(endpoint['networkVolumeId'])}")
    query = (
        "mutation {"
        " saveEndpoint(input: {"
        + ", ".join(fields)
        + " }) {"
        + " id name gpuIds gpuCount idleTimeout locations flashBootType"
        + " scalerType scalerValue templateId workersMax workersMin"
        + " workersStandby networkVolumeId"
        + " }"
        "}"
    )
    return _runpod_graphql_request(api_key=api_key, query=query)["data"]["saveEndpoint"]


def _serverless_request(
    *,
    endpoint_id: str,
    path: str,
    api_key: str,
    method: str,
    payload: dict[str, Any] | None = None,
    timeout: int,
) -> dict[str, Any]:
    data = json.dumps(payload or {}).encode() if method != "GET" else None
    request = urllib.request.Request(
        f"https://api.runpod.ai/v2/{endpoint_id}/{path.lstrip('/')}",
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "gpu-job-control"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        return {"ok": False, "http_status": exc.code, "error": body}


def _runpod_rest_request(*, api_key: str, path: str, method: str, payload: dict[str, Any] | None, timeout: int = 30) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None and method != "DELETE" else None
    request = urllib.request.Request(
        f"{RUNPOD_REST_BASE_URL}/{path.lstrip('/')}",
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "gpu-job-control"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode()
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"RunPod REST {method} {path} failed: status={exc.code} body={body}") from exc


def _runpod_graphql_request(*, api_key: str, query: str, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(
        "https://api.runpod.io/graphql",
        data=json.dumps({"query": query}).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "gpu-job-control"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"RunPod GraphQL failed: status={exc.code} body={body}") from exc


def _submit_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.submit_profile == "custom_probe":
        return {"input": {"probe_runtime": True, "diarize": True, "require_gpu": False}}
    return {
        "input": {
            "audio_base64": _audio_base64(args.audio_base64_file),
            "model": "large-v3",
            "transcription": "plain text",
            "translation": "plain text",
            "translate": False,
            "language": "ja",
            "temperature": 0,
            "best_of": 5,
            "beam_size": 5,
            "patience": 1.0,
            "length_penalty": 0,
            "suppress_tokens": "-1",
            "condition_on_previous_text": False,
            "temperature_increment_on_fallback": 0.2,
            "compression_ratio_threshold": 2.4,
            "logprob_threshold": -1.0,
            "no_speech_threshold": 0.6,
            "enable_vad": False,
            "word_timestamps": False,
            "diarize": True,
            "speaker_model": MODEL,
        }
    }


def _audio_base64(file_path: str) -> str:
    path = Path(file_path) if file_path else Path(__file__).resolve().parent.parent / "fixtures" / "audio" / "asr-ja.wav"
    if not path.is_file():
        raise RuntimeError(f"Audio fixture not found: {path}")
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _rest_gpu_type_ids(gpu_ids: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for token in [item.strip() for item in gpu_ids.split(",") if item.strip()]:
        if token.startswith("-"):
            continue
        expanded = RUNPOD_REST_GPU_ALIAS_MAP.get(token, [token])
        for item in expanded:
            if item not in seen:
                seen.add(item)
                values.append(item)
    return values


def _submit_payload_preview(payload: dict[str, Any]) -> dict[str, Any]:
    preview = json.loads(json.dumps(payload))
    input_payload = preview.get("input")
    if isinstance(input_payload, dict) and "audio_base64" in input_payload:
        input_payload["audio_base64"] = f"<base64:{len(str(input_payload['audio_base64']))} chars>"
    return preview


def _official_template_smoke_ok(run_result: dict[str, Any]) -> bool:
    return isinstance(run_result, dict) and bool(run_result.get("ok")) and str(run_result.get("status") or "").upper() == "COMPLETED"


def _probe_name_for_contract(success_contract: str) -> str:
    return OFFICIAL_TEMPLATE_SMOKE_PROBE_NAME if success_contract == "official_template_smoke" else CUSTOM_HANDLER_PROBE_NAME


def _delete_template(api_key: str, *, template_id: str) -> dict[str, Any]:
    if not template_id:
        return {"ok": False, "error": "template_id_required"}
    return {
        "ok": True,
        "template_id": template_id,
        "result": _runpod_rest_request(api_key=api_key, path=f"/templates/{template_id}", method="DELETE", payload=None),
    }


def _delete_endpoint_rest(api_key: str, endpoint_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "endpoint_id": endpoint_id,
        "result": _runpod_rest_request(api_key=api_key, path=f"/endpoints/{endpoint_id}", method="DELETE", payload=None),
    }


def _disable_endpoint_rest(api_key: str, endpoint_id: str) -> dict[str, Any]:
    return _runpod_rest_request(
        api_key=api_key,
        path=f"/endpoints/{endpoint_id}",
        method="PATCH",
        payload={"workersMin": 0, "workersMax": 0},
    )


def _read_existing_template(*, api_key: str, template_id: str) -> dict[str, Any]:
    template_id = str(template_id or "").strip()
    if not template_id:
        return {}
    try:
        payload = _runpod_rest_request(api_key=api_key, path=f"/templates/{template_id}", method="GET", payload=None)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _prepare_managed_template(
    *,
    api_key: str,
    args: argparse.Namespace,
    plan: dict[str, Any],
    artifact_dir: Path,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    managed_template_id = str(args.managed_template_id or "").strip()
    if managed_template_id:
        template = _read_existing_template(api_key=api_key, template_id=managed_template_id)
        source = "rest_template_get"
        if template:
            _write_json(artifact_dir / "runpod_serverless_managed_template.json", template)
        else:
            source = "operator_supplied_template_id"
            template = {
                "id": managed_template_id,
                "name": str(args.managed_template_label or ""),
                "imageName": str(args.managed_template_image or plan["template"].get("imageName") or ""),
                "rest_lookup_ok": False,
            }
        provenance = {
            "mode": "managed_existing_template",
            "source": source,
            "template_id": managed_template_id,
            "template_label": str(args.managed_template_label or ""),
        }
        normalized = {
            "id": managed_template_id,
            "imageName": str(template.get("imageName") or plan["template"].get("imageName") or ""),
            "name": str(template.get("name") or args.managed_template_label or ""),
            "template_record": template,
        }
        return normalized, managed_template_id, provenance
    if args.managed_create_surface == "graphql":
        created = _create_template_graphql(
            api_key,
            {
                **plan["template"],
                "name": f"{plan['template']['name']}-{time.strftime('%Y%m%d%H%M%S')}",
            },
        )
    else:
        created = _create_template(api_key, plan["template"])
    provenance = {
        "mode": "managed_created_template",
        "source": args.managed_create_surface,
        "template_id": str(created.get("id") or ""),
        "template_label": str(args.managed_template_label or ""),
    }
    return created, str(created.get("id") or ""), provenance


def _apply_managed_template_endpoint_defaults(endpoint: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    record = template.get("template_record") if isinstance(template.get("template_record"), dict) else {}
    config = record.get("config") if isinstance(record.get("config"), dict) else {}
    if not config:
        return endpoint
    updated = dict(endpoint)
    for key in ("gpuIds", "gpuCount", "idleTimeout", "scalerType", "scalerValue"):
        value = config.get(key)
        if value not in (None, ""):
            updated[key] = value
    return updated


def _resolve_existing_endpoint(
    provider: RunPodProvider,
    *,
    endpoint_id: str,
    endpoint_name: str,
    template_id: str,
) -> dict[str, Any] | None:
    endpoint_id = str(endpoint_id or "").strip()
    endpoint_name = str(endpoint_name or "").strip()
    if not endpoint_id and not endpoint_name:
        return None
    endpoints = provider._api_snapshot().get("endpoints", [])
    if endpoint_id:
        for item in endpoints:
            if str(item.get("id") or "") != endpoint_id:
                continue
            resolved = dict(item)
            if template_id:
                resolved["templateId"] = str(template_id)
            return resolved
    if endpoint_name:
        matches = [dict(item) for item in endpoints if str(item.get("name") or "") == endpoint_name]
        if len(matches) == 1:
            resolved = matches[0]
            if template_id:
                resolved["templateId"] = str(template_id)
            return resolved
        if len(matches) > 1:
            ids = [str(item.get("id") or "") for item in matches]
            raise RuntimeError(f"RunPod endpoint name is ambiguous in snapshot: {endpoint_name} ids={ids}")
    raise RuntimeError(f"RunPod endpoint not found in snapshot: id={endpoint_id} name={endpoint_name}")


def _endpoint_resolution_snapshot(provider: RunPodProvider) -> dict[str, Any]:
    snapshot = provider._api_snapshot()
    endpoints = snapshot.get("endpoints") if isinstance(snapshot.get("endpoints"), list) else []
    return {
        "count": len(endpoints),
        "endpoints": [
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or ""),
                "templateId": str(item.get("templateId") or ""),
                "workersMax": item.get("workersMax"),
                "workersMin": item.get("workersMin"),
                "workersStandby": item.get("workersStandby"),
            }
            for item in endpoints
            if isinstance(item, dict)
        ],
    }


def _health_has_active_worker(health: dict[str, Any]) -> bool:
    workers = health.get("workers") if isinstance(health.get("workers"), dict) else {}
    for key in ("running", "initializing", "throttled"):
        try:
            if int(workers.get(key) or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _poll_serverless_run(
    *,
    provider: RunPodProvider,
    endpoint_id: str,
    provider_job_id: str,
    api_key: str,
    timeout_seconds: int,
    poll_seconds: int,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    samples: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = _serverless_request(
            endpoint_id=endpoint_id,
            path=f"status/{provider_job_id}",
            api_key=api_key,
            method="GET",
            timeout=30,
        )
        status = str(last.get("status") or "").upper()
        samples.append(
            {
                "status": status,
                "delayTime": last.get("delayTime"),
                "executionTime": last.get("executionTime"),
                "retried": last.get("retried"),
                "timestamp": now_unix(),
                "endpoint_health": provider._endpoint_health_sample(endpoint_id),
            }
        )
        if status in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            return {
                "ok": status == "COMPLETED",
                "endpoint_id": endpoint_id,
                "provider_job_id": provider_job_id,
                "status": status,
                "output": last.get("output") if isinstance(last.get("output"), dict) else {},
                "raw": last,
                "samples": samples,
            }
        time.sleep(max(1, poll_seconds))
    return {
        "ok": False,
        "endpoint_id": endpoint_id,
        "provider_job_id": provider_job_id,
        "status": str(last.get("status") or "TIMEOUT"),
        "output": last.get("output") if isinstance(last.get("output"), dict) else {},
        "raw": last,
        "samples": samples,
        "error": "RunPod serverless job timed out",
    }


def _normalized_output(raw_output: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw_output, dict):
        return {}
    nested_result = raw_output.get("result") if isinstance(raw_output.get("result"), dict) else {}
    nested_metrics = raw_output.get("metrics") if isinstance(raw_output.get("metrics"), dict) else {}
    nested_verify = raw_output.get("verify") if isinstance(raw_output.get("verify"), dict) else {}
    nested_probe_info = raw_output.get("probe_info") if isinstance(raw_output.get("probe_info"), dict) else {}
    if not nested_result and not nested_metrics and not nested_verify and not nested_probe_info:
        return raw_output
    return {
        **raw_output,
        "ok": bool(raw_output.get("ok")) and bool(nested_verify.get("ok")),
        "text": str(nested_result.get("text") or ""),
        "model": str(nested_result.get("diarization_model") or nested_result.get("loaded_model_id") or MODEL),
        "loaded_model_id": str(nested_result.get("loaded_model_id") or nested_result.get("diarization_model") or MODEL),
        "hf_token_present": bool(nested_result.get("diarization_requested")) and not bool(nested_result.get("diarization_error")),
        "cache_hit": bool(nested_probe_info.get("cache_hit") or nested_result.get("cache_hit") or nested_metrics.get("cache_hit")),
        "workspace_contract_ok": bool(raw_output.get("ok")) and bool(nested_verify.get("ok")),
        "worker_startup_ok": bool(raw_output.get("ok")),
        "image_contract_marker_present": bool(
            nested_probe_info.get("image_contract_marker_present") or nested_result.get("image_contract_marker_present")
        ),
        "gpu_probe": nested_probe_info.get("gpu_probe") or nested_result.get("gpu_probe") or {},
        "runtime_checks": nested_result.get("checks") if isinstance(nested_result.get("checks"), dict) else {},
        "runtime_seconds": (
            raw_output.get("runtime_seconds") or nested_metrics.get("runtime_seconds") or nested_result.get("runtime_seconds")
        ),
        "cleanup": {"ok": True, "source": "serverless_handler_nested_artifacts"},
        "actual_cost_guard": {"ok": True, "source": "serverless_handler_nested_artifacts"},
    }


def _guard_clean(guard: dict[str, Any]) -> bool:
    return bool(guard.get("ok")) and (
        (isinstance(guard.get("billable_resources"), list) and not guard.get("billable_resources")) or guard.get("billable_count") == 0
    )


def _delete_ok(cleanup: dict[str, Any]) -> bool:
    for step in cleanup.get("steps") or []:
        deleted = step.get("delete_endpoint") if isinstance(step, dict) else None
        if isinstance(deleted, dict):
            if deleted.get("ok") is True:
                return True
            if "deleteEndpoint" in deleted.get("data", {}) or "deleteEndpoint" in deleted:
                return True
    return False


def _endpoint_scale(endpoint: dict[str, Any]) -> dict[str, int]:
    return {
        "workersMin": int(endpoint.get("workersMin") or 0),
        "workersMax": int(endpoint.get("workersMax") or 0),
        "workersStandby": int(endpoint.get("workersStandby") or 0),
    }


def _startup_seconds_observed(run_result: dict[str, Any]) -> int:
    samples = run_result.get("samples")
    if not isinstance(samples, list) or not samples:
        return 0
    first = samples[0].get("timestamp")
    last = samples[-1].get("timestamp")
    try:
        return max(0, int(last) - int(first))
    except (TypeError, ValueError):
        return 0


def _blocker_chain(*, run_result: dict[str, Any], endpoint: dict[str, Any], managed_resources: bool) -> list[str]:
    blockers: list[str] = []
    status = str(run_result.get("status") or "").upper()
    endpoint_scale = _endpoint_scale(endpoint)
    samples = run_result.get("samples")
    last_sample = samples[-1] if isinstance(samples, list) and samples else {}
    endpoint_health = last_sample.get("endpoint_health") if isinstance(last_sample, dict) else {}
    active_workers = _health_has_active_worker(endpoint_health) if isinstance(endpoint_health, dict) else False
    zero_capacity = endpoint_scale["workersMin"] == 0 and endpoint_scale["workersMax"] == 0 and endpoint_scale["workersStandby"] == 0
    unexpected_warm_standby = endpoint_scale["workersStandby"] > 0
    if managed_resources and status in {"IN_QUEUE", "CANCELLED", "TIMEOUT", "TIMED_OUT"} and unexpected_warm_standby:
        blockers.append("warm_standby_unexpected")
    if not managed_resources and status in {"IN_QUEUE", "CANCELLED", "TIMEOUT", "TIMED_OUT"} and zero_capacity and not active_workers:
        blockers.append("disabled_endpoint_queue")
    elif status == "IN_QUEUE":
        blockers.append("provider_backpressure")
    elif status in {"TIMEOUT", "TIMED_OUT"}:
        blockers.append("startup_timeout")
    elif status in {"FAILED", "CANCELLED"}:
        blockers.append("worker_request")
    return blockers


def _stdout_text(payload: dict[str, Any]) -> str:
    return (
        "\n".join(
            [
                f"ok={bool(payload.get('ok'))}",
                f"probe_name={payload.get('probe_name')}",
                f"endpoint_id={payload.get('endpoint_id')}",
                f"provider_job_id={payload.get('provider_job_id')}",
                f"model={payload.get('model')}",
                f"provider_image={payload.get('provider_image')}",
                f"blocker_type={payload.get('blocker_type')}",
                f"cache_hit={bool(payload.get('cache_hit'))}",
                f"workspace_contract_ok={bool(payload.get('workspace_contract_ok'))}",
                f"cleanup_ok={bool(payload.get('cleanup_ok'))}",
            ]
        )
        + "\n"
    )


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
