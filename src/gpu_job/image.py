from __future__ import annotations

from pathlib import Path
from shutil import which
from subprocess import run
from typing import Any
import json
import os
import platform
import shlex

from .image_contracts import load_image_contract_registry


ROOT = Path(__file__).resolve().parents[2]
LOCAL_DOCKER_FORBIDDEN_ACTION = "use_remote_linux_builder_or_ci"
LOCAL_DOCKER_FORBIDDEN_REASON = "local Docker execution is forbidden on macOS/Mac Studio; use a remote Linux builder or CI"


def image_plan(worker: str = "asr") -> dict[str, Any]:
    if worker != "asr":
        raise ValueError(f"unknown worker image: {worker}")
    return {
        "ok": True,
        "worker": worker,
        "image": "gpu-job-asr-worker:local",
        "registry_image": "registry.example.com/gpu-job-control/asr-worker:canary",
        "dockerfile": "docker/asr-worker.Dockerfile",
        "context": ".",
        "build_host_policy": "remote linux builder or CI",
        "push_policy": "operator-controlled registry mirror; GitHub/GHCR is not required at runtime",
        "commands": {
            "check": "gpu-job image check --worker asr",
            "build": "gpu-job image build --worker asr --execute",
        },
    }


def image_check(worker: str = "asr") -> dict[str, Any]:
    plan = image_plan(worker)
    dockerfile = ROOT / plan["dockerfile"]
    if not dockerfile.is_file():
        return {"ok": False, "error": f"Dockerfile not found: {dockerfile}"}
    counts = {"from": 0, "run": 0, "copy": 0, "entrypoint": 0}
    for line in dockerfile.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("FROM "):
            counts["from"] += 1
        elif stripped.startswith("RUN "):
            counts["run"] += 1
        elif stripped.startswith("COPY "):
            counts["copy"] += 1
        elif stripped.startswith("ENTRYPOINT "):
            counts["entrypoint"] += 1
    return {
        "ok": counts["from"] >= 1 and counts["entrypoint"] >= 1,
        "worker": worker,
        "dockerfile": str(dockerfile),
        "counts": counts,
    }


def image_build(worker: str = "asr", execute: bool = False) -> dict[str, Any]:
    plan = image_plan(worker)
    check = image_check(worker)
    if not check["ok"]:
        return {"ok": False, "check": check}
    command = [
        "docker",
        "build",
        "-t",
        plan["image"],
        "-f",
        plan["dockerfile"],
        plan["context"],
    ]
    if not execute:
        return {"ok": True, "planned": True, "command": command, "plan": plan, "check": check}
    forbidden = _local_docker_forbidden()
    if forbidden:
        return {**forbidden, "command": command, "plan": plan, "check": check}
    if os.getenv("GPU_JOB_ALLOW_LOCAL_DOCKER", "") != "1":
        return {
            "ok": False,
            "error": "refusing to run Docker build without GPU_JOB_ALLOW_LOCAL_DOCKER=1",
            "requires_action": "enable_remote_linux_builder_or_ci",
            "command": command,
        }
    proc = run(command, cwd=ROOT, capture_output=True, text=True, timeout=1800)
    inspect = run(["docker", "image", "inspect", plan["image"]], capture_output=True, text=True, timeout=60)
    return {
        "ok": proc.returncode == 0 and inspect.returncode == 0,
        "command": command,
        "returncode": proc.returncode,
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-40:]),
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-40:]),
        "inspect_ok": inspect.returncode == 0,
    }


def image_contract_plan(contract_id: str) -> dict[str, Any]:
    contract = _image_contract(contract_id)
    build_action = dict(contract.get("build_action") or {})
    dockerfile = str(build_action.get("dockerfile") or "docker/asr-worker.Dockerfile")
    context = str(build_action.get("context") or ".")
    image = str(contract.get("image") or "")
    if not image:
        raise ValueError(f"image contract {contract_id} does not declare image")
    build_command = ["docker", "build", "-t", image, "-f", dockerfile, context]
    probe_command = list(contract.get("probe_command") or [])
    docker_probe_command = _docker_probe_command(image, probe_command, require_gpu=False)
    gpu_probe_command = _docker_probe_command(image, probe_command, require_gpu=True)
    return {
        "ok": True,
        "contract_id": contract_id,
        "contract": contract,
        "image": image,
        "dockerfile": dockerfile,
        "context": context,
        "build_command": build_command,
        "probe_command": probe_command,
        "docker_probe_command": docker_probe_command,
        "gpu_probe_command": gpu_probe_command,
        "status": contract.get("status") or "unverified",
    }


def image_contract_check(contract_id: str) -> dict[str, Any]:
    plan = image_contract_plan(contract_id)
    dockerfile = ROOT / plan["dockerfile"]
    contract = dict(plan["contract"])
    missing_fields = [
        key for key in ("image", "entrypoint", "provides_backends", "artifact_contract", "probe_command") if not contract.get(key)
    ]
    dockerfile_check = image_check("asr") if plan["dockerfile"] == "docker/asr-worker.Dockerfile" else {"ok": dockerfile.is_file()}
    return {
        "ok": not missing_fields and bool(dockerfile_check.get("ok")),
        "contract_id": contract_id,
        "status": plan["status"],
        "image": plan["image"],
        "dockerfile": str(dockerfile),
        "missing_fields": missing_fields,
        "dockerfile_check": dockerfile_check,
    }


