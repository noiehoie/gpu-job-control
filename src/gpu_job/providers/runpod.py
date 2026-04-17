from __future__ import annotations

from pathlib import Path
from shutil import which
from subprocess import run
from typing import Any
import base64
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


RUNPOD_GPU_POOL_IDS = {
    "AMPERE_16",
    "AMPERE_24",
    "ADA_24",
    "AMPERE_48",
    "ADA_48_PRO",
    "AMPERE_80",
    "ADA_80_PRO",
    "HOPPER_141",
    "ADA_32_PRO",
    "BLACKWELL_96",
    "BLACKWELL_180",
}


def _validate_runpod_gpu_ids(gpu_ids: str) -> dict[str, Any]:
    tokens = [item.strip() for item in gpu_ids.split(",") if item.strip()]
    pool_ids = [item for item in tokens if not item.startswith("-")]
    excluded_gpu_types = [item[1:].strip() for item in tokens if item.startswith("-") and item[1:].strip()]
    invalid_pool_ids = [item for item in pool_ids if item not in RUNPOD_GPU_POOL_IDS]
    ok = bool(pool_ids) and not invalid_pool_ids and len(tokens) == len(pool_ids) + len(excluded_gpu_types)
    return {
        "ok": ok,
        "input": gpu_ids,
        "pool_ids": pool_ids,
        "excluded_gpu_types": excluded_gpu_types,
        "invalid_pool_ids": invalid_pool_ids,
        "valid_pool_ids": sorted(RUNPOD_GPU_POOL_IDS),
        "rule": "gpuIds accepts RunPod GPU pool IDs; concrete GPU type names are only valid as exclusions prefixed with '-'",
    }


