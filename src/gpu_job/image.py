from __future__ import annotations

from pathlib import Path
from subprocess import run
from typing import Any
import os


ROOT = Path(__file__).resolve().parents[2]


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
    if os.getenv("GPU_JOB_ALLOW_LOCAL_DOCKER", "") != "1":
        return {
            "ok": False,
            "error": "refusing to run Docker build without GPU_JOB_ALLOW_LOCAL_DOCKER=1",
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
