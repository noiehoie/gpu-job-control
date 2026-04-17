from __future__ import annotations

from pathlib import Path
from shutil import which
from subprocess import run
from typing import Any
import json
import os
import sys
import time
import tomllib
import urllib.error
import urllib.request

from gpu_job.models import Job, now_unix
from gpu_job.policy import load_execution_policy
from gpu_job.providers.base import Provider
from gpu_job.store import JobStore
from gpu_job.verify import verify_artifacts


def runpod_bin() -> str | None:
    candidates = [which("runpod"), str(Path.home() / ".local" / "bin" / "runpod")]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def runpod_python() -> str | None:
    configured = os.getenv("RUNPOD_PYTHON", "").strip()
    candidates = [configured, sys.executable]
    for candidate in candidates:
        if not candidate:
            continue
        proc = run([candidate, "-c", "import runpod"], capture_output=True, text=True, timeout=10)
        if proc.returncode == 0:
            return candidate
    return None


def _graphql_string(value: str) -> str:
    return json.dumps(value)


def _runpod_api_key() -> str:
    configured = os.getenv("RUNPOD_API_KEY", "").strip()
    if configured:
        return configured
    config = Path.home() / ".runpod" / "config.toml"
    if not config.is_file():
        return ""
    try:
        data = tomllib.loads(config.read_text(encoding="utf-8"))
    except Exception:
        return ""
    default = data.get("default")
    if isinstance(default, dict):
        return str(default.get("api_key") or "").strip()
    return ""