def image_contract_build(contract_id: str, *, execute: bool = False) -> dict[str, Any]:
    plan = image_contract_plan(contract_id)
    check = image_contract_check(contract_id)
    if not check["ok"]:
        return {"ok": False, "check": check, "plan": plan}
    if not execute:
        return {"ok": True, "planned": True, "plan": plan, "check": check}
    forbidden = _local_docker_forbidden()
    if forbidden:
        return {**forbidden, "plan": plan, "check": check}
    if os.getenv("GPU_JOB_ALLOW_LOCAL_DOCKER", "") != "1":
        return {
            "ok": False,
            "error": "refusing to run Docker build without GPU_JOB_ALLOW_LOCAL_DOCKER=1",
            "requires_action": "enable_remote_linux_builder_or_ci",
            "plan": plan,
            "check": check,
        }
    if not which("docker"):
        return {
            "ok": False,
            "error": "docker binary not found",
            "requires_action": "install_docker_or_configure_remote_builder",
            "plan": plan,
            "check": check,
        }
    proc = run(plan["build_command"], cwd=ROOT, capture_output=True, text=True, timeout=3600)
    inspect = run(["docker", "image", "inspect", plan["image"]], capture_output=True, text=True, timeout=60)
    return {
        "ok": proc.returncode == 0 and inspect.returncode == 0,
        "planned": False,
        "contract_id": contract_id,
        "image": plan["image"],
        "command": plan["build_command"],
        "returncode": proc.returncode,
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-60:]),
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-60:]),
        "inspect_ok": inspect.returncode == 0,
    }


def image_contract_probe(contract_id: str, *, execute: bool = False, require_gpu: bool = False) -> dict[str, Any]:
    plan = image_contract_plan(contract_id)
    command = plan["gpu_probe_command"] if require_gpu else plan["docker_probe_command"]
    if not command:
        return {"ok": False, "error": "image contract does not declare probe_command", "plan": plan}
    if not execute:
        return {"ok": True, "planned": True, "contract_id": contract_id, "command": command, "plan": plan}
    forbidden = _local_docker_forbidden()
    if forbidden:
        return {**forbidden, "command": command, "plan": plan}
    if os.getenv("GPU_JOB_ALLOW_LOCAL_DOCKER", "") != "1":
        return {
            "ok": False,
            "error": "refusing to run Docker probe without GPU_JOB_ALLOW_LOCAL_DOCKER=1",
            "requires_action": "enable_remote_linux_builder_or_ci",
            "command": command,
            "plan": plan,
        }
    if not which("docker"):
        return {
            "ok": False,
            "error": "docker binary not found",
            "requires_action": "install_docker_or_configure_remote_builder",
            "command": command,
            "plan": plan,
        }
    proc = run(command, cwd=ROOT, capture_output=True, text=True, timeout=600)
    payload = _json_from_stdout(proc.stdout)
    return {
        "ok": proc.returncode == 0 and bool(payload.get("ok", True)),
        "planned": False,
        "contract_id": contract_id,
        "image": plan["image"],
        "command": command,
        "returncode": proc.returncode,
        "probe": payload,
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-60:]),
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-60:]),
    }


def image_mirror_plan(source: str, target: str, *, builder: str = "") -> dict[str, Any]:
    if not source.strip() or not target.strip():
        raise ValueError("source and target image references are required")
    remote_builder = builder.strip() or os.getenv("GPU_JOB_DOCKER_BUILDER", "").strip()
    command = ["docker", "buildx", "imagetools", "create", "-t", target, source]
    if remote_builder:
        remote_command = " ".join(shlex.quote(item) for item in command)
        command = ["ssh", remote_builder, remote_command]
    return {
        "ok": True,
        "source": source,
        "target": target,
        "builder": remote_builder or None,
        "command": command,
        "runtime_policy": "mirror into an operator-controlled registry; do not require GitHub/GHCR at execution time",
        "requires": [
            "source image is readable by the builder",
            "target registry credentials are available only on the builder",
            "production jobs use the target digest after verification",
        ],
    }


def _local_docker_forbidden() -> dict[str, Any]:
    if platform.system() != "Darwin":
        return {}
    return {
        "ok": False,
        "error": "local_docker_forbidden_on_macos",
        "requires_action": LOCAL_DOCKER_FORBIDDEN_ACTION,
        "reason": LOCAL_DOCKER_FORBIDDEN_REASON,
    }


def image_mirror(source: str, target: str, *, builder: str = "", execute: bool = False) -> dict[str, Any]:
    plan = image_mirror_plan(source, target, builder=builder)
    if not execute:
        return {"ok": True, "planned": True, "plan": plan}
    if not plan["builder"] and os.getenv("GPU_JOB_ALLOW_LOCAL_DOCKER", "") != "1":
        return {
            "ok": False,
            "error": "refusing local Docker mirror without GPU_JOB_ALLOW_LOCAL_DOCKER=1 or --builder",
            "plan": plan,
        }
    proc = run(plan["command"], cwd=ROOT, capture_output=True, text=True, timeout=1800)
    return {
        "ok": proc.returncode == 0,
        "planned": False,
        "source": source,
        "target": target,
        "builder": plan["builder"],
        "command": plan["command"],
        "returncode": proc.returncode,
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-40:]),
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-40:]),
    }


def _image_contract(contract_id: str) -> dict[str, Any]:
    registry = load_image_contract_registry()
    contract = dict((registry.get("image_contracts") or {}).get(contract_id) or {})
    if not contract:
        raise ValueError(f"unknown image contract: {contract_id}")
    return contract


def _json_from_stdout(stdout: str) -> dict[str, Any]:
    for line in reversed(str(stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _docker_probe_command(image: str, probe_command: list[Any], *, require_gpu: bool) -> list[str]:
    if not probe_command:
        return []
    entrypoint = str(probe_command[0])
    args = [str(item) for item in probe_command[1:]]
    command = ["docker", "run", "--rm"]
    if require_gpu:
        command.extend(["--gpus", "all"])
    command.extend(["--entrypoint", entrypoint, image, *args])
    if require_gpu:
        command.append("--require-gpu")
    return command