RUNPOD_DEFAULT_POD_GPU_TYPE_ID = "NVIDIA GeForce RTX 3090"
RUNPOD_DEFAULT_POD_IMAGE = "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404"


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
            if int(endpoint.get("workersMin") or 0) > 0
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

    def plan_pod_worker(
        self,
        *,
        gpu_type_id: str,
        image: str,
        cloud_type: str,
        gpu_count: int,
        volume_in_gb: int,
        container_disk_in_gb: int,
        min_vcpu_count: int,
        min_memory_in_gb: int,
        max_uptime_seconds: int,
        max_estimated_cost_usd: float,
        docker_args: str,
    ) -> dict[str, Any]:
        gpu_info = self._gpu_type_info(gpu_type_id, gpu_count=gpu_count)
        price = _gpu_uninterruptable_price(gpu_info)
        estimated = None if price is None else round(float(price) * max_uptime_seconds / 3600, 6)
        ok_cost = estimated is not None and estimated <= max_estimated_cost_usd
        return {
            "ok": bool(gpu_info.get("ok")) and ok_cost,
            "provider": self.name,
            "plan_version": "runpod-pod-worker-plan-v1",
            "official_basis": {
                "graphql_docs": "https://docs.runpod.io/sdks/graphql/manage-pods",
                "create_mutation": "podFindAndDeployOnDemand",
                "stop_mutation": "podStop",
                "sdk_cleanup": "runpod.terminate_pod",
            },
            "pod_input": {
                "name": "gpu-job-pod-canary-<timestamp>",
                "imageName": image,
                "gpuTypeId": gpu_type_id,
                "cloudType": cloud_type,
                "gpuCount": gpu_count,
                "volumeInGb": volume_in_gb,
                "containerDiskInGb": container_disk_in_gb,
                "minVcpuCount": min_vcpu_count,
                "minMemoryInGb": min_memory_in_gb,
                "dockerArgs": docker_args,
                "volumeMountPath": "/runpod-volume",
            },
            "gpu_info": gpu_info,
            "cost_guard": {
                "uninterruptable_price_usd_per_hour": price,
                "max_uptime_seconds": max_uptime_seconds,
                "estimated_cost_usd": estimated,
                "max_estimated_cost_usd": max_estimated_cost_usd,
                "ok": ok_cost,
            },
            "cleanup_sequence": [
                "create pod only after clean pre-guard",
                "observe desiredStatus/runtime until ready or timeout",
                "terminate pod in finally block",
                "run post-guard and require no active pods",
            ],
        }

    def canary_pod_lifecycle(
        self,
        *,
        gpu_type_id: str,
        image: str,
        cloud_type: str,
        gpu_count: int,
        volume_in_gb: int,
        container_disk_in_gb: int,
        min_vcpu_count: int,
        min_memory_in_gb: int,
        max_uptime_seconds: int,
        max_estimated_cost_usd: float,
        docker_args: str,
        execute: bool,
    ) -> dict[str, Any]:
        plan = self.plan_pod_worker(
            gpu_type_id=gpu_type_id,
            image=image,
            cloud_type=cloud_type,
            gpu_count=gpu_count,
            volume_in_gb=volume_in_gb,
            container_disk_in_gb=container_disk_in_gb,
            min_vcpu_count=min_vcpu_count,
            min_memory_in_gb=min_memory_in_gb,
            max_uptime_seconds=max_uptime_seconds,
            max_estimated_cost_usd=max_estimated_cost_usd,
            docker_args=docker_args,
        )
        if not execute:
            return {**plan, "executed": False}
        if not plan.get("ok"):
            return {**plan, "executed": False}
        pod: dict[str, Any] | None = None
        samples: list[dict[str, Any]] = []
        terminated: dict[str, Any] | None = None
        result: dict[str, Any] | None = None
        start = time.time()
        try:
            pod = self._create_canary_pod(
                gpu_type_id=gpu_type_id,
                image=image,
                cloud_type=cloud_type,
                gpu_count=gpu_count,
                volume_in_gb=volume_in_gb,
                container_disk_in_gb=container_disk_in_gb,
                min_vcpu_count=min_vcpu_count,
                min_memory_in_gb=min_memory_in_gb,
                docker_args=docker_args,
            )
            pod_id = str(pod.get("id") or "")
            if not pod_id:
                raise RuntimeError(f"RunPod pod create returned no id: {pod}")
            actual_cost_guard = _actual_pod_cost_guard(
                pod,
                max_uptime_seconds=max_uptime_seconds,
                max_estimated_cost_usd=max_estimated_cost_usd,
            )
            if not actual_cost_guard["ok"]:
                raise RuntimeError(f"created pod exceeds cost guard: {actual_cost_guard}")
            deadline = start + max_uptime_seconds
            while time.time() < deadline:
                sample = self._get_pod(pod_id)
                samples.append(_public_pod(sample))
                if _pod_has_runtime(sample):
                    break
                time.sleep(min(10, max(1, int(deadline - time.time()))))
            observed_runtime = any(_pod_has_runtime(sample) for sample in samples)
            result = {
                "ok": observed_runtime,
                "executed": True,
                "plan": plan,
                "pod": _public_pod(pod),
                "actual_cost_guard": actual_cost_guard,
                "samples": samples,
                "observed_runtime": observed_runtime,
                "runtime_seconds": round(time.time() - start, 3),
            }
        except Exception as exc:
            result = {
                "ok": False,
                "executed": True,
                "error": str(exc),
                "plan": plan,
                "pod": _public_pod(pod) if pod else None,
                "samples": samples,
                "cleanup": terminated,
            }
        finally:
            if pod and pod.get("id"):
                try:
                    terminated = self._terminate_pod(str(pod["id"]))
                except Exception as exc:
                    terminated = {"ok": False, "error": str(exc)}
            if result is not None:
                result["cleanup"] = terminated
                if terminated and not terminated.get("ok", False):
                    result["ok"] = False
        if result is None:
            return {**plan, "ok": False, "executed": True, "error": "pod lifecycle returned no result"}
        return result

    def canary_pod_http_worker(
        self,
        *,
        gpu_type_id: str,
        image: str,
        cloud_type: str,
        gpu_count: int,
        volume_in_gb: int,
        container_disk_in_gb: int,
        min_vcpu_count: int,
        min_memory_in_gb: int,
        max_uptime_seconds: int,
        max_estimated_cost_usd: float,
        execute: bool,
    ) -> dict[str, Any]:
        docker_args = _pod_http_worker_docker_args()
        plan = self.plan_pod_worker(
            gpu_type_id=gpu_type_id,
            image=image,
            cloud_type=cloud_type,
            gpu_count=gpu_count,
            volume_in_gb=volume_in_gb,
            container_disk_in_gb=container_disk_in_gb,
            min_vcpu_count=min_vcpu_count,
            min_memory_in_gb=min_memory_in_gb,
            max_uptime_seconds=max_uptime_seconds,
            max_estimated_cost_usd=max_estimated_cost_usd,
            docker_args=docker_args,
        )
        plan["pod_input"]["ports"] = "8000/http"
        plan["worker_canary"] = {
            "health_path": "/health",
            "proxy_url_template": "https://<pod_id>-8000.proxy.runpod.net/health",
            "success_condition": "HTTP 200 JSON with ok=true and nvidia-smi exit_code=0",
        }
        if not execute:
            return {**plan, "executed": False}
        if not plan.get("ok"):
            return {**plan, "executed": False}
        pod: dict[str, Any] | None = None
        samples: list[dict[str, Any]] = []
        health_samples: list[dict[str, Any]] = []
        terminated: dict[str, Any] | None = None
        result: dict[str, Any] | None = None
        start = time.time()
        try:
            pod = self._create_canary_pod(
                gpu_type_id=gpu_type_id,
                image=image,
                cloud_type=cloud_type,
                gpu_count=gpu_count,
                volume_in_gb=volume_in_gb,
                container_disk_in_gb=container_disk_in_gb,
                min_vcpu_count=min_vcpu_count,
                min_memory_in_gb=min_memory_in_gb,
                docker_args=docker_args,
                ports="8000/http",
            )
            pod_id = str(pod.get("id") or "")
            if not pod_id:
                raise RuntimeError(f"RunPod pod create returned no id: {pod}")
            actual_cost_guard = _actual_pod_cost_guard(
                pod,
                max_uptime_seconds=max_uptime_seconds,
                max_estimated_cost_usd=max_estimated_cost_usd,
            )
            if not actual_cost_guard["ok"]:
                raise RuntimeError(f"created pod exceeds cost guard: {actual_cost_guard}")
            health_url = f"https://{pod_id}-8000.proxy.runpod.net/health"
            deadline = start + max_uptime_seconds
            while time.time() < deadline:
                sample = self._get_pod(pod_id)
                samples.append(_public_pod(sample))
                if _pod_has_runtime(sample):
                    health = _fetch_json_url(health_url, timeout=10)
                    health_samples.append(health)
                    if health.get("ok") and health.get("gpu_probe", {}).get("exit_code") == 0:
                        break
                time.sleep(min(5, max(1, int(deadline - time.time()))))
            healthy = any(item.get("ok") and item.get("gpu_probe", {}).get("exit_code") == 0 for item in health_samples)
            result = {
                "ok": healthy,
                "executed": True,
                "plan": plan,
                "pod": _public_pod(pod),
                "actual_cost_guard": actual_cost_guard,
                "samples": samples,
                "health_url": health_url,
                "health_samples": health_samples,
                "observed_runtime": any(_pod_has_runtime(sample) for sample in samples),
                "observed_http_worker": healthy,
                "runtime_seconds": round(time.time() - start, 3),
            }
        except Exception as exc:
            result = {
                "ok": False,
                "executed": True,
                "error": str(exc),
                "plan": plan,
                "pod": _public_pod(pod) if pod else None,
                "samples": samples,
                "health_samples": health_samples,
                "cleanup": terminated,
            }
        finally:
            if pod and pod.get("id"):
                try:
                    terminated = self._terminate_pod(str(pod["id"]))
                except Exception as exc:
                    terminated = {"ok": False, "error": str(exc)}
            if result is not None:
                result["cleanup"] = terminated
                if terminated and not terminated.get("ok", False):
                    result["ok"] = False
        if result is None:
            return {**plan, "ok": False, "executed": True, "error": "pod HTTP worker canary returned no result"}
        return result

    def _gpu_type_info(self, gpu_type_id: str, *, gpu_count: int) -> dict[str, Any]:
        query = f"""
query {{
  gpuTypes(input: {{ id: {_graphql_string(gpu_type_id)} }}) {{
    id displayName memoryInGb secureCloud communityCloud
    lowestPrice(input: {{ gpuCount: {int(gpu_count)} }}) {{
      stockStatus minimumBidPrice uninterruptablePrice availableGpuCounts
    }}
  }}
}}
"""
        try:
            rows = self._run_graphql(query)["data"]["gpuTypes"]
        except Exception as exc:
            return {"ok": False, "id": gpu_type_id, "error": str(exc)}
        if not rows:
            return {"ok": False, "id": gpu_type_id, "error": "gpu type not found"}
        row = rows[0]
        row["ok"] = True
        return row

    def _run_runpod_python(self, code: str, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
        python_bin = runpod_python()
        if not python_bin:
            raise RuntimeError("runpod python SDK not importable; install gpu-job-control[providers] or set RUNPOD_PYTHON")
        proc = run([python_bin, "-c", code], input=json.dumps(payload), capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "runpod python action failed")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"runpod python action returned non-json: {proc.stdout!r}") from exc

    def _create_canary_pod(
        self,
        *,
        gpu_type_id: str,
        image: str,
        cloud_type: str,
        gpu_count: int,
        volume_in_gb: int,
        container_disk_in_gb: int,
        min_vcpu_count: int,
        min_memory_in_gb: int,
        docker_args: str,
        ports: str | None = None,
    ) -> dict[str, Any]:
        name = f"gpu-job-pod-canary-{time.strftime('%Y%m%d%H%M%S')}"
        fields = [
            f"name: {_graphql_string(name)}",
            f"imageName: {_graphql_string(image)}",
            f"gpuTypeId: {_graphql_string(gpu_type_id)}",
            f"cloudType: {cloud_type}",
            f"gpuCount: {int(gpu_count)}",
            f"volumeInGb: {int(volume_in_gb)}",
            f"containerDiskInGb: {int(container_disk_in_gb)}",
            f"minVcpuCount: {int(min_vcpu_count)}",
            f"minMemoryInGb: {int(min_memory_in_gb)}",
            f"dockerArgs: {_graphql_string(docker_args)}",
            'volumeMountPath: "/runpod-volume"',
            "supportPublicIp: false",
            "startSsh: false",
        ]
        if ports:
            fields.append(f"ports: {_graphql_string(ports)}")
        query = f"""
mutation {{
  podFindAndDeployOnDemand(input: {{ {", ".join(fields)} }}) {{
    id
    name
    desiredStatus
    imageName
    machineId
    gpuCount
    costPerHr
    uptimeSeconds
    machine {{ podHostId gpuDisplayName }}
  }}
}}
"""
        return self._run_graphql(query)["data"]["podFindAndDeployOnDemand"]

    def _get_pod(self, pod_id: str) -> dict[str, Any]:
        code = (
            "import json,runpod,sys; "
            "p=json.loads(sys.stdin.read()); "
            "pod=runpod.get_pod(p['pod_id']); "
            "print(json.dumps(pod, ensure_ascii=False))"
        )
        return self._run_runpod_python(code, {"pod_id": pod_id}, timeout=30)

    def _terminate_pod(self, pod_id: str) -> dict[str, Any]:
        code = (
            "import json,runpod,sys; "
            "p=json.loads(sys.stdin.read()); "
            "result=runpod.terminate_pod(p['pod_id']); "
            "print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))"
        )
        return self._run_runpod_python(code, {"pod_id": pod_id}, timeout=60)

    def plan_vllm_endpoint(
        self,
        *,
        model: str,
        image: str,
        gpu_ids: str,
        network_volume_id: str,
        locations: str,
        hf_secret_name: str,
        max_model_len: int,
        gpu_memory_utilization: float,
        max_concurrency: int,
        idle_timeout: int,
        workers_max: int,
        scaler_value: int,
        quantization: str,
        served_model_name: str,
        flashboot: bool,
    ) -> dict[str, Any]:
        gpu_selection = _validate_runpod_gpu_ids(gpu_ids)
        if not gpu_selection["ok"]:
            return {
                "ok": False,
                "provider": self.name,
                "plan_version": "runpod-vllm-endpoint-plan-v1",
                "error": "invalid_runpod_gpu_ids",
                "gpu_selection": gpu_selection,
            }
        served_model_name = served_model_name or model
        template = self._vllm_template_input(
            model=model,
            image=image,
            hf_secret_name=hf_secret_name,
            max_model_len=max_model_len,
            gpu_memory_utilization=gpu_memory_utilization,
            max_concurrency=max_concurrency,
            quantization=quantization,
            served_model_name=served_model_name,
        )
        endpoint = self._vllm_endpoint_input(
            model=model,
            gpu_ids=gpu_ids,
            network_volume_id=network_volume_id,
            locations=locations,
            template_id="<created-template-id>",
            idle_timeout=idle_timeout,
            workers_max=workers_max,
            scaler_value=scaler_value,
            flashboot=flashboot,
        )
        return {
            "ok": True,
            "provider": self.name,
            "plan_version": "runpod-vllm-endpoint-plan-v1",
            "execute_required": "gpu-job runpod promote-vllm-endpoint --execute",
            "official_basis": {
                "worker_repo": "https://github.com/runpod-workers/worker-vllm",
                "openai_base_url": "https://api.runpod.ai/v2/<endpoint-id>/openai/v1",
            },
            "gpu_selection": gpu_selection,
            "safety_invariants": {
                "workers_min": 0,
                "workers_standby": 0,
                "workers_max": workers_max,
                "idle_timeout_seconds": idle_timeout,
                "requires_clean_pre_guard": True,
                "requires_clean_post_guard": True,
                "delete_precondition": "set workersMax=0 and workersMin=0 before delete",
            },
            "template": template,
            "endpoint": endpoint,
            "canary_sequence": [
                "create serverless template",
                "create serverless endpoint with workersMin=0",
                "verify endpoint has no warm capacity",
                "POST /openai/v1/chat/completions with max_tokens=8",
                "verify non-empty text response",
                "run post-guard; any warm capacity is failure",
            ],
        }

    def promote_vllm_endpoint(
        self,
        *,
        model: str,
        image: str,
        gpu_ids: str,
        network_volume_id: str,
        locations: str,
        hf_secret_name: str,
        max_model_len: int,
        gpu_memory_utilization: float,
        max_concurrency: int,
        idle_timeout: int,
        workers_max: int,
        scaler_value: int,
        quantization: str,
        served_model_name: str,
        flashboot: bool,
        canary_prompt: str,
        canary_timeout_seconds: int,
        execute: bool,
    ) -> dict[str, Any]:
        plan = self.plan_vllm_endpoint(
            model=model,
            image=image,
            gpu_ids=gpu_ids,
            network_volume_id=network_volume_id,
            locations=locations,
            hf_secret_name=hf_secret_name,
            max_model_len=max_model_len,
            gpu_memory_utilization=gpu_memory_utilization,
            max_concurrency=max_concurrency,
            idle_timeout=idle_timeout,
            workers_max=workers_max,
            scaler_value=scaler_value,
            quantization=quantization,
            served_model_name=served_model_name,
            flashboot=flashboot,
        )
        if not execute:
            return {**plan, "executed": False}
        if not plan.get("ok"):
            return {**plan, "executed": False}

        created_template = None
        endpoint = None
        disabled = None
        deleted = None
        canary: dict[str, Any] = {"ok": False}
        served_model_name = served_model_name or model
        try:
            created_template = self._create_vllm_template(
                model=model,
                image=image,
                hf_secret_name=hf_secret_name,
                max_model_len=max_model_len,
                gpu_memory_utilization=gpu_memory_utilization,
                max_concurrency=max_concurrency,
                quantization=quantization,
                served_model_name=served_model_name,
            )
            endpoint = self._create_vllm_endpoint(
                model=model,
                gpu_ids=gpu_ids,
                network_volume_id=network_volume_id,
                locations=locations,
                template_id=str(created_template["id"]),
                idle_timeout=idle_timeout,
                workers_max=workers_max,
                scaler_value=scaler_value,
                flashboot=flashboot,
            )
            endpoint_id = str(endpoint["id"])
            invariant = self._endpoint_scale_to_zero_invariant(endpoint)
            if not invariant["ok"]:
                raise RuntimeError(f"unsafe RunPod endpoint scale configuration: {invariant}")
            canary = self._openai_canary(
                endpoint_id=endpoint_id,
                model=served_model_name,
                prompt=canary_prompt,
                timeout=canary_timeout_seconds,
            )
            if not canary["ok"]:
                raise RuntimeError(f"RunPod vLLM canary failed: {canary}")
            return {
                "ok": True,
                "executed": True,
                "promotion_state": "short_generation_canary_ok",
                "plan": plan,
                "created_template": _public_template(created_template),
                "endpoint": _public_endpoint(endpoint),
                "scale_to_zero_invariant": invariant,
                "canary": canary,
                "openai_base_url": f"https://api.runpod.ai/v2/{endpoint_id}/openai/v1",
                "recommended_env": {
                    "RUNPOD_LLM_ENDPOINT_ID": endpoint_id,
                    "RUNPOD_LLM_ENDPOINT_MODE": "openai",
                    "RUNPOD_LLM_MODEL_OVERRIDE": served_model_name,
                },
            }
        except Exception as exc:
            if endpoint and endpoint.get("id") and endpoint.get("templateId"):
                try:
                    disabled = self._disable_endpoint(str(endpoint["id"]), template_id=str(endpoint["templateId"]))
                except Exception as disable_exc:
                    disabled = {"ok": False, "error": str(disable_exc)}
                try:
                    deleted = self._delete_endpoint(str(endpoint["id"]))
                except Exception as delete_exc:
                    deleted = {"ok": False, "error": str(delete_exc)}
            return {
                "ok": False,
                "executed": True,
                "error": str(exc),
                "plan": plan,
                "created_template": _public_template(created_template) if created_template else None,
                "endpoint": _public_endpoint(endpoint) if endpoint else None,
                "canary": canary,
                "disabled": disabled,
                "deleted": deleted,
            }

    def _vllm_template_input(
        self,
        *,
        model: str,
        image: str,
        hf_secret_name: str,
        max_model_len: int,
        gpu_memory_utilization: float,
        max_concurrency: int,
        quantization: str,
        served_model_name: str,
    ) -> dict[str, Any]:
        env = [
            {"key": "MODEL_NAME", "value": model},
            {"key": "MAX_MODEL_LEN", "value": str(max_model_len)},
            {"key": "GPU_MEMORY_UTILIZATION", "value": str(gpu_memory_utilization)},
            {"key": "MAX_CONCURRENCY", "value": str(max_concurrency)},
            {"key": "OPENAI_SERVED_MODEL_NAME_OVERRIDE", "value": served_model_name},
        ]
        if quantization:
            env.append({"key": "QUANTIZATION", "value": quantization})
        if hf_secret_name:
            env.append({"key": "HF_TOKEN", "value": f"{{{{ RUNPOD_SECRET_{hf_secret_name} }}}}"})
        return {
            "name": f"gpu-job-vllm-{_safe_name(model)}",
            "imageName": image,
            "isServerless": True,
            "containerDiskInGb": 30,
            "volumeInGb": 0,
            "dockerArgs": "",
            "env": env,
        }

    def _vllm_endpoint_input(
        self,
        *,
        model: str,
        gpu_ids: str,
        network_volume_id: str,
        locations: str,
        template_id: str,
        idle_timeout: int,
        workers_max: int,
        scaler_value: int,
        flashboot: bool,
    ) -> dict[str, Any]:
        endpoint: dict[str, Any] = {
            "name": f"gpu-job-vllm-{_safe_name(model)}-{time.strftime('%Y%m%d%H%M%S')}",
            "gpuIds": gpu_ids,
            "gpuCount": 1,
            "locations": locations.strip(),
            "templateId": template_id,
            "workersMin": 0,
            "workersMax": workers_max,
            "idleTimeout": idle_timeout,
            "scalerType": "QUEUE_DELAY",
            "scalerValue": scaler_value,
            "networkVolumeId": network_volume_id.strip(),
        }
        if flashboot:
            endpoint["flashBootType"] = "FLASHBOOT"
        return endpoint

    def _create_vllm_template(
        self,
        *,
        model: str,
        image: str,
        hf_secret_name: str,
        max_model_len: int,
        gpu_memory_utilization: float,
        max_concurrency: int,
        quantization: str,
        served_model_name: str,
    ) -> dict[str, Any]:
        template = self._vllm_template_input(
            model=model,
            image=image,
            hf_secret_name=hf_secret_name,
            max_model_len=max_model_len,
            gpu_memory_utilization=gpu_memory_utilization,
            max_concurrency=max_concurrency,
            quantization=quantization,
            served_model_name=served_model_name,
        )
        template["name"] = f"{template['name']}-{time.strftime('%Y%m%d%H%M%S')}"
        env = ", ".join(
            f"{{ key: {_graphql_string(str(item['key']))}, value: {_graphql_string(str(item['value']))} }}" for item in template["env"]
        )
        query = (
            "mutation {"
            " saveTemplate(input: {"
            f" name: {_graphql_string(str(template['name']))},"
            f" imageName: {_graphql_string(str(template['imageName']))},"
            " isServerless: true,"
            f" containerDiskInGb: {int(template['containerDiskInGb'])},"
            f" volumeInGb: {int(template['volumeInGb'])},"
            f" dockerArgs: {_graphql_string(str(template['dockerArgs']))},"
            f" env: [{env}]"
            " }) { id name imageName isServerless containerDiskInGb volumeInGb dockerArgs env { key value } }"
            "}"
        )
        return self._run_graphql(query)["data"]["saveTemplate"]

    def _create_vllm_endpoint(
        self,
        *,
        model: str,
        gpu_ids: str,
        network_volume_id: str,
        locations: str,
        template_id: str,
        idle_timeout: int,
        workers_max: int,
        scaler_value: int,
        flashboot: bool,
    ) -> dict[str, Any]:
        endpoint = self._vllm_endpoint_input(
            model=model,
            gpu_ids=gpu_ids,
            network_volume_id=network_volume_id,
            locations=locations,
            template_id=template_id,
            idle_timeout=idle_timeout,
            workers_max=workers_max,
            scaler_value=scaler_value,
            flashboot=flashboot,
        )
        endpoint_fields = [
            f"gpuCount: {int(endpoint['gpuCount'])}",
            f"gpuIds: {_graphql_string(str(endpoint['gpuIds']))}",
            f"idleTimeout: {int(endpoint['idleTimeout'])}",
            f"name: {_graphql_string(str(endpoint['name']))}",
            f"scalerType: {_graphql_string(str(endpoint['scalerType']))}",
            f"scalerValue: {int(endpoint['scalerValue'])}",
            f"templateId: {_graphql_string(str(endpoint['templateId']))}",
            f"workersMax: {int(endpoint['workersMax'])}",
            f"workersMin: {int(endpoint['workersMin'])}",
        ]
        if endpoint["locations"]:
            endpoint_fields.insert(2, f"locations: {_graphql_string(str(endpoint['locations']))}")
        if flashboot:
            endpoint_fields.insert(3 if endpoint["locations"] else 2, "flashBootType: FLASHBOOT")
        if endpoint["networkVolumeId"]:
            endpoint_fields.append(f"networkVolumeId: {_graphql_string(str(endpoint['networkVolumeId']))}")
        endpoint_input = ",\n    ".join(endpoint_fields)
        query = f"""
mutation {{
  saveEndpoint(input: {{
    {endpoint_input}
  }}) {{
    id name gpuIds gpuCount idleTimeout locations flashBootType
    scalerType scalerValue templateId
    workersMax workersMin workersStandby networkVolumeId
  }}
}}
"""
        return self._run_graphql(query)["data"]["saveEndpoint"]

    def _endpoint_scale_to_zero_invariant(self, endpoint: dict[str, Any]) -> dict[str, Any]:
        workers_min = int(endpoint.get("workersMin") or 0)
        workers_standby = int(endpoint.get("workersStandby") or 0)
        workers_max = int(endpoint.get("workersMax") or 0)
        return {
            "ok": workers_min == 0 and workers_max <= 1,
            "workersMin": workers_min,
            "workersStandby": workers_standby,
            "workersMax": workers_max,
            "fixed_warm_capacity_basis": (
                "workersMin is the input-configurable minimum active worker count; "
                "workersStandby is observed but not accepted by EndpointInput"
            ),
        }

    def _openai_canary(self, *, endpoint_id: str, model: str, prompt: str, timeout: int) -> dict[str, Any]:
        deadline = time.time() + timeout
        attempts: list[dict[str, Any]] = []
        health_samples: list[dict[str, Any]] = []
        transient_http = {502, 503, 504}
        output: dict[str, Any] = {}
        text = ""
        while time.time() < deadline:
            remaining = max(1, int(deadline - time.time()))
            attempt_timeout = remaining
            output = self._run_openai_chat(
                endpoint_id=endpoint_id,
                model=model,
                prompt=prompt,
                max_tokens=8,
                timeout=attempt_timeout,
            )
            text = _extract_text(output)
            attempts.append(
                {
                    "status": output.get("status"),
                    "http_status": output.get("http_status"),
                    "text_chars": len(text),
                    "error": output.get("error", ""),
                }
            )
            if text.strip():
                break
            if output.get("status") == "HTTP_ERROR" and output.get("http_status") not in transient_http:
                break
            health_samples.append(self._endpoint_health_sample(endpoint_id))
            if output.get("status") == "TRANSPORT_ERROR":
                break
            time.sleep(min(10, max(1, int(deadline - time.time()))))
        return {
            "ok": bool(text.strip()),
            "endpoint_id": endpoint_id,
            "model": model,
            "text": text,
            "text_chars": len(text),
            "raw_status": output.get("status") if isinstance(output, dict) else None,
            "attempts": attempts,
            "health_samples": health_samples,
        }

    def _endpoint_health_sample(self, endpoint_id: str) -> dict[str, Any]:
        try:
            endpoints = [endpoint for endpoint in self._api_snapshot().get("endpoints", []) if str(endpoint.get("id") or "") == endpoint_id]
            if not endpoints:
                return {"id": endpoint_id, "ok": False, "error": "endpoint not found"}
            health = self._endpoint_health(endpoints)
            return health[0] if health else {"id": endpoint_id, "ok": False, "error": "endpoint health unavailable"}
        except Exception as exc:
            return {"id": endpoint_id, "ok": False, "error": str(exc)}

    def _run_openai_chat(self, *, endpoint_id: str, model: str, prompt: str, max_tokens: int, timeout: int) -> dict[str, Any]:
        api_key = _runpod_api_key()
        if not api_key:
            raise RuntimeError("RunPod OpenAI-compatible endpoint requires RunPod API key")
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
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
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                output = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            return {
                "id": endpoint_id,
                "status": "HTTP_ERROR",
                "http_status": exc.code,
                "error": body,
            }
        except (TimeoutError, urllib.error.URLError) as exc:
            return {
                "id": endpoint_id,
                "status": "TRANSPORT_ERROR",
                "error": str(exc),
            }
        return {"id": endpoint_id, "status": "COMPLETED", "output": output}

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

    def _run_graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        api_key = _runpod_api_key()
        if not api_key:
            raise RuntimeError("RUNPOD_API_KEY or ~/.runpod/config.toml default.api_key is required for RunPod GraphQL operations")
        base_url = os.getenv("RUNPOD_API_BASE_URL", "https://api.runpod.io").rstrip("/")
        request = urllib.request.Request(
            f"{base_url}/graphql",
            data=json.dumps({"query": query, "variables": variables or {}}).encode(),
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
            if workers_min > 0:
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


def _public_template(template: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": template.get("id"),
        "name": template.get("name"),
        "imageName": template.get("imageName"),
        "isServerless": template.get("isServerless"),
        "containerDiskInGb": template.get("containerDiskInGb"),
        "volumeInGb": template.get("volumeInGb"),
        "dockerArgs": template.get("dockerArgs"),
        "env_keys": [item.get("key") for item in template.get("env", []) if isinstance(item, dict)],
    }


def _gpu_uninterruptable_price(gpu_info: dict[str, Any]) -> float | None:
    lowest = gpu_info.get("lowestPrice")
    lowest = lowest if isinstance(lowest, dict) else {}
    value = lowest.get("uninterruptablePrice")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _pod_has_runtime(pod: dict[str, Any]) -> bool:
    if str(pod.get("desiredStatus") or "").upper() == "RUNNING":
        return True
    uptime_seconds = pod.get("uptimeSeconds")
    try:
        if uptime_seconds is not None and int(uptime_seconds) >= 0:
            return True
    except (TypeError, ValueError):
        pass
    runtime = pod.get("runtime")
    if not isinstance(runtime, dict):
        return False
    uptime = runtime.get("uptimeInSeconds")
    try:
        return uptime is not None and int(uptime) >= 0
    except (TypeError, ValueError):
        return False


def _actual_pod_cost_guard(
    pod: dict[str, Any],
    *,
    max_uptime_seconds: int,
    max_estimated_cost_usd: float,
) -> dict[str, Any]:
    raw = pod.get("costPerHr")
    try:
        cost_per_hour = float(raw)
    except (TypeError, ValueError):
        return {"ok": False, "cost_per_hour": raw, "error": "pod costPerHr is not numeric"}
    max_cost = round(cost_per_hour * max_uptime_seconds / 3600, 6)
    return {
        "ok": max_cost <= max_estimated_cost_usd,
        "cost_per_hour": cost_per_hour,
        "max_uptime_seconds": max_uptime_seconds,
        "max_cost_usd": max_cost,
        "max_estimated_cost_usd": max_estimated_cost_usd,
    }


def _public_pod(pod: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pod:
        return None
    runtime = pod.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    machine = pod.get("machine")
    machine = machine if isinstance(machine, dict) else {}
    return {
        "id": pod.get("id"),
        "name": pod.get("name"),
        "desiredStatus": pod.get("desiredStatus"),
        "imageName": pod.get("imageName"),
        "gpuCount": pod.get("gpuCount"),
        "costPerHr": pod.get("costPerHr"),
        "machineId": pod.get("machineId"),
        "podHostId": machine.get("podHostId"),
        "uptimeSeconds": pod.get("uptimeSeconds"),
        "runtime": {
            "uptimeInSeconds": runtime.get("uptimeInSeconds"),
            "ports": runtime.get("ports"),
            "gpus": runtime.get("gpus"),
            "container": runtime.get("container"),
        }
        if runtime
        else None,
    }


def _fetch_json_url(url: str, *, timeout: int) -> dict[str, Any]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gpu-job-control"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        return json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": exc.code, "error": body[:500]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _pod_http_worker_docker_args() -> str:
    script = r"""
import json
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

STARTED_AT = time.time()


def gpu_probe():
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
    except Exception as exc:
        return {"exit_code": 1, "error": str(exc)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/health":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        probe = gpu_probe()
        self._send_json(
            200,
            {
                "ok": probe.get("exit_code") == 0,
                "service": "gpu-job-runpod-pod-http-canary",
                "uptime_seconds": round(time.time() - STARTED_AT, 3),
                "gpu_probe": probe,
            },
        )

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        self._send_json(
            200,
            {
                "ok": True,
                "service": "gpu-job-runpod-pod-http-canary",
                "received_bytes": len(raw.encode()),
                "gpu_probe": gpu_probe(),
            },
        )


HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
""".strip()
    encoded = base64.b64encode(script.encode()).decode()
    return f'bash -lc "python -u -c \\"import base64;exec(base64.b64decode(\\\\\\"{encoded}\\\\\\"))\\""'


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
