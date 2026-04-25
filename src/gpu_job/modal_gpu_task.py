from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import time

import modal


image = modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.12").pip_install("torch", "numpy")

app = modal.App("gpu-job-modal-gpu-task", image=image)


@app.function(gpu="any", timeout=3600)
def run_gpu_task(job_dict: dict, command: list[str]) -> dict:
    started = time.time()
    env = dict(os.environ)
    env["GPU_JOB_ID"] = job_dict["job_id"]

    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=env,
            timeout=int((job_dict.get("limits") or {}).get("max_runtime_minutes") or 30) * 60,
        )
        ok = proc.returncode == 0
        stdout = proc.stdout
        stderr = proc.stderr
        exit_code = proc.returncode
    except Exception as exc:
        ok = False
        stdout = ""
        stderr = f"Execution failed: {exc}"
        exit_code = 1

    return {
        "job_id": job_dict["job_id"],
        "job_type": job_dict.get("job_type") or "gpu_task",
        "provider": "modal",
        "lane": "modal_function",
        "ok": ok,
        "exit_code": exit_code,
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "runtime_seconds": round(time.time() - started, 3),
    }


@app.local_entrypoint()
def main(job_json: str, artifact_dir: str) -> None:
    job_path = Path(job_json)
    out = Path(artifact_dir)
    out.mkdir(parents=True, exist_ok=True)

    with job_path.open("r", encoding="utf-8") as f:
        job_dict = json.load(f)

    provider_plan = job_dict.get("metadata", {}).get("provider_plan") or {}
    execution_plan = provider_plan.get("execution_plan") or {}
    command = execution_plan.get("command") or ["echo", "no command provided"]

    started = time.time()
    result = run_gpu_task.remote(job_dict, command)

    metrics = {
        "job_id": job_dict["job_id"],
        "job_type": job_dict.get("job_type") or "gpu_task",
        "provider": "modal",
        "runtime_seconds": round(time.time() - started, 3),
        "remote_runtime_seconds": result["runtime_seconds"],
    }

    verify = {
        "ok": result["ok"],
        "artifact_count": 5,
        "artifact_bytes": len(result["stdout"]) + len(result["stderr"]),
    }

    (out / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
    (out / "verify.json").write_text(json.dumps(verify, indent=2, sort_keys=True) + "\n")
    (out / "stdout.log").write_text(result["stdout"])
    (out / "stderr.log").write_text(result["stderr"])
