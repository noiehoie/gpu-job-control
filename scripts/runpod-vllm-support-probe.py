from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from argparse import ArgumentParser

from gpu_job.providers.runpod import RunPodProvider
from gpu_job.providers.runpod import _graphql_string
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
        "mutations": {},
        "responses": {},
    }
    endpoint = None

    try:
        env = [
            {"key": "MODEL_NAME", "value": model},
            {"key": "MAX_MODEL_LEN", "value": "512"},
            {"key": "GPU_MEMORY_UTILIZATION", "value": "0.8"},
            {"key": "MAX_CONCURRENCY", "value": "1"},
            {"key": "OPENAI_SERVED_MODEL_NAME_OVERRIDE", "value": model},
        ]
        env_graphql = ", ".join(
            "{ key: " + _graphql_string(item["key"]) + ", value: " + _graphql_string(item["value"]) + " }" for item in env
        )
        template_name = "gpu-job-vllm-support-diff-" + now
        template_mutation = (
            "mutation {"
            " saveTemplate(input: {"
            f" name: {_graphql_string(template_name)},"
            f" imageName: {_graphql_string('runpod/worker-v1-vllm:v2.14.0')},"
            " isServerless: true,"
            " containerDiskInGb: 30,"
            " volumeInGb: 0,"
            f" dockerArgs: {_graphql_string('')},"
            f" env: [{env_graphql}]"
            " }) { id name imageName isServerless containerDiskInGb volumeInGb dockerArgs env { key value } }"
            "}"
        )
        result["mutations"]["saveTemplate"] = template_mutation
        template_response = provider._run_graphql(template_mutation)
        result["responses"]["saveTemplate"] = template_response
        created_template = template_response["data"]["saveTemplate"]

        endpoint_name = "gpu-job-vllm-support-diff-" + now
        endpoint_mutation = f"""
mutation {{
  saveEndpoint(input: {{
    flashBootType: FLASHBOOT,
    gpuCount: 1,
    gpuIds: {_graphql_string("ADA_24")},
    idleTimeout: 180,
    name: {_graphql_string(endpoint_name)},
    scalerType: {_graphql_string("QUEUE_DELAY")},
    scalerValue: 15,
    templateId: {_graphql_string(str(created_template["id"]))},
    workersMax: 1,
    workersMin: 0
  }}) {{
    id name gpuIds gpuCount idleTimeout locations flashBootType
    scalerType scalerValue templateId workersMax workersMin workersStandby networkVolumeId
  }}
}}
"""
        result["mutations"]["saveEndpoint"] = endpoint_mutation
        endpoint_response = provider._run_graphql(endpoint_mutation)
        result["responses"]["saveEndpoint"] = endpoint_response
        endpoint = endpoint_response["data"]["saveEndpoint"]
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
            result["disabled"] = provider._disable_endpoint(str(endpoint["id"]), template_id=str(endpoint["templateId"]))
            time.sleep(3)
            if args.keep_endpoint_for_console:
                result["deleted"] = None
                result["console_endpoint_id"] = str(endpoint["id"])
                result["console_note"] = "Endpoint intentionally retained for dashboard logs; workersMin/workersMax were set to 0."
            else:
                result["deleted"] = provider._delete_endpoint(str(endpoint["id"]))
        result["guard"] = provider.cost_guard()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
