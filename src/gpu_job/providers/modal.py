from __future__ import annotations

from pathlib import Path
from shutil import which
from subprocess import run
from typing import Any

from gpu_job.models import Job, now_unix
from gpu_job.execution_plan import build_execution_plan
from gpu_job.providers.base import Provider
from gpu_job.store import JobStore
from gpu_job.verify import verify_artifacts


def modal_bin() -> str | None:
    candidates = [which("modal"), str(Path.home() / ".local" / "bin" / "modal")]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


class ModalProvider(Provider):
    name = "modal"

    def doctor(self) -> dict[str, Any]:
        binary = modal_bin()
        config = Path.home() / ".modal.toml"
        ok_binary = bool(binary and Path(binary).exists())
        ok_config = config.is_file() and config.stat().st_size > 0
        version_ok = False
        token_ok = False
        profile = ""
        if ok_binary:
            version = run([binary, "--version"], capture_output=True, text=True, timeout=20)
            version_ok = version.returncode == 0 and "modal client version" in version.stdout
            token = run([binary, "token", "info"], capture_output=True, text=True, timeout=30)
            token_ok = token.returncode == 0 and "Workspace:" in token.stdout
            current = run([binary, "profile", "current"], capture_output=True, text=True, timeout=20)
            if current.returncode == 0:
                profile = current.stdout.strip()
        return {
            "provider": self.name,
            "ok": ok_binary and ok_config and version_ok and token_ok,
            "binary": binary if ok_binary else "",
            "config_present": ok_config,
            "version_ok": version_ok,
            "token_ok": token_ok,
            "profile": profile,
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
        binary = modal_bin()
        if not binary:
            signal["available"] = False
            signal["reason"] = "modal binary not found"
            return signal
        apps = run([binary, "app", "list"], capture_output=True, text=True, timeout=45)
        signal["app_list_ok"] = apps.returncode == 0
        if apps.returncode != 0:
            signal["available"] = False
            signal["reason"] = apps.stderr.strip() or "modal app list failed"
            return signal
        running_lines = [line for line in apps.stdout.splitlines() if " running " in line]
        signal["active_jobs"] = len(running_lines)
        signal["capacity_hint"] = "function execution provider; capacity is allocated per run"
        signal["estimated_startup_seconds"] = 15
        max_cost_usd = float(profile.get("max_cost_usd", 0))
        # Conservative placeholder until a concrete Modal GPU price table is wired in.
        # Actual spend is measured in metrics after a canary run.
        estimate = 0.0
        signal["estimated_max_runtime_cost_usd"] = estimate
        signal["reason"] = "healthy; app list readable"
        if max_cost_usd and estimate > max_cost_usd:
            signal["available"] = False
            signal["reason"] = "estimated cost exceeds profile max_cost_usd"
        return signal

    def cost_guard(self) -> dict[str, Any]:
        health = self.doctor()
        if not health.get("ok"):
            return {
                "provider": self.name,
                "ok": False,
                "billable_resources": [],
                "estimated_hourly_usd": 0.0,
                "reason": "modal health check failed",
                "health": health,
            }
        binary = modal_bin()
        apps = run([binary, "app", "list"], capture_output=True, text=True, timeout=45)
        running = []
        if apps.returncode == 0:
            running = [line.strip() for line in apps.stdout.splitlines() if " running " in line]
        return {
            "provider": self.name,
            "ok": not running,
            "billable_resources": running,
            "estimated_hourly_usd": None,
            "reason": "no running Modal apps" if not running else "running Modal apps present",
        }

    def plan(self, job: Job) -> dict[str, Any]:
        return {
            "provider": self.name,
            "job_id": job.job_id,
            "mode": "function plan",
            "worker_image": job.worker_image,
            "execution_plan": build_execution_plan(job, self.name),
            "gpu_profile": job.gpu_profile,
            "input_uri": job.input_uri,
            "output_uri": job.output_uri,
            "notes": [
                "Modal runs GPU jobs as functions rather than SSH instances.",
                "Execute supports GPU smoke, ASR canaries, llm_heavy canaries, and VLM/OCR canaries.",
                "Execute writes the standard artifact contract.",
                "Worker must write result.json, metrics.json, verify.json, stdout.log, stderr.log.",
            ],
        }

    def _command(self, binary: str, job: Job, artifact_dir: Path) -> list[str]:
        package_dir = Path(__file__).resolve().parents[1]
        if job.job_type == "smoke":
            script = package_dir / "modal_smoke.py"
            return [
                binary,
                "run",
                f"{script}::main",
                "--job-id",
                job.job_id,
                "--artifact-dir",
                str(artifact_dir),
                "--gpu-profile",
                job.gpu_profile,
            ]
        if job.job_type == "asr":
            script = package_dir / "modal_asr.py"
            contract_probe = job.metadata.get("contract_probe") if isinstance(job.metadata.get("contract_probe"), dict) else {}
            if contract_probe.get("probe_name") == "modal.asr_diarization.pyannote":
                return [
                    binary,
                    "run",
                    f"{script}::canary",
                    "--artifact-dir",
                    str(artifact_dir),
                    "--speaker-model",
                    job.model or "pyannote/speaker-diarization-3.1",
                ]
            input_payload = job.metadata.get("input") if isinstance(job.metadata.get("input"), dict) else {}
            command = [
                binary,
                "run",
                f"{script}::main",
                "--job-id",
                job.job_id,
                "--artifact-dir",
                str(artifact_dir),
                "--gpu-profile",
                job.gpu_profile,
                "--input-uri",
                job.input_uri,
                "--model-name",
                job.model or "large-v3",
                "--language",
                str(input_payload.get("language") or "ja"),
            ]
            if bool(input_payload.get("diarize") or input_payload.get("speaker_diarization")):
                command.append("--diarize")
                command.extend(["--speaker-model", str(input_payload.get("speaker_model") or "pyannote/speaker-diarization-3.1")])
            return command
        if job.job_type == "llm_heavy":
            script = package_dir / "modal_llm.py"
            job_json = JobStore().job_path(job.job_id)
            return [
                binary,
                "run",
                f"{script}::main",
                "--job-json",
                str(job_json),
                "--artifact-dir",
                str(artifact_dir),
            ]
        if job.job_type in {"vlm_ocr", "pdf_ocr"}:
            script = package_dir / "modal_vlm.py"
            job_json = JobStore().job_path(job.job_id)
            return [
                binary,
                "run",
                f"{script}::main",
                "--job-json",
                str(job_json),
                "--artifact-dir",
                str(artifact_dir),
            ]
        raise ValueError(f"Modal execute does not support job_type: {job.job_type}")

    def submit(self, job: Job, store: JobStore, execute: bool = False) -> Job:
        job.provider = self.name
        job.provider_job_id = ""
        job.metadata["provider_plan"] = self.plan(job)
        job.metadata["execute_requested"] = execute
        if not execute:
            job.status = "planned"
            store.save(job)
            return job

        binary = modal_bin()
        if not binary:
            job.status = "failed"
            job.error = "modal binary not found"
            job.exit_code = 127
            store.save(job)
            return job

        artifact_dir = store.artifact_dir(job.job_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        store.save(job)
        command = self._command(binary, job, artifact_dir)
        start = now_unix()
        job.started_at = start
        job.status = "running"
        store.save(job)
        proc = run(
            command,
            capture_output=True,
            text=True,
            timeout=int(job.limits.get("max_runtime_minutes", 10)) * 60,
        )
        (artifact_dir / "stdout.log").write_text(proc.stdout)
        (artifact_dir / "stderr.log").write_text(proc.stderr)
        verify = verify_artifacts(artifact_dir)
        job.finished_at = now_unix()
        job.runtime_seconds = max(0, job.finished_at - start)
        job.exit_code = proc.returncode
        job.artifact_count = verify["artifact_count"]
        job.artifact_bytes = verify["artifact_bytes"]
        job.status = "succeeded" if proc.returncode == 0 and verify["ok"] else "failed"
        if proc.returncode != 0:
            job.error = proc.stderr.strip() or proc.stdout.strip() or "modal run failed"
        store.save(job)
        return job
