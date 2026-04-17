from __future__ import annotations

from pathlib import Path
import json
import subprocess
import time

import modal


app = modal.App("gpu-job-modal-smoke")


@app.function(gpu="T4", timeout=300)
def gpu_smoke(job_id: str, gpu_profile: str) -> dict:
    started = time.time()
    proc = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"], capture_output=True, text=True
    )
    return {
        "job_id": job_id,
        "gpu_profile": gpu_profile,
        "provider": "modal",
        "nvidia_smi_exit_code": proc.returncode,
        "nvidia_smi_stdout": proc.stdout.strip(),
        "nvidia_smi_stderr": proc.stderr.strip(),
        "runtime_seconds": round(time.time() - started, 3),
    }


@app.local_entrypoint()
def main(job_id: str, artifact_dir: str, gpu_profile: str):
    out = Path(artifact_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    result = gpu_smoke.remote(job_id, gpu_profile)
    metrics = {
        "job_id": job_id,
        "provider": "modal",
        "gpu_profile": gpu_profile,
        "runtime_seconds": round(time.time() - started, 3),
        "remote_runtime_seconds": result["runtime_seconds"],
        "nvidia_smi_exit_code": result["nvidia_smi_exit_code"],
    }
    verify = {
        "ok": result["nvidia_smi_exit_code"] == 0,
        "required": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
        "missing": [],
    }
    (out / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
    (out / "verify.json").write_text(json.dumps(verify, indent=2, sort_keys=True) + "\n")