class RunPodProvider(Provider):
    name = "runpod"

    def doctor(self) -> dict[str, Any]:
        binary = runpod_bin()
        config = Path.home() / ".runpod" / "config.toml"
        ok_binary = bool(binary and Path(binary).exists())
        ok_config = config.is_file() and config.stat().st_size > 0
        help_ok = False
        if ok_binary:
            proc = run([binary, "pod", "--help"], capture_output=True, text=True, timeout=20)
            help_ok = proc.returncode == 0 and "create" in proc.stdout
        return {
            "provider": self.name,
            "ok": ok_binary and ok_config and help_ok,
            "binary": binary if ok_binary else "",
            "config_present": ok_config,
            "help_ok": help_ok,
        }

    def signal(self, profile: dict[str, Any]) -> dict[str, Any]:
        health = self.doctor()
        signal: dict[str, Any] = {
            "provider": self.name,
            "healthy": bool(health.get("ok")),
            "available": bool(health.get("ok")),
            "reason": "healthy" if health.get("ok") else "provider health check failed",
            "health": health,
            "active_jobs": None,
            "capacity_hint": "unknown",
            "estimated_startup_seconds": None,
            "offer_count": None,
            "cheapest_offer": None,
            "estimated_max_runtime_cost_usd": None,
        }
        if not health.get("ok"):
            return signal
        binary = runpod_bin()
        if not binary:
            signal["available"] = False
            signal["reason"] = "runpod binary not found"
            return signal
        proc = run([binary, "pod", "list"], capture_output=True, text=True, timeout=45)
        signal["pod_list_ok"] = proc.returncode == 0
        if proc.returncode != 0:
            signal["available"] = False
            signal["reason"] = proc.stderr.strip() or "runpod pod list failed"
            return signal
        rows = [
            line
            for line in proc.stdout.splitlines()
            if line.startswith("|") and " ID " not in line and not set(line.strip()) <= {"|", "+", "-", " "}
        ]
        endpoint_data = self._api_snapshot()
        endpoints = endpoint_data.get("endpoints") or []
        endpoint_health = self._endpoint_health(endpoints)
        warm_endpoints = [
            {
                "id": endpoint.get("id"),
                "name": endpoint.get("name"),
                "workersMin": endpoint.get("workersMin"),
                "workersStandby": endpoint.get("workersStandby"),
                "workersMax": endpoint.get("workersMax"),
                "gpuCount": endpoint.get("gpuCount"),
                "idleTimeout": endpoint.get("idleTimeout"),
            }
            for endpoint in endpoints
            if int(endpoint.get("workersMin") or 0) > 0 or int(endpoint.get("workersStandby") or 0) > 0
        ]
        signal["endpoint_count"] = len(endpoints)
        signal["endpoint_health"] = endpoint_health
        signal["external_queue_depth"] = sum(int((item.get("jobs") or {}).get("inQueue") or 0) for item in endpoint_health)
        signal["external_in_progress"] = sum(int((item.get("jobs") or {}).get("inProgress") or 0) for item in endpoint_health)
        signal["warm_endpoint_count"] = len(warm_endpoints)
        signal["warm_endpoints"] = warm_endpoints
        signal["active_jobs"] = len(rows) + len(warm_endpoints) + signal["external_in_progress"]
        signal["capacity_hint"] = self._capacity_hint(len(rows), len(endpoints), len(warm_endpoints))
        signal["estimated_startup_seconds"] = 30 if rows or warm_endpoints else 180
        max_startup = int(profile.get("max_startup_seconds", 0) or 0)
        if max_startup and signal["estimated_startup_seconds"] > max_startup:
            signal["startup_exceeds_profile_limit"] = True
            signal["reason"] = "healthy; pod list readable; cold-start estimate exceeds profile max_startup_seconds"
        else:
            signal["reason"] = "healthy; pod list readable"
        return signal

    def _endpoint_health(self, endpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
        python_bin = runpod_python()
        if not python_bin:
            return [{"ok": False, "error": "runpod python SDK not importable; install gpu-job-control[providers] or set RUNPOD_PYTHON"}]
        rows = []
        for endpoint in endpoints:
            endpoint_id = str(endpoint.get("id") or "")
            if not endpoint_id:
                continue
            proc = run(
                [
                    python_bin,
                    "-c",
                    ("import json,runpod,sys; endpoint=runpod.Endpoint(sys.argv[1]); print(json.dumps(endpoint.health()))"),
                    endpoint_id,
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if proc.returncode != 0:
                rows.append({"id": endpoint_id, "ok": False, "error": proc.stderr.strip() or proc.stdout.strip()})
                continue
            try:
                health = json.loads(proc.stdout)
            except json.JSONDecodeError:
                health = {"raw": proc.stdout.strip()}
            rows.append({"id": endpoint_id, "name": endpoint.get("name"), "ok": True, **health})
        return rows

    def _api_snapshot(self) -> dict[str, Any]:
        try:
            python_bin = runpod_python()
            if not python_bin:
                return {}
            proc = run(
                [
                    python_bin,
                    "-c",
                    (
                        "import json,runpod; "
                        "print(json.dumps({'pods': runpod.get_pods(), "
                        "'endpoints': runpod.get_endpoints(), "
                        "'user': runpod.get_user()}))"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=45,
            )
            return json.loads(proc.stdout) if proc.returncode == 0 and proc.stdout.strip() else {}
        except Exception:
            return {}

    def quarantine_public_ollama_endpoint(
        self,
        *,
        model: str = "llama3.2:1b",
        gpu_ids: str = "AMPERE_24,ADA_24",
        network_volume_id: str = "",
        locations: str = "US",
        template_id: str = "",
        flashboot: bool = False,
    ) -> dict[str, Any]:
        created = self._create_public_ollama_endpoint(
            model=model,
            gpu_ids=gpu_ids,
            network_volume_id=network_volume_id,
            locations=locations,
            template_id=template_id,
            flashboot=flashboot,
        )
        created_template = created.get("created_template")
        endpoint = created["endpoint"]
        template_id = str(endpoint["templateId"])
        endpoint_id = str(endpoint["id"])
        guard = self.cost_guard()
        endpoint_billable = [item for item in guard.get("billable_resources", []) if str(item.get("id") or "") == endpoint_id]
        deleted = None
        disabled = None
        accepted = not endpoint_billable and bool(guard.get("ok"))
        if not accepted:
            disabled = self._disable_endpoint(endpoint_id, template_id=template_id)
            deleted = self._delete_endpoint(endpoint_id)
        post_guard = self.cost_guard()
        return {
            "ok": accepted and bool(post_guard.get("ok")),
            "quarantine_version": "runpod-public-endpoint-quarantine-v1",
            "model": model,
            "flashboot": flashboot,
            "created_template": created_template,
            "endpoint": endpoint,
            "guard": guard,
            "endpoint_billable_resources": endpoint_billable,
            "disabled": disabled,
            "deleted": deleted,
            "post_guard": post_guard,
            "decision": "accepted" if accepted else "deleted_due_to_warm_capacity_or_guard_failure",
        }

    def _create_public_ollama_endpoint(
        self,
        *,
        model: str,
        gpu_ids: str,
        network_volume_id: str,
        locations: str,
        template_id: str,
        flashboot: bool,
    ) -> dict[str, Any]:
        created_template = None
        if not template_id:
            template_name = f"gpu-job-public-ollama-{_safe_name(model)}-{time.strftime('%Y%m%d%H%M%S')}"
            template_query = (
                "mutation {"
                " saveTemplate(input: {"
                f" name: {_graphql_string(template_name)},"
                ' imageName: "svenbrnn/runpod-ollama:latest",'
                " isServerless: true,"
                " containerDiskInGb: 30,"
                f' env: [{{ key: "OLLAMA_MODEL_NAME", value: {_graphql_string(model)} }}]'
                " }) { id name imageName isServerless env { key value } }"
                "}"
            )
            created_template = self._run_graphql(template_query)["data"]["saveTemplate"]
            template_id = str(created_template["id"])
        endpoint_name = f"gpu-job-public-ollama-{_safe_name(model)}-{time.strftime('%Y%m%d%H%M%S')}"
        flashboot_line = "    flashBootType: FLASHBOOT,\n" if flashboot else ""
        endpoint_query = f"""
mutation {{
  saveEndpoint(input: {{
    gpuIds: {_graphql_string(gpu_ids)},
    idleTimeout: 15,
    locations: {_graphql_string(locations)},
    name: {_graphql_string(endpoint_name)},
{flashboot_line}    scalerType: {_graphql_string("QUEUE_DELAY")},
    scalerValue: 4,
    templateId: {_graphql_string(template_id)},
    workersMax: 1,
    workersMin: 0,
    networkVolumeId: {_graphql_string(network_volume_id)}
  }}) {{
    id name gpuIds idleTimeout locations flashBootType
    scalerType scalerValue templateId
    workersMax workersMin workersStandby networkVolumeId
  }}
}}
"""
        endpoint = self._run_graphql(endpoint_query)["data"]["saveEndpoint"]
        return {"created_template": created_template, "endpoint": endpoint}

    def _disable_endpoint(self, endpoint_id: str, *, template_id: str) -> dict[str, Any]:
        query = (
            "mutation {"
            " saveEndpoint(input: {"
            f" id: {_graphql_string(endpoint_id)},"
            ' name: "gpu-job-disabled",'
            ' gpuIds: "AMPERE_24",'
            f" templateId: {_graphql_string(template_id)},"
            " workersMax: 0,"
            " workersMin: 0"
            " }) { id workersMax workersMin }"
            "}"
        )
        return self._run_graphql(query)

    def _delete_endpoint(self, endpoint_id: str) -> dict[str, Any]:
        return self._run_graphql(f"mutation {{ deleteEndpoint(id: {_graphql_string(endpoint_id)}) }}")

    def _run_graphql(self, query: str) -> dict[str, Any]:
        api_key = _runpod_api_key()
        if not api_key:
            raise RuntimeError("RUNPOD_API_KEY or ~/.runpod/config.toml default.api_key is required for RunPod GraphQL operations")
        base_url = os.getenv("RUNPOD_API_BASE_URL", "https://api.runpod.io").rstrip("/")
        request = urllib.request.Request(
            f"{base_url}/graphql",
            data=json.dumps({"query": query}).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "gpu-job-control",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            raise RuntimeError(f"runpod graphql http {exc.code}: {body}") from exc
        if payload.get("errors"):
            raise RuntimeError(f"runpod graphql error: {payload['errors']}")
        return payload

    def _capacity_hint(self, pod_count: int, endpoint_count: int, warm_endpoint_count: int) -> str:
        if pod_count:
            return f"{pod_count} listed pod(s)"
        if warm_endpoint_count:
            return f"{warm_endpoint_count} warm serverless endpoint(s)"
        if endpoint_count:
            return f"{endpoint_count} serverless endpoint(s), none warm"
        return "no active pods or serverless endpoints"

    def _persistent_storage_guard(self, volumes: list[dict[str, Any]]) -> dict[str, Any]:
        policy = load_execution_policy()
        storage_policy = dict(policy.get("persistent_storage", {}).get("runpod", {}))
        allowed = storage_policy.get("allowed_network_volumes", []) or []
        price = float(storage_policy.get("network_volume_price_usd_per_gb_month") or 0.07)
        warning_monthly = float(storage_policy.get("warning_monthly_usd") or 0)
        fail_monthly = float(storage_policy.get("fail_monthly_usd") or 0)
        allowed_by_id = {str(item.get("id")): item for item in allowed if item.get("id")}
        allowed_resources = []
        unknown_resources = []
        size_mismatch_resources = []
        for volume in volumes:
            volume_id = str(volume.get("id") or "")
            size_gb = float(volume.get("size_gb") or 0)
            estimated_monthly = size_gb * price
            enriched = dict(volume)
            enriched["estimated_monthly_usd"] = round(estimated_monthly, 4)
            allowed_item = allowed_by_id.get(volume_id)
            if not allowed_item:
                unknown_resources.append(enriched)
                continue
            allowed_size = float(allowed_item.get("size_gb") or 0)
            enriched["allowed_size_gb"] = allowed_size
            enriched["purpose"] = allowed_item.get("purpose", "")
            if allowed_size and size_gb > allowed_size:
                size_mismatch_resources.append(enriched)
                continue
            allowed_resources.append(enriched)
        total_gb = sum(float(volume.get("size_gb") or 0) for volume in volumes)
        monthly = total_gb * price
        ok = not unknown_resources and not size_mismatch_resources and (not fail_monthly or monthly <= fail_monthly)
        warning = bool(warning_monthly and monthly > warning_monthly)
        if unknown_resources:
            reason = "unknown RunPod persistent volume present"
        elif size_mismatch_resources:
            reason = "RunPod persistent volume exceeds approved size"
        elif fail_monthly and monthly > fail_monthly:
            reason = "RunPod persistent storage monthly estimate exceeds failure budget"
        elif warning:
            reason = "RunPod persistent storage monthly estimate exceeds warning budget"
        else:
            reason = "RunPod persistent storage is within approved fixed-cost budget"
        return {
            "ok": ok,
            "warning": warning,
            "reason": reason,
            "price_usd_per_gb_month": price,
            "total_gb": total_gb,
            "estimated_monthly_usd": round(monthly, 4),
            "allowed_monthly_usd": storage_policy.get("allowed_monthly_usd"),
            "warning_monthly_usd": warning_monthly,
            "fail_monthly_usd": fail_monthly,
            "allowed_persistent_resources": allowed_resources,
            "unknown_persistent_resources": unknown_resources,
            "size_mismatch_persistent_resources": size_mismatch_resources,
        }

    def cost_guard(self) -> dict[str, Any]:
        health = self.doctor()
        if not health.get("ok"):
            return {
                "provider": self.name,
                "ok": False,
                "billable_resources": [],
                "estimated_hourly_usd": 0.0,
                "reason": "runpod health check failed",
                "health": health,
            }
        try:
            data = self._api_snapshot()
        except Exception:
            data = {}
        pods = data.get("pods") or []
        endpoints = data.get("endpoints") or []
        user = data.get("user") or {}
        endpoint_health = self._endpoint_health(endpoints)
        billable = [
            {
                "id": pod.get("id"),
                "name": pod.get("name"),
                "desiredStatus": pod.get("desiredStatus"),
                "costPerHr": pod.get("costPerHr"),
                "gpuCount": pod.get("gpuCount"),
                "imageName": pod.get("imageName"),
            }
            for pod in pods
        ]
        warm_endpoints = []
        for endpoint in endpoints:
            workers_min = int(endpoint.get("workersMin") or 0)
            workers_standby = int(endpoint.get("workersStandby") or 0)
            if workers_min > 0 or workers_standby > 0:
                warm_endpoints.append(
                    {
                        "type": "serverless_endpoint_warm_capacity",
                        "id": endpoint.get("id"),
                        "name": endpoint.get("name"),
                        "workersMin": workers_min,
                        "workersStandby": workers_standby,
                        "workersMax": endpoint.get("workersMax"),
                        "idleTimeout": endpoint.get("idleTimeout"),
                        "gpuCount": endpoint.get("gpuCount"),
                    }
                )
        billable.extend(warm_endpoints)
        persistent_resources = [
            {
                "type": "network_volume",
                "id": volume.get("id"),
                "name": volume.get("name"),
                "size_gb": volume.get("size"),
                "dataCenterId": volume.get("dataCenterId"),
            }
            for volume in user.get("networkVolumes", [])
        ]
        storage_guard = self._persistent_storage_guard(persistent_resources)
        hourly = sum(float(pod.get("costPerHr") or 0) for pod in billable)
        ok = not billable and storage_guard["ok"]
        if billable:
            reason = "RunPod active pods or warm serverless workers present"
        elif not storage_guard["ok"]:
            reason = storage_guard["reason"]
        else:
            reason = "no RunPod active pods or warm serverless workers; persistent storage within approved fixed-cost budget"
        return {
            "provider": self.name,
            "ok": ok,
            "billable_resources": billable,
            "persistent_resources": persistent_resources,
            "persistent_storage": storage_guard,
            "serverless_queue": endpoint_health,
            "estimated_hourly_usd": hourly,
            "reason": reason,
        }

    def plan(self, job: Job) -> dict[str, Any]:
        snapshot = self._api_snapshot()
        endpoints = snapshot.get("endpoints") or []
        llm_endpoint = self._llm_endpoint(endpoints)
        serverless_plan = {
            "mode": "serverless plan",
            "create_endpoint_function": "runpod.create_endpoint",
            "worker_contract": {
                "dockerfile": "docker/asr-worker.Dockerfile",
                "entrypoint": "gpu-job-asr-worker",
                "artifact_contract": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
                "command_template": [
                    "serverless endpoint receives input.prompt, input.system_prompt, input.model, input.max_tokens",
                    "response must include text/response/output/generated_text/answer or OpenAI-style choices",
                ],
            },
            "recommended_args": {
                "name": f"gpu-job-{job.job_type}-{job.gpu_profile}",
                "template_id": "<template-id-required>",
                "gpu_ids": "<gpu-type-id-required>",
                "workers_min": 0,
                "workers_max": 1,
                "idle_timeout": 5,
                "scaler_type": "QUEUE_DELAY",
                "scaler_value": 4,
                "flashboot": True,
                "gpu_count": 1,
            },
            "cost_guard_policy": [
                "workers_min must remain 0 unless user explicitly chooses paid warm capacity.",
                "workers_standby must remain 0 unless user explicitly chooses paid warm capacity.",
                "gpu-job guard blocks execution if warm capacity is detected.",
            ],
            "existing_endpoints": [
                {
                    "id": endpoint.get("id"),
                    "name": endpoint.get("name"),
                    "workersMin": endpoint.get("workersMin"),
                    "workersStandby": endpoint.get("workersStandby"),
                    "workersMax": endpoint.get("workersMax"),
                    "idleTimeout": endpoint.get("idleTimeout"),
                    "gpuCount": endpoint.get("gpuCount"),
                    "templateId": endpoint.get("templateId"),
                    "networkVolumeId": endpoint.get("networkVolumeId"),
                }
                for endpoint in endpoints
            ],
            "selected_llm_endpoint": _public_endpoint(llm_endpoint) if llm_endpoint else None,
        }
        return {
            "provider": self.name,
            "job_id": job.job_id,
            "mode": "pod/serverless plan",
            "worker_image": job.worker_image,
            "gpu_profile": job.gpu_profile,
            "input_uri": job.input_uri,
            "output_uri": job.output_uri,
            "serverless": serverless_plan,
            "public_endpoint_candidates": [
                {
                    "name": "RunPod official worker-vllm",
                    "image": "runpod/worker-v1-vllm:<version>",
                    "best_for": ["llm_heavy", "openai_compatible_chat", "high-throughput text generation"],
                    "notes": [
                        "Use OpenAI-compatible endpoint path when deployed.",
                        "Prefer this over bespoke workers for vLLM-compatible models.",
                    ],
                },
                {
                    "name": "SvenBrnn/runpod-worker-ollama",
                    "image": "svenbrnn/runpod-ollama:latest",
                    "hub_url": "https://console.runpod.io/hub/SvenBrnn/runpod-worker-ollama",
                    "best_for": ["ollama-compatible llm_heavy", "fast reuse of public worker template"],
                    "required_env": ["OLLAMA_MODEL_NAME"],
                    "notes": [
                        "Attach an approved network volume so models are cached across cold starts.",
                        "Use workersMin=0 and workersStandby=0 unless paid warm capacity is explicitly accepted.",
                    ],
                },
                {
                    "name": "RunPod Ollama tutorial image",
                    "image": "pooyaharatian/runpod-ollama:0.0.8",
                    "best_for": ["ollama-compatible endpoint canary"],
                    "required_env": ["MODEL_NAME"],
                    "notes": [
                        "Official RunPod documentation recommends network volume caching to reduce repeated model downloads.",
                    ],
                },
            ],
            "notes": [
                "RunPod execute uses an existing serverless endpoint when one is configured or discoverable.",
                "gpu-job-control does not create paid pods automatically.",
                "Serverless endpoint creation requires a concrete template_id and gpu_ids.",
                "Use workers_min=0 and workers_standby=0 unless paid warm capacity is explicitly intended.",
                "Worker must write result.json, metrics.json, verify.json, stdout.log, stderr.log.",
            ],
        }

    def submit(self, job: Job, store: JobStore, execute: bool = False) -> Job:
        job.provider = self.name
        job.provider_job_id = ""
        job.metadata["provider_plan"] = self.plan(job)
        job.metadata["execute_requested"] = execute
        if not execute:
            job.status = "planned"
            store.save(job)
            return job
        if job.job_type != "llm_heavy":
            job.status = "failed"
            job.error = "runpod execute currently supports llm_heavy jobs only"
            job.exit_code = 2
            store.save(job)
            return job
        endpoint = self._llm_endpoint((self._api_snapshot().get("endpoints") or []))
        if not endpoint:
            job.status = "failed"
            job.error = "no RunPod llm_heavy endpoint configured"
            job.exit_code = 2
            store.save(job)
            return job
        artifact_dir = store.artifact_dir(job.job_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        start = now_unix()
        job.started_at = start
        job.status = "running"
        store.save(job)
        stdout = ""
        stderr = ""
        try:
            output = self._run_llm_endpoint(endpoint, job)
            text = _extract_text(output)
            result = {
                "job_id": job.job_id,
                "job_type": job.job_type,
                "provider": self.name,
                "endpoint_id": endpoint.get("id"),
                "provider_job_id": output.get("id") if isinstance(output, dict) else None,
                "model": job.model,
                "text": text,
                "raw_output": output,
            }
            stdout = f"runpod llm completed endpoint={endpoint.get('id')} chars={len(text)}\n"
            if not text:
                raise RuntimeError(
                    f"RunPod endpoint returned no text; output keys={list(output) if isinstance(output, dict) else type(output).__name__}"
                )
        except Exception as exc:
            result = {"job_id": job.job_id, "job_type": job.job_type, "provider": self.name, "text": "", "error": str(exc)}
            stderr = str(exc)
            job.error = stderr
            job.exit_code = 1
        (artifact_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        metrics = {
            "job_id": job.job_id,
            "job_type": job.job_type,
            "provider": self.name,
            "endpoint_id": endpoint.get("id"),
            "runtime_seconds": max(0, now_unix() - start),
            "text_chars": len(result.get("text") or ""),
        }
        (artifact_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        (artifact_dir / "stdout.log").write_text(stdout)
        (artifact_dir / "stderr.log").write_text(stderr)
        (artifact_dir / "verify.json").write_text("{}\n")
        verify = verify_artifacts(artifact_dir)
        (artifact_dir / "verify.json").write_text(json.dumps(verify, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        job.finished_at = now_unix()
        job.runtime_seconds = max(0, job.finished_at - start)
        job.artifact_count = verify["artifact_count"]
        job.artifact_bytes = verify["artifact_bytes"]
        if not job.error and verify["ok"] and result.get("text"):
            job.status = "succeeded"
            job.exit_code = 0
        else:
            job.status = "failed"
            if job.exit_code is None:
                job.exit_code = 1
        store.save(job)
        return job

    def _llm_endpoint(self, endpoints: list[dict[str, Any]]) -> dict[str, Any] | None:
        configured = os.getenv("RUNPOD_LLM_ENDPOINT_ID", "").strip()
        if configured:
            for endpoint in endpoints:
                if str(endpoint.get("id")) == configured:
                    return endpoint
            return {"id": configured, "name": "RUNPOD_LLM_ENDPOINT_ID", "mode": os.getenv("RUNPOD_LLM_ENDPOINT_MODE", "").strip()}
        for endpoint in endpoints:
            name = str(endpoint.get("name") or "").lower()
            if "llm" in name:
                return endpoint
        return endpoints[0] if endpoints else None

    def _run_llm_endpoint(self, endpoint: dict[str, Any], job: Job) -> Any:
        if endpoint.get("mode") == "openai":
            return self._run_openai_llm_endpoint(endpoint, job)
        python_bin = runpod_python()
        if not python_bin:
            raise RuntimeError("runpod python SDK not importable; install gpu-job-control[providers] or set RUNPOD_PYTHON")
        queue_timeout = _int_from_metadata(job, "max_queue_seconds", default=0)
        if not queue_timeout:
            queue_timeout = _int_from_metadata(job, "max_startup_seconds", default=0)
        if not queue_timeout:
            queue_timeout = 300
        payload = {
            "endpoint_id": endpoint.get("id"),
            "input": _llm_input(job),
            "timeout": int(job.limits.get("max_runtime_minutes", 10)) * 60,
            "queue_timeout": queue_timeout,
        }
        proc = run(
            [
                python_bin,
                "-c",
                (
                    "import json,runpod,sys,time; "
                    "p=json.loads(sys.stdin.read()); "
                    "e=runpod.Endpoint(p['endpoint_id']); "
                    "j=e.run({'input': p['input'], 'policy': {"
                    "'executionTimeout': int(p['timeout']*1000), "
                    "'ttl': int(max(p['timeout'], p['queue_timeout'])*1000)}}); "
                    "start=time.time(); last={'id': j.job_id, 'status': 'SUBMITTED'}; "
                    "\nwhile True:\n"
                    "    status=j.status(); last={'id': j.job_id, 'status': status}\n"
                    "    if status == 'COMPLETED':\n"
                    "        output=j.output(); "
                    "print(json.dumps({'id': j.job_id, 'status': status, 'output': output}, ensure_ascii=False)); break\n"
                    "    if status in {'FAILED', 'ERROR', 'CANCELLED', 'TIMED_OUT'}:\n"
                    "        print(json.dumps({'id': j.job_id, 'status': status, 'output': j.output()}, ensure_ascii=False)); break\n"
                    "    if status == 'IN_QUEUE' and time.time() - start > p['queue_timeout']:\n"
                    "        cancel=j.cancel(timeout=10); "
                    "print(json.dumps({'id': j.job_id, 'status': 'QUEUE_TIMEOUT_CANCELLED', "
                    "'cancel': cancel, 'last': last}, ensure_ascii=False)); break\n"
                    "    if time.time() - start > p['timeout']:\n"
                    "        cancel=j.cancel(timeout=10); "
                    "print(json.dumps({'id': j.job_id, 'status': 'EXECUTION_TIMEOUT_CANCELLED', "
                    "'cancel': cancel, 'last': last}, ensure_ascii=False)); break\n"
                    "    time.sleep(5)"
                ),
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=payload["timeout"] + 60,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "runpod endpoint failed")
        output = json.loads(proc.stdout)
        status = str(output.get("status") or "")
        if status not in {"COMPLETED"}:
            raise RuntimeError(f"RunPod endpoint job did not complete: {json.dumps(output, ensure_ascii=False, sort_keys=True)}")
        return output

    def _run_openai_llm_endpoint(self, endpoint: dict[str, Any], job: Job) -> Any:
        api_key = _runpod_api_key()
        if not api_key:
            raise RuntimeError("RunPod OpenAI-compatible endpoint requires RunPod API key")
        endpoint_id = str(endpoint.get("id") or "").strip()
        if not endpoint_id:
            raise RuntimeError("RunPod OpenAI-compatible endpoint id is required")
        payload = _openai_chat_payload(job)
        request = urllib.request.Request(
            f"https://api.runpod.ai/v2/{endpoint_id}/openai/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "gpu-job-control",
            },
            method="POST",
        )
        timeout = max(30, min(int(job.limits.get("max_runtime_minutes", 10)) * 60, 600))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                output = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            raise RuntimeError(f"runpod openai endpoint http {exc.code}: {body}") from exc
        return {"id": endpoint_id, "status": "COMPLETED", "output": output}


def _public_endpoint(endpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": endpoint.get("id"),
        "name": endpoint.get("name"),
        "workersMin": endpoint.get("workersMin"),
        "workersStandby": endpoint.get("workersStandby"),
        "workersMax": endpoint.get("workersMax"),
        "gpuCount": endpoint.get("gpuCount"),
        "templateId": endpoint.get("templateId"),
        "networkVolumeId": endpoint.get("networkVolumeId"),
    }


def _safe_name(value: str) -> str:
    out = []
    for ch in value.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in {".", "-", "_", ":"}:
            out.append("-")
    name = "".join(out).strip("-")
    return name[:48] or "model"


def _llm_input(job: Job) -> dict[str, Any]:
    payload = job.metadata.get("input")
    payload = payload if isinstance(payload, dict) else {}
    prompt = str(payload.get("prompt") or job.input_uri.removeprefix("text://"))
    system_prompt = str(payload.get("system_prompt") or "")
    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "prompt": prompt,
        "system_prompt": system_prompt,
        "model": job.model,
        "max_tokens": int(payload.get("max_tokens") or 1024),
    }


def _openai_chat_payload(job: Job) -> dict[str, Any]:
    data = _llm_input(job)
    messages = []
    if data["system_prompt"]:
        messages.append({"role": "system", "content": data["system_prompt"]})
    messages.append({"role": "user", "content": data["prompt"]})
    return {
        "model": os.getenv("RUNPOD_LLM_MODEL_OVERRIDE", "").strip() or data["model"],
        "messages": messages,
        "max_tokens": data["max_tokens"],
        "temperature": 0,
    }


def _int_from_metadata(job: Job, key: str, default: int = 0) -> int:
    routing = job.metadata.get("routing")
    routing = routing if isinstance(routing, dict) else {}
    for value in (routing.get(key), job.metadata.get(key), job.limits.get(key)):
        try:
            if value is not None and value != "":
                return int(float(value))
        except (TypeError, ValueError):
            continue
    return default


def _extract_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        for key in ("text", "response", "output", "generated_text", "answer"):
            value = output.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, (dict, list)):
                nested = _extract_text(value)
                if nested:
                    return nested
        choices = output.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return message["content"]
                if isinstance(first.get("text"), str):
                    return first["text"]
    if isinstance(output, list):
        for item in output:
            nested = _extract_text(item)
            if nested:
                return nested
    return ""
