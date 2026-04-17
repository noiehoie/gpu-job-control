from __future__ import annotations

from pathlib import Path
from shutil import which
from subprocess import run
from typing import Any
import json
import ast
import time

from gpu_job.models import Job, now_unix
from gpu_job.providers.base import Provider
from gpu_job.store import JobStore
from gpu_job.verify import verify_artifacts


def vast_bin() -> str | None:
    candidates = [which("vastai"), str(Path.home() / ".local" / "bin" / "vastai"), "/usr/local/bin/vastai"]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


class VastProvider(Provider):
    name = "vast"

    def doctor(self) -> dict[str, Any]:
        binary = vast_bin()
        candidates = [Path.home() / ".config" / "vastai" / "vast_api_key", Path.home() / ".vast_api_key"]
        key_present = any(path.is_file() and path.stat().st_size > 0 for path in candidates)
        ok_binary = bool(binary and Path(binary).exists())
        help_ok = False
        instances_ok = False
        if ok_binary:
            proc = run([binary, "--help"], capture_output=True, text=True, timeout=20)
            help_ok = proc.returncode == 0 and "show instances" in proc.stdout
            proc = run([binary, "show", "instances", "--raw"], capture_output=True, text=True, timeout=45)
            instances_ok = proc.returncode == 0 and proc.stdout.lstrip().startswith("[")
        return {
            "provider": self.name,
            "ok": ok_binary and key_present and help_ok and instances_ok,
            "binary": binary if ok_binary else "",
            "api_key_present": key_present,
            "help_ok": help_ok,
            "instances_api_ok": instances_ok,
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
        binary = vast_bin()
        if not binary:
            signal["available"] = False
            signal["reason"] = "vastai binary not found"
            return signal
        user = run([binary, "show", "user", "--raw"], capture_output=True, text=True, timeout=45)
        if user.returncode == 0 and user.stdout.lstrip().startswith("{"):
            user_data = json.loads(user.stdout)
            signal["balance"] = user_data.get("balance")
            signal["credit"] = user_data.get("credit")
            signal["can_pay"] = user_data.get("can_pay")
            if not user_data.get("can_pay"):
                signal["available"] = False
                signal["reason"] = "vast account cannot pay"
                return signal
        instances = run([binary, "show", "instances", "--raw"], capture_output=True, text=True, timeout=45)
        if instances.returncode == 0 and instances.stdout.lstrip().startswith("["):
            data = json.loads(instances.stdout)
            running = [item for item in data if item.get("actual_status") == "running" or item.get("cur_state") == "running"]
            signal["active_jobs"] = len(running)
            signal["capacity_hint"] = "no running instances" if not running else f"{len(running)} running instance(s)"
        offers_result = self.offers(profile, limit=5)
        offers = offers_result.get("offers", [])
        signal["offer_query"] = offers_result.get("query", "")
        signal["offer_count"] = len(offers)
        signal["cheapest_offer"] = offers[0] if offers else None
        if not offers:
            signal["available"] = False
            signal["reason"] = "no matching offers"
            return signal
        max_runtime_minutes = float(profile.get("max_runtime_minutes", 60))
        max_cost_usd = float(profile.get("max_cost_usd", 0))
        cheapest_dph = float(offers[0].get("dph_total") or 0)
        estimated_cost = cheapest_dph * (max_runtime_minutes / 60)
        signal["estimated_max_runtime_cost_usd"] = estimated_cost
        signal["estimated_startup_seconds"] = 90
        max_startup = int(profile.get("max_startup_seconds", 0) or 0)
        if max_startup and signal["estimated_startup_seconds"] > max_startup:
            signal["startup_exceeds_profile_limit"] = True
            signal["reason"] = "healthy with matching offers; startup exceeds profile max_startup_seconds"
        if max_cost_usd and estimated_cost > max_cost_usd:
            signal["available"] = False
            signal["reason"] = "cheapest offer exceeds profile max_cost_usd"
        elif not signal.get("startup_exceeds_profile_limit"):
            signal["reason"] = "healthy with matching offers"
        return signal

    def plan(self, job: Job) -> dict[str, Any]:
        endpoint_name = f"gpu-job-{job.job_type}-{job.gpu_profile}"
        profile = {
            "min_vram_gb": job.metadata.get("min_vram_gb") or 16,
            "min_compute_cap": job.metadata.get("min_compute_cap") or 750,
        }
        offer_query = self.offers(profile, limit=1).get("query", "")
        return {
            "provider": self.name,
            "job_id": job.job_id,
            "mode": "instance/serverless plan",
            "worker_image": job.worker_image,
            "gpu_profile": job.gpu_profile,
            "input_uri": job.input_uri,
            "output_uri": job.output_uri,
            "serverless": {
                "endpoint_name": endpoint_name,
                "worker_contract": {
                    "dockerfile": "docker/asr-worker.Dockerfile",
                    "entrypoint": "gpu-job-asr-worker",
                    "artifact_contract": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
                    "command_template": [
                        "gpu-job-asr-worker",
                        "--job-id",
                        job.job_id,
                        "--artifact-dir",
                        "/workspace/artifacts",
                        "--gpu-profile",
                        job.gpu_profile,
                        "--input-uri",
                        "<staged-input-path>",
                        "--provider",
                        "vast",
                        "--model-name",
                        job.model or "large-v3",
                    ],
                },
                "create_endpoint_command": [
                    "vastai",
                    "create",
                    "endpoint",
                    "--endpoint_name",
                    endpoint_name,
                    "--cold_workers",
                    "0",
                    "--max_workers",
                    "1",
                    "--inactivity_timeout",
                    "60",
                    "--max_queue_time",
                    "30",
                    "--target_queue_time",
                    "10",
                    "--raw",
                ],
                "create_workergroup_command_template": [
                    "vastai",
                    "create",
                    "workergroup",
                    "--endpoint_name",
                    endpoint_name,
                    "--template_hash",
                    "<template-hash-required>",
                    "--test_workers",
                    "0",
                    "--cold_workers",
                    "0",
                    "--search_params",
                    offer_query,
                    "--raw",
                ],
                "cost_guard_policy": [
                    "cold_workers must be 0 for no-warm-capacity canaries.",
                    "max_workers must start at 1.",
                    "gpu-job guard treats any endpoint/workergroup as a blocking resource until it is deleted.",
                ],
            },
            "notes": [
                "v0 does not launch paid instances automatically.",
                "Serverless creation still creates account resources; guard must be clean before and after.",
                "Use docker/asr-worker.Dockerfile for ASR templates unless a provider-specific image is supplied.",
                "Prefer explicit max dollars per hour and max runtime guards.",
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
        allow_direct = bool(job.metadata.get("allow_vast_direct_instance_smoke"))
        if not allow_direct:
            job.status = "failed"
            job.error = "vast direct instance execution is disabled; use serverless/workergroup with a known-good template"
            job.exit_code = 2
            store.save(job)
            return job
        if job.job_type != "smoke":
            job.status = "failed"
            job.error = "vast execute currently supports smoke jobs only"
            job.exit_code = 2
            store.save(job)
            return job

        binary = vast_bin()
        artifact_dir = store.artifact_dir(job.job_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        start = now_unix()
        job.started_at = start
        job.status = "running"
        store.save(job)
        instance_id = ""
        created_ids: set[str] = set()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        try:
            profile = {
                "min_vram_gb": job.metadata.get("min_vram_gb", 16),
                "min_compute_cap": job.metadata.get("min_compute_cap", 750),
            }
            offers = self.offers(profile, limit=1).get("offers", [])
            if not offers:
                raise RuntimeError("no Vast.ai offer for smoke job")
            offer = offers[0]
            max_startup = int(job.limits.get("max_startup_seconds") or job.metadata.get("max_startup_seconds") or 60)
            command = (
                "mkdir -p /workspace/gpu-job-smoke; "
                "echo GPU_JOB_SMOKE_START; "
                "nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader; "
                "echo GPU_JOB_SMOKE_DONE; "
                "sleep 20"
            )
            label = f"gpu-job:{job.job_id}"
            before_ids = self._instance_ids()
            create = run(
                [
                    binary,
                    "create",
                    "instance",
                    str(offer["id"]),
                    "--image",
                    "nvidia/cuda:12.4.1-base-ubuntu22.04",
                    "--disk",
                    "10",
                    "--label",
                    label,
                    "--onstart-cmd",
                    command,
                    "--raw",
                    "--cancel-unavail",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            stdout_lines.append(create.stdout)
            stderr_lines.append(create.stderr)
            if create.returncode != 0:
                raise RuntimeError(create.stderr.strip() or create.stdout.strip() or "vast create instance failed")
            raw = create.stdout.strip()
            if raw:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = ast.literal_eval(raw)
                parsed_id = data.get("new_contract") or data.get("id") or data.get("instance", {}).get("id")
                if parsed_id:
                    created_ids.add(str(parsed_id))
            deadline_for_id = time.time() + 20
            while time.time() < deadline_for_id:
                labelled = self._instances_by_label(label)
                created_ids.update(str(item["id"]) for item in labelled if item.get("id") is not None)
                created_ids.update(self._instance_ids() - before_ids)
                if created_ids:
                    break
                time.sleep(2)
            instance_id = sorted(created_ids)[0] if created_ids else ""
            if not instance_id:
                raise RuntimeError(f"could not detect Vast instance id; create stdout={raw!r}")
            job.provider_job_id = instance_id
            job.metadata["vast_offer"] = offer
            job.metadata["vast_created_ids"] = sorted(created_ids)
            store.save(job)

            logs_text = ""
            deadline = time.time() + max_startup
            while time.time() < deadline:
                logs = run([binary, "logs", instance_id, "--tail", "200"], capture_output=True, text=True, timeout=30)
                logs_text = logs.stdout + logs.stderr
                if "GPU_JOB_SMOKE_DONE" in logs_text:
                    break
                time.sleep(5)
            (artifact_dir / "stdout.log").write_text(logs_text)
            (artifact_dir / "stderr.log").write_text("\n".join(s for s in stderr_lines if s))
            if "GPU_JOB_SMOKE_DONE" not in logs_text:
                raise RuntimeError(f"vast smoke did not finish within {max_startup}s")
            gpu_line = ""
            for line in logs_text.splitlines():
                if "," in line and ("MiB" in line or "RTX" in line or "Tesla" in line or "NVIDIA" in line):
                    gpu_line = line.strip()
                    break
            result = {
                "provider": self.name,
                "job_id": job.job_id,
                "instance_id": instance_id,
                "nvidia_smi_exit_code": 0 if gpu_line else 1,
                "nvidia_smi_stdout": gpu_line,
                "offer": offer,
            }
            metrics = {
                "provider": self.name,
                "job_id": job.job_id,
                "instance_id": instance_id,
                "runtime_seconds": max(0, now_unix() - start),
                "estimated_hourly_usd": offer.get("dph_total"),
                "estimated_cost_usd": (float(offer.get("dph_total") or 0) * max(0, now_unix() - start)) / 3600,
            }
            verify_data = {
                "ok": bool(gpu_line),
                "required": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
                "missing": [],
            }
            (artifact_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
            (artifact_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
            (artifact_dir / "verify.json").write_text(json.dumps(verify_data, indent=2, sort_keys=True) + "\n")
        except Exception as exc:
            job.error = str(exc)
        finally:
            label = f"gpu-job:{job.job_id}"
            for item in self._instances_by_label(label):
                if item.get("id") is not None:
                    created_ids.add(str(item["id"]))
            destroy_results = []
            for target_id in sorted(created_ids):
                destroy = run([binary, "destroy", "instance", target_id, "-y", "--raw"], capture_output=True, text=True, timeout=60)
                destroy_results.append(
                    {
                        "id": target_id,
                        "stdout": destroy.stdout.strip(),
                        "stderr": destroy.stderr.strip(),
                        "exit_code": destroy.returncode,
                    }
                )
            if destroy_results:
                job.metadata["vast_destroy_results"] = destroy_results
            verify = verify_artifacts(artifact_dir)
            job.finished_at = now_unix()
            job.runtime_seconds = max(0, job.finished_at - start)
            job.artifact_count = verify["artifact_count"]
            job.artifact_bytes = verify["artifact_bytes"]
            job.exit_code = 0 if not job.error and verify["ok"] else 1
            job.status = "succeeded" if job.exit_code == 0 else "failed"
            store.save(job)
        return job

    def _instances(self) -> list[dict[str, Any]]:
        binary = vast_bin()
        proc = run([binary, "show", "instances", "--raw"], capture_output=True, text=True, timeout=45)
        if proc.returncode == 0 and proc.stdout.lstrip().startswith("["):
            return json.loads(proc.stdout)
        return []

    def _instance_ids(self) -> set[str]:
        return {str(item["id"]) for item in self._instances() if item.get("id") is not None}

    def _instances_by_label(self, label: str) -> list[dict[str, Any]]:
        return [item for item in self._instances() if item.get("label") == label]

    def offers(self, profile: dict[str, Any], limit: int = 5) -> dict[str, Any]:
        binary = vast_bin()
        if not binary:
            raise ValueError("vastai binary not found")
        min_vram = int(profile.get("min_vram_gb", 16))
        min_compute = int(profile.get("min_compute_cap", 750))
        query = f"gpu_ram>={min_vram} compute_cap>={min_compute} num_gpus=1 rented=False reliability>0.95"
        proc = run(
            [binary, "search", "offers", query, "--raw", "--limit", str(limit), "-o", "dph_total"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise ValueError(proc.stderr.strip() or proc.stdout.strip() or "vastai search offers failed")
        data = json.loads(proc.stdout)
        offers = []
        for item in data[:limit]:
            offers.append(
                {
                    "id": item.get("id"),
                    "gpu_name": item.get("gpu_name"),
                    "gpu_ram_mb": item.get("gpu_ram"),
                    "compute_cap": item.get("compute_cap"),
                    "dph_total": item.get("dph_total"),
                    "reliability": item.get("reliability"),
                    "geolocation": item.get("geolocation"),
                    "cuda_max_good": item.get("cuda_max_good"),
                    "disk_space_gb": item.get("disk_space"),
                }
            )
        return {"provider": self.name, "query": query, "limit": limit, "offers": offers}

    def recommended_templates(self) -> dict[str, Any]:
        binary = vast_bin()
        wanted = {"NVIDIA CUDA", "vLLM", "vLLM Omni", "Ollama", "Whisper WebUI & API"}
        if not binary:
            return {"ok": False, "error": "vastai binary not found", "templates": []}
        proc = run(
            [binary, "search", "templates", "recommended=True", "--raw"],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if proc.returncode != 0:
            return {"ok": False, "error": proc.stderr.strip() or proc.stdout.strip(), "templates": []}
        try:
            rows = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": str(exc), "templates": []}
        templates = [
            {
                "id": row.get("id"),
                "hash_id": row.get("hash_id"),
                "name": row.get("name"),
                "image": row.get("image"),
                "tag": row.get("tag"),
                "recommended_disk_space": row.get("recommended_disk_space"),
            }
            for row in rows
            if row.get("name") in wanted
        ]
        return {"ok": True, "templates": templates}

    def cost_guard(self) -> dict[str, Any]:
        health = self.doctor()
        if not health.get("ok"):
            return {
                "provider": self.name,
                "ok": False,
                "billable_resources": [],
                "estimated_hourly_usd": 0.0,
                "reason": "vast health check failed",
                "health": health,
            }
        binary = vast_bin()
        resources: list[dict[str, Any]] = []
        hourly = 0.0

        instances = run([binary, "show", "instances-v1", "--raw"], capture_output=True, text=True, timeout=60)
        if instances.returncode == 0 and instances.stdout.lstrip().startswith("{"):
            data = json.loads(instances.stdout).get("instances", [])
            for item in data:
                inst = item.get("instance") or {}
                cost = float(inst.get("totalHour") or 0)
                if cost > 0 or item.get("cur_state") not in {None, "deleted"}:
                    resources.append(
                        {
                            "type": "instance",
                            "id": item.get("id"),
                            "state": item.get("cur_state"),
                            "actual_status": item.get("actual_status"),
                            "gpu_name": item.get("gpu_name"),
                            "num_gpus": item.get("num_gpus"),
                            "image": item.get("image_uuid"),
                            "estimated_hourly_usd": cost,
                        }
                    )
                    hourly += cost

        for command, rtype in [
            (["show", "endpoints", "--raw"], "endpoint"),
            (["show", "workergroups", "--raw"], "workergroup"),
            (["show", "volumes", "--raw"], "volume"),
            (["show", "scheduled-jobs", "--raw"], "scheduled_job"),
        ]:
            proc = run([binary, *command], capture_output=True, text=True, timeout=45)
            if proc.returncode == 0 and proc.stdout.lstrip().startswith("["):
                for item in json.loads(proc.stdout):
                    resources.append(
                        {
                            "type": rtype,
                            "id": item.get("id"),
                            "name": item.get("endpoint_name") or item.get("label"),
                            "raw_state": item.get("endpoint_state") or item.get("status"),
                        }
                    )

        return {
            "provider": self.name,
            "ok": not resources,
            "billable_resources": resources,
            "estimated_hourly_usd": hourly,
            "reason": "no Vast.ai billable resources" if not resources else "Vast.ai resources present",
        }
