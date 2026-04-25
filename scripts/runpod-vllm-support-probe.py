from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from argparse import ArgumentParser

from gpu_job.providers.runpod import RunPodProvider
from gpu_job.providers.runpod import _runpod_api_key


def request_json(url: str, *, api_key: str, method: str = "GET", timeout: int = 300) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode()
            try:
                decoded = json.loads(body)
            except json.JSONDecodeError:
                decoded = body
            return {"ok": True, "status": response.status, "body": decoded}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "body": exc.read().decode(errors="replace")}
    except Exception as exc:
        return {"ok": False, "status": type(exc).__name__, "body": str(exc)}


def parse_args() -> object:
    parser = ArgumentParser(description="Create a minimal RunPod vLLM endpoint and capture support evidence.")
    parser.add_argument("--keep-endpoint-for-console", action="store_true", help="disable workers but do not delete the endpoint")
    parser.add_argument("--models-timeout", type=int, default=300, help="timeout in seconds for GET /openai/v1/models")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    provider = RunPodProvider()
    api_key = os.environ.get("RUNPOD_API_KEY", "").strip() or _runpod_api_key()
    model = "facebook/opt-125m"
    now = time.strftime("%Y%m%d%H%M%S")
    result = {
        "ok": False,
        "model": model,
        "keep_endpoint_for_console": bool(args.keep_endpoint_for_console),
        "models_timeout_seconds": int(args.models_timeout),
        "responses": {},
    }
    endpoint = None
    template = None

    try:
        env = [
            {"key": "MODEL_NAME", "value": model},
            {"key": "MAX_MODEL_LEN", "value": "512"},
            {"key": "GPU_MEMORY_UTILIZATION", "value": "0.8"},
            {"key": "MAX_CONCURRENCY", "value": "1"},
            {"key": "OPENAI_SERVED_MODEL_NAME_OVERRIDE", "value": model},
        ]
        template_payload = {
            "name": "gpu-job-vllm-support-diff-" + now,
            "imageName": "runpod/worker-v1-vllm:v2.14.0",
            "isServerless": True,
            "containerDiskInGb": 30,
            "volumeInGb": 0,
            "dockerArgs": "",
            "env": env,
        }
        template_req = urllib.request.Request(
            "https://api.runpod.io/v1/templates",
            data=json.dumps(template_payload).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "gpu-job-control",
            },
            method="POST",
        )
        with urllib.request.urlopen(template_req, timeout=30) as resp:
            template = json.loads(resp.read().decode())
        result["responses"]["saveTemplate"] = template

        endpoint_payload = {
            "flashBootType": "FLASHBOOT",
            "gpuCount": 1,
            "gpuIds": "ADA_24",
            "idleTimeout": 180,
            "name": "gpu-job-vllm-support-diff-" + now,
            "scalerType": "QUEUE_DELAY",
            "scalerValue": 15,
            "templateId": str(template["id"]),
            "workersMax": 1,
            "workersMin": 0,
        }
        endpoint_req = urllib.request.Request(
            "https://api.runpod.io/v1/endpoints",
            data=json.dumps(endpoint_payload).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "gpu-job-control",
            },
            method="POST",
        )
        with urllib.request.urlopen(endpoint_req, timeout=30) as resp:
            endpoint = json.loads(resp.read().decode())
        result["responses"]["saveEndpoint"] = endpoint
        endpoint_id = endpoint["id"]
        result["openai_models"] = request_json(
            f"https://api.runpod.ai/v2/{endpoint_id}/openai/v1/models",
            api_key=api_key,
            method="GET",
            timeout=int(args.models_timeout),
        )
        result["health_after_models"] = provider._endpoint_health_sample(endpoint_id)
        result["ok"] = bool(result["openai_models"].get("ok"))
    finally:
        if endpoint:
            # Disable endpoint via POST
            disable_payload = {
                "name": "gpu-job-disabled",
                "templateId": str(endpoint["templateId"]),
                "gpuIds": "ADA_24",
                "workersMin": 0,
                "workersMax": 0,
            }
            disable_req = urllib.request.Request(
                f"https://api.runpod.io/v1/endpoints/{endpoint['id']}",
                data=json.dumps(disable_payload).encode(),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "gpu-job-control",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(disable_req, timeout=30) as resp:
                    result["disabled"] = json.loads(resp.read().decode())
            except Exception as e:
                result["disabled"] = {"ok": False, "error": str(e)}
            
            time.sleep(3)
            if args.keep_endpoint_for_console:
                result["deleted"] = None
                result["console_endpoint_id"] = str(endpoint["id"])
                result["console_note"] = "Endpoint intentionally retained for dashboard logs; workersMin/workersMax were set to 0."
            else:
                delete_req = urllib.request.Request(
                    f"https://api.runpod.io/v1/endpoints/{endpoint['id']}",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "User-Agent": "gpu-job-control",
                    },
                    method="DELETE",
                )
                try:
                    with urllib.request.urlopen(delete_req, timeout=30) as resp:
                        result["deleted"] = {"ok": True, "status": resp.status}
                except Exception as e:
                    result["deleted"] = {"ok": False, "error": str(e)}
        
        if template and not args.keep_endpoint_for_console:
            delete_template_req = urllib.request.Request(
                f"https://api.runpod.io/v1/templates/{template['id']}",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "gpu-job-control",
                },
                method="DELETE",
            )
            try:
                with urllib.request.urlopen(delete_template_req, timeout=30) as resp:
                    result["template_deleted"] = {"ok": True, "status": resp.status}
            except Exception as e:
                result["template_deleted"] = {"ok": False, "error": str(e)}

        result["guard"] = provider.cost_guard()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
