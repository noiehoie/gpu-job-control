from __future__ import annotations

from pathlib import Path
from shutil import which
from subprocess import run
from typing import Any
from urllib.parse import urlparse
import json
import ast
import os
import shlex
import time
import urllib.error
import urllib.request

from gpu_job.models import Job, now_unix
from gpu_job.execution_plan import build_execution_plan
from gpu_job.providers.base import Provider
from gpu_job.store import JobStore
from gpu_job.timing import enter_phase, exit_phase, instant_phase
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
        execution_plan = build_execution_plan(job, self.name)
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
            "execution_plan": execution_plan,
            "gpu_profile": job.gpu_profile,
            "input_uri": job.input_uri,
            "output_uri": job.output_uri,
            "serverless": {
                "endpoint_name": endpoint_name,
                "configured_contract": self._serverless_contract_from_job(job),
                "worker_contract": {
                    "dockerfile": "docker/asr-worker.Dockerfile",
                    "entrypoint": execution_plan["entrypoint"],
                    "artifact_contract": execution_plan["artifact_contract"],
                    "command_template": execution_plan["command"],
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
        if job.job_type == "asr":
            return self._submit_direct_instance_asr(job, store)
        if job.job_type == "gpu_task" and str(job.metadata.get("provider_module_id") or "") == "vast_pyworker_serverless":
            return self._submit_pyworker_serverless_gpu_task(job, store)
        allow_direct = bool(
            job.metadata.get("allow_vast_direct_instance_gpu_task")
            if job.job_type == "gpu_task"
            else job.metadata.get("allow_vast_direct_instance_smoke")
        )
        if not allow_direct:
            job.status = "failed"
            job.error = "vast direct instance execution is disabled; use an explicit allow flag or serverless/workergroup endpoint"
            job.exit_code = 2
            store.save(job)
            return job
        if job.job_type not in {"smoke", "gpu_task"}:
            job.status = "failed"
            job.error = "vast execute currently supports smoke, asr, and gpu_task jobs only"
            job.exit_code = 2
            store.save(job)
            return job

        binary = vast_bin()
        artifact_dir = store.artifact_dir(job.job_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        start = now_unix()
        job.started_at = None
        job.status = "starting"
        job.metadata["startup_started_at"] = start
        store.save(job)
        instance_id = ""
        created_ids: set[str] = set()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        reserving_open = False
        image_open = False
        running_open = False
        collecting_open = False
        try:
            profile = {
                "min_vram_gb": job.metadata.get("min_vram_gb", 16),
                "min_compute_cap": job.metadata.get("min_compute_cap", 750),
            }
            enter_phase(job, "reserving_workspace", provider=self.name)
            reserving_open = True
            store.save(job)
            offers = self.offers(profile, limit=1).get("offers", [])
            if not offers:
                exit_phase(job, "reserving_workspace", provider=self.name, status="failed", error_class="provider_backpressure")
                reserving_open = False
                store.save(job)
                raise RuntimeError("no Vast.ai offer for smoke job")
            offer = offers[0]
            exit_phase(job, "reserving_workspace", provider=self.name, status="ok")
            reserving_open = False
            store.save(job)
            max_startup = int(job.limits.get("max_startup_seconds") or job.metadata.get("max_startup_seconds") or 60)
            execution_plan = dict((job.metadata.get("provider_plan") or {}).get("execution_plan") or build_execution_plan(job, self.name))
            marker = "GPU_JOB_TASK_DONE" if job.job_type == "gpu_task" else "GPU_JOB_SMOKE_DONE"
            if job.job_type == "gpu_task":
                planned_command = [str(item) for item in (execution_plan.get("command") or ["true"])]
                worker_command = " ".join(shlex.quote(item) for item in planned_command)
                command = (
                    "mkdir -p /workspace/gpu-job-task; "
                    "echo GPU_JOB_TASK_START; "
                    "nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader; "
                    "echo GPU_JOB_COMMAND_START; "
                    f"{worker_command}; "
                    "echo GPU_JOB_COMMAND_EXIT:$?; "
                    "echo GPU_JOB_TASK_DONE; "
                    "sleep 20"
                )
            else:
                command = (
                    "mkdir -p /workspace/gpu-job-smoke; "
                    "echo GPU_JOB_SMOKE_START; "
                    "nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader; "
                    "echo GPU_JOB_SMOKE_DONE; "
                    "sleep 20"
                )
            label = f"gpu-job:{job.job_id}"
            job.metadata["vast_instance_label"] = label
            job.metadata["vast_lifecycle_evidence"] = {
                "offer_id": str(offer.get("id") or ""),
                "instance_id": "",
                "label": label,
                "create_detection": "",
                "last_known_vast_state": "",
            }
            enter_phase(job, "image_materialization", provider=self.name)
            image_open = True
            store.save(job)
            before_ids = self._instance_ids()
            create = run(
                [
                    binary,
                    "create",
                    "instance",
                    str(offer["id"]),
                    "--image",
                    str(
                        job.metadata.get("vast_image")
                        or (job.worker_image if job.worker_image != "auto" else "nvidia/cuda:12.4.1-base-ubuntu22.04")
                    ),
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
                    job.metadata["vast_lifecycle_evidence"]["create_detection"] = "create_stdout"
            deadline_for_id = time.time() + 20
            while time.time() < deadline_for_id:
                labelled = self._instances_by_label(label)
                created_ids.update(str(item["id"]) for item in labelled if item.get("id") is not None)
                if labelled:
                    job.metadata["vast_lifecycle_evidence"]["last_known_vast_state"] = str(
                        labelled[0].get("actual_status") or labelled[0].get("cur_state") or ""
                    )
                    if not job.metadata["vast_lifecycle_evidence"].get("create_detection"):
                        job.metadata["vast_lifecycle_evidence"]["create_detection"] = "label_poll"
                created_ids.update(self._instance_ids() - before_ids)
                if created_ids and not job.metadata["vast_lifecycle_evidence"].get("create_detection"):
                    job.metadata["vast_lifecycle_evidence"]["create_detection"] = "instance_delta"
                if created_ids:
                    break
                time.sleep(2)
            instance_id = sorted(created_ids)[0] if created_ids else ""
            if not instance_id:
                raise RuntimeError(f"could not detect Vast instance id; create stdout={raw!r}")
            job.provider_job_id = instance_id
            job.metadata["vast_offer"] = offer
            job.metadata["vast_created_ids"] = sorted(created_ids)
            job.metadata["vast_lifecycle_evidence"]["instance_id"] = instance_id
            exit_phase(job, "image_materialization", provider=self.name, status="ready")
            image_open = False
            store.save(job)

            logs_text = ""
            instant_phase(job, "starting_worker", provider=self.name, status="ok")
            job.status = "running"
            job.started_at = now_unix()
            job.metadata["worker_started_at"] = job.started_at
            store.save(job)
            enter_phase(job, "running_worker", provider=self.name)
            running_open = True
            store.save(job)
            deadline = time.time() + max_startup
            while time.time() < deadline:
                logs = run([binary, "logs", instance_id, "--tail", "200"], capture_output=True, text=True, timeout=30)
                logs_text = logs.stdout + logs.stderr
                if marker in logs_text:
                    break
                time.sleep(5)
            exit_phase(
                job,
                "running_worker",
                provider=self.name,
                status="ok" if marker in logs_text else "failed",
                error_class="" if marker in logs_text else "worker_failed",
            )
            running_open = False
            store.save(job)
            enter_phase(job, "collecting_artifacts", provider=self.name)
            collecting_open = True
            store.save(job)
            (artifact_dir / "stdout.log").write_text(logs_text)
            (artifact_dir / "stderr.log").write_text("\n".join(s for s in stderr_lines if s))
            if marker not in logs_text:
                raise RuntimeError(f"vast {job.job_type} did not finish within {max_startup}s")
            gpu_line = ""
            command_exit_code = 0
            for line in logs_text.splitlines():
                if "," in line and ("MiB" in line or "RTX" in line or "Tesla" in line or "NVIDIA" in line):
                    gpu_line = line.strip()
                    break
            for line in logs_text.splitlines():
                if line.startswith("GPU_JOB_COMMAND_EXIT:"):
                    try:
                        command_exit_code = int(line.split(":", 1)[1])
                    except ValueError:
                        command_exit_code = 1
            result = {
                "provider": self.name,
                "job_id": job.job_id,
                "job_type": job.job_type,
                "lane": "vast_instance",
                "ok": bool(gpu_line) and command_exit_code == 0,
                "instance_id": instance_id,
                "command": execution_plan.get("command") or [],
                "command_exit_code": command_exit_code,
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
                "gpu_memory_used_mb": 1 if gpu_line else 0,
                "gpu_name": gpu_line or None,
            }
            probe_info = {
                "provider": self.name,
                "worker_image": "nvidia/cuda:12.4.1-base-ubuntu22.04",
                "loaded_model_id": job.model or "",
                "gpu_name": gpu_line or None,
                "gpu_count": 1 if gpu_line else None,
                "gpu_memory_used_mb": 1 if gpu_line else None,
                "offer_id": offer.get("id"),
                "template_mode": "direct_instance_gpu_task" if job.job_type == "gpu_task" else "direct_instance_smoke",
                "serverless_contract": self._serverless_contract_from_job(job),
            }
            verify_data = {
                "ok": bool(result["ok"]),
                "required": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
                "missing": [],
            }
            (artifact_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
            (artifact_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
            (artifact_dir / "verify.json").write_text(json.dumps(verify_data, indent=2, sort_keys=True) + "\n")
            (artifact_dir / "probe_info.json").write_text(json.dumps(probe_info, indent=2, sort_keys=True) + "\n")
            exit_phase(job, "collecting_artifacts", provider=self.name, status="ok")
            collecting_open = False
            store.save(job)
        except Exception as exc:
            if collecting_open:
                exit_phase(job, "collecting_artifacts", provider=self.name, status="failed", error_class="artifact_collection_failed")
            if running_open:
                exit_phase(job, "running_worker", provider=self.name, status="failed", error_class="worker_failed")
            if image_open:
                exit_phase(job, "image_materialization", provider=self.name, status="failed", error_class="startup_failed")
            if reserving_open:
                exit_phase(job, "reserving_workspace", provider=self.name, status="failed", error_class="provider_backpressure")
            job.error = str(exc)
            store.save(job)
        finally:
            enter_phase(job, "cleaning_up", provider=self.name)
            store.save(job)
            label = f"gpu-job:{job.job_id}"
            for item in self._instances_by_label(label):
                if item.get("id") is not None:
                    created_ids.add(str(item["id"]))
            destroy_results = []
            cleanup_ok = True
            for target_id in sorted(created_ids):
                try:
                    destroy = run([binary, "destroy", "instance", target_id, "-y", "--raw"], capture_output=True, text=True, timeout=60)
                    cleanup_ok = cleanup_ok and destroy.returncode == 0
                    destroy_results.append(
                        {
                            "id": target_id,
                            "stdout": destroy.stdout.strip(),
                            "stderr": destroy.stderr.strip(),
                            "exit_code": destroy.returncode,
                        }
                    )
                except Exception as destroy_exc:
                    cleanup_ok = False
                    destroy_results.append(
                        {
                            "id": target_id,
                            "stdout": "",
                            "stderr": str(destroy_exc),
                            "exit_code": 124,
                        }
                    )
            if destroy_results:
                job.metadata["vast_destroy_results"] = destroy_results
            if not cleanup_ok and not job.error:
                job.error = "Vast cleanup failed"
            exit_phase(
                job,
                "cleaning_up",
                provider=self.name,
                status="ok" if cleanup_ok else "failed",
                error_class="" if cleanup_ok else "cleanup_failed",
            )
            verify = verify_artifacts(artifact_dir)
            job.finished_at = now_unix()
            job.metadata["total_elapsed_seconds"] = max(0, job.finished_at - start)
            job.runtime_seconds = max(0, job.finished_at - int(job.started_at or start))
            job.artifact_count = verify["artifact_count"]
            job.artifact_bytes = verify["artifact_bytes"]
            job.exit_code = 0 if not job.error and verify["ok"] else 1
            job.status = "succeeded" if job.exit_code == 0 else "failed"
            store.save(job)
        return job

    def _submit_direct_instance_asr(self, job: Job, store: JobStore) -> Job:
        binary = vast_bin()
        if not binary:
            job.status = "failed"
            job.error = "vastai binary not found"
            job.exit_code = 127
            store.save(job)
            return job
        artifact_dir = store.artifact_dir(job.job_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        start = now_unix()
        job.started_at = None
        job.status = "starting"
        job.metadata["startup_started_at"] = start
        store.save(job)
        label = f"gpu-job:{job.job_id}"
        instance_id = ""
        created_ids: set[str] = set()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        try:
            input_path = _resolve_vast_local_input(job.input_uri)
            metadata_input = job.metadata.get("input") if isinstance(job.metadata.get("input"), dict) else {}
            diarize = bool(metadata_input.get("diarize") or metadata_input.get("speaker_diarization"))
            language = str(metadata_input.get("language") or "ja")
            model_name = str(metadata_input.get("model") or job.model or "large-v3")
            compute_type = str(metadata_input.get("compute_type") or "int8_float16")
            speaker_model = str(metadata_input.get("speaker_model") or "pyannote/speaker-diarization-3.1")
            if job.metadata.get("allow_runtime_dependency_install") or job.metadata.get("debug_runtime_dependency_install"):
                raise RuntimeError(
                    "Vast ASR runtime dependency install is disabled for production execution; "
                    "build and verify the ASR image contract before allocating cloud GPU"
                )
            execution_plan = build_execution_plan(job, self.name)
            image_status = dict(execution_plan.get("image_contract") or {})
            if not image_status.get("ok"):
                raise RuntimeError(
                    "Vast ASR production execution requires a verified prebuilt image contract; "
                    f"image_contract={image_status.get('contract_id') or 'missing'} "
                    f"status={image_status.get('status') or 'unknown'}"
                )
            provider_image = str(job.metadata.get("vast_image") or execution_plan.get("provider_image") or "")
            if image_status.get("ok") and not provider_image:
                raise RuntimeError(
                    "Vast ASR production execution requires a provider-distributed image; "
                    f"image_contract={image_status.get('contract_id') or 'missing'} provider=vast"
                )
            image_login = _vast_image_login_from_distribution(
                dict(execution_plan.get("image_distribution") or {}),
                metadata=dict(job.metadata or {}),
            )
            hf_token = _secret_token_from_env("hf_token") if diarize else ""
            if diarize and not hf_token:
                raise RuntimeError(
                    "speaker diarization requires HF_TOKEN, HUGGINGFACE_TOKEN, or HUGGING_FACE_HUB_TOKEN before Vast GPU allocation"
                )
            max_runtime = int(job.limits.get("max_runtime_minutes", 60)) * 60
            max_startup = int(job.metadata.get("max_startup_seconds") or 300)
            min_worker_seconds = int(job.metadata.get("min_worker_seconds") or (120 if diarize else 60))
            if max_runtime <= max_startup + min_worker_seconds:
                raise RuntimeError(
                    "Vast ASR max_runtime_minutes must exceed max_startup_seconds plus min_worker_seconds "
                    f"before GPU allocation: max_runtime_seconds={max_runtime} "
                    f"max_startup_seconds={max_startup} min_worker_seconds={min_worker_seconds}"
                )
            max_startup_attempts = max(1, int(job.metadata.get("max_startup_attempts") or 1))
            max_cost = float(job.limits.get("max_cost_usd") or job.metadata.get("max_estimated_cost_usd") or 1.0)
            enter_phase(job, "reserving_workspace", provider=self.name)
            store.save(job)
            offers = self.offers(
                {
                    "min_vram_gb": job.metadata.get("min_vram_gb") or (24 if diarize else 16),
                    "min_compute_cap": job.metadata.get("min_compute_cap") or 800,
                },
                limit=10,
            ).get("offers", [])
            if not offers:
                exit_phase(job, "reserving_workspace", provider=self.name, status="failed", error_class="provider_backpressure")
                store.save(job)
                raise RuntimeError("no Vast.ai ASR offer")
            exit_phase(job, "reserving_workspace", provider=self.name, status="ok")
            store.save(job)
            image = provider_image
            onstart = self._asr_onstart_command()
            startup_attempts: list[dict[str, Any]] = []
            ssh_parts: list[str] = []
            offer = None
            total_deadline = start + max_runtime
            for attempt_index, candidate in enumerate(offers[:max_startup_attempts], start=1):
                estimated_max_cost = float(candidate.get("dph_total") or 0) * (max_runtime / 3600)
                attempt: dict[str, Any] = {
                    "attempt": attempt_index,
                    "started_at": now_unix(),
                    "offer_id": candidate.get("id"),
                    "gpu_name": candidate.get("gpu_name"),
                    "dph_total": candidate.get("dph_total"),
                    "estimated_max_cost_usd": round(estimated_max_cost, 6),
                }
                startup_attempts.append(attempt)
                remaining_budget = max(0, int(total_deadline - time.time()))
                if remaining_budget <= max_startup + min_worker_seconds:
                    attempt["status"] = "skipped_deadline"
                    attempt["reason"] = (
                        f"remaining runtime budget {remaining_budget}s cannot cover startup {max_startup}s "
                        f"and worker floor {min_worker_seconds}s"
                    )
                    continue
                if max_cost and estimated_max_cost > max_cost:
                    attempt["status"] = "skipped_budget"
                    attempt["reason"] = f"estimated max cost ${estimated_max_cost:.4f} exceeds max_cost_usd ${max_cost:.4f}"
                    continue
                attempt_created_ids: set[str] = set()
                try:
                    enter_phase(job, "image_materialization", attempt=attempt_index, provider=self.name)
                    store.save(job)
                    before_ids = self._instance_ids()
                    create = run(
                        [
                            binary,
                            "create",
                            "instance",
                            str(candidate["id"]),
                            "--image",
                            image,
                            "--disk",
                            str(int(job.metadata.get("vast_disk_gb") or 40)),
                            "--ssh",
                            "--direct",
                            "--label",
                            label,
                            "--onstart-cmd",
                            onstart,
                            *(_vast_login_args(image_login)),
                            "--raw",
                            "--cancel-unavail",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=90,
                    )
                    stdout_lines.append(create.stdout)
                    stderr_lines.append(create.stderr)
                    if create.returncode != 0:
                        raise RuntimeError(create.stderr.strip() or create.stdout.strip() or "vast create instance failed")
                    parsed_id = _parse_vast_instance_id(create.stdout)
                    if parsed_id:
                        attempt_created_ids.add(parsed_id)
                        created_ids.add(parsed_id)
                        attempt["instance_created_at"] = now_unix()
                    deadline_for_id = time.time() + 45
                    while time.time() < deadline_for_id:
                        labelled = self._instances_by_label(label)
                        attempt_created_ids.update(str(item["id"]) for item in labelled if item.get("id") is not None)
                        attempt_created_ids.update(self._instance_ids() - before_ids)
                        created_ids.update(attempt_created_ids)
                        if attempt_created_ids:
                            break
                        time.sleep(2)
                    instance_id = sorted(attempt_created_ids)[0] if attempt_created_ids else ""
                    if not instance_id:
                        raise RuntimeError(f"could not detect Vast instance id; create stdout={create.stdout!r}")
                    attempt["instance_id"] = instance_id
                    job.provider_job_id = instance_id
                    job.metadata["vast_offer"] = candidate
                    job.metadata["vast_instance_label"] = label
                    job.metadata["vast_startup_attempts"] = startup_attempts
                    store.save(job)
                    ssh_wait_started_at = now_unix()
                    attempt["ssh_wait_started_at"] = ssh_wait_started_at
                    ssh_parts = self._wait_ssh_parts(instance_id, timeout_seconds=max_startup)
                    attempt["status"] = "ready"
                    attempt["ssh_ready_at"] = now_unix()
                    attempt["ssh_wait_seconds"] = max(0, int(attempt["ssh_ready_at"] - ssh_wait_started_at))
                    exit_phase(job, "image_materialization", attempt=attempt_index, provider=self.name, status="ready")
                    store.save(job)
                    offer = candidate
                    break
                except Exception as exc:
                    attempt["status"] = "startup_failed"
                    attempt["error"] = str(exc)
                    attempt["finished_at"] = now_unix()
                    exit_phase(
                        job,
                        "image_materialization",
                        attempt=attempt_index,
                        provider=self.name,
                        status="failed",
                        error_class="startup_failed",
                    )
                    store.save(job)
                    for target_id in sorted(attempt_created_ids):
                        try:
                            destroy = run(
                                [binary, "destroy", "instance", target_id, "-y", "--raw"], capture_output=True, text=True, timeout=90
                            )
                            attempt.setdefault("destroy_results", []).append(
                                {
                                    "id": target_id,
                                    "stdout": destroy.stdout.strip(),
                                    "stderr": destroy.stderr.strip(),
                                    "exit_code": destroy.returncode,
                                }
                            )
                        except Exception as destroy_exc:
                            attempt.setdefault("destroy_results", []).append(
                                {
                                    "id": target_id,
                                    "stdout": "",
                                    "stderr": str(destroy_exc),
                                    "exit_code": 124,
                                }
                            )
                    continue
            job.metadata["vast_startup_attempts"] = startup_attempts
            store.save(job)
            if offer is None or not ssh_parts:
                raise RuntimeError(f"Vast ASR startup failed for all eligible offers: {startup_attempts}")
            scp_parts = _scp_parts_from_ssh_parts(ssh_parts)
            remote_root = "/workspace/gpu-job-asr"
            remote_input = f"{remote_root}/input/{input_path.name}"
            enter_phase(job, "staging_input", provider=self.name)
            store.save(job)
            _run_checked([*ssh_parts, f"mkdir -p {remote_root}/input {remote_root}/out"], timeout=60)
            _run_checked([*scp_parts, str(input_path), f"{ssh_parts[-1]}:{remote_input}"], timeout=300)
            if diarize and hf_token:
                token_path = artifact_dir / ".hf_token"
                token_path.write_text(hf_token, encoding="utf-8")
                try:
                    _run_checked([*scp_parts, str(token_path), f"{ssh_parts[-1]}:{remote_root}/hf_token"], timeout=60)
                    _run_checked([*ssh_parts, f"chmod 600 {remote_root}/hf_token"], timeout=30)
                finally:
                    token_path.unlink(missing_ok=True)
            exit_phase(job, "staging_input", provider=self.name, status="ok")
            store.save(job)
            remote_cmd = (
                f"cd {remote_root} && "
                "if [ -f hf_token ]; then export HF_TOKEN=$(cat hf_token) HUGGINGFACE_TOKEN=$(cat hf_token); fi && "
                f"gpu-job-asr-worker --job-id {shlex.quote(job.job_id)} "
                f"--artifact-dir {remote_root}/out "
                f"--gpu-profile {shlex.quote(job.gpu_profile)} "
                f"--input-uri {shlex.quote(remote_input)} "
                f"--provider vast "
                f"--model-name {shlex.quote(model_name)} "
                f"--language {shlex.quote(language)} "
                f"--compute-type {shlex.quote(compute_type)} "
                + ("--diarize " if diarize else "")
                + (f"--speaker-model {shlex.quote(speaker_model)} " if diarize else "")
            )
            instant_phase(job, "starting_worker", provider=self.name, status="ok")
            job.status = "running"
            job.started_at = now_unix()
            job.metadata["worker_started_at"] = job.started_at
            store.save(job)
            enter_phase(job, "running_worker", provider=self.name)
            store.save(job)
            remote = run([*ssh_parts, remote_cmd], capture_output=True, text=True, timeout=max_runtime)
            exit_phase(
                job,
                "running_worker",
                provider=self.name,
                status="ok" if remote.returncode == 0 else "failed",
                error_class="" if remote.returncode == 0 else "worker_failed",
            )
            store.save(job)
            stdout_lines.append(remote.stdout)
            stderr_lines.append(remote.stderr)
            enter_phase(job, "collecting_artifacts", provider=self.name)
            store.save(job)
            _run_checked([*scp_parts, "-r", f"{ssh_parts[-1]}:{remote_root}/out/.", str(artifact_dir)], timeout=300)
            _augment_provider_image_probe_artifacts(
                artifact_dir,
                provider_image=image,
                image_contract_id=str(image_status.get("contract_id") or ""),
            )
            exit_phase(job, "collecting_artifacts", provider=self.name, status="ok")
            store.save(job)
            if remote.returncode != 0:
                raise RuntimeError(remote.stderr.strip() or remote.stdout.strip() or f"Vast ASR worker failed with {remote.returncode}")
        except Exception as exc:
            job.error = str(exc)
        finally:
            enter_phase(job, "cleaning_up", provider=self.name)
            store.save(job)
            for item in self._instances_by_label(label):
                if item.get("id") is not None:
                    created_ids.add(str(item["id"]))
            destroy_results = []
            cleanup_ok = True
            for target_id in sorted(created_ids):
                try:
                    destroy = run([binary, "destroy", "instance", target_id, "-y", "--raw"], capture_output=True, text=True, timeout=90)
                    cleanup_ok = cleanup_ok and destroy.returncode == 0
                    destroy_results.append(
                        {
                            "id": target_id,
                            "stdout": destroy.stdout.strip(),
                            "stderr": destroy.stderr.strip(),
                            "exit_code": destroy.returncode,
                        }
                    )
                except Exception as destroy_exc:
                    cleanup_ok = False
                    destroy_results.append(
                        {
                            "id": target_id,
                            "stdout": "",
                            "stderr": str(destroy_exc),
                            "exit_code": 124,
                        }
                    )
            if not (artifact_dir / "stdout.log").is_file():
                (artifact_dir / "stdout.log").write_text("\n".join(item for item in stdout_lines if item), encoding="utf-8")
            if not (artifact_dir / "stderr.log").is_file():
                (artifact_dir / "stderr.log").write_text("\n".join(item for item in stderr_lines if item), encoding="utf-8")
            if destroy_results:
                job.metadata["vast_destroy_results"] = destroy_results
            if not cleanup_ok and not job.error:
                job.error = "Vast cleanup failed"
            exit_phase(
                job,
                "cleaning_up",
                provider=self.name,
                status="ok" if cleanup_ok else "failed",
                error_class="" if cleanup_ok else "cleanup_failed",
            )
            verify = verify_artifacts(artifact_dir, require_gpu_utilization=True)
            job.finished_at = now_unix()
            job.metadata["total_elapsed_seconds"] = max(0, job.finished_at - start)
            job.runtime_seconds = max(0, job.finished_at - int(job.started_at or start))
            hourly_usd = float((job.metadata.get("vast_offer") or {}).get("dph_total") or 0)
            job.metadata["vast_runtime_cost"] = {
                "estimated_hourly_usd": hourly_usd,
                "runtime_seconds": job.runtime_seconds,
                "estimated_cost_usd": round(hourly_usd * (job.runtime_seconds / 3600), 6),
            }
            job.artifact_count = verify["artifact_count"]
            job.artifact_bytes = verify["artifact_bytes"]
            job.exit_code = 0 if not job.error and verify["ok"] else 1
            job.status = "succeeded" if job.exit_code == 0 else "failed"
            store.save(job)
        return job

    def _submit_pyworker_serverless_gpu_task(self, job: Job, store: JobStore) -> Job:
        artifact_dir = store.artifact_dir(job.job_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        start = now_unix()
        job.started_at = start
        job.status = "running"
        store.save(job)
        endpoint_url = str(job.metadata.get("vast_serverless_url") or os.getenv("VAST_PYWORKER_ENDPOINT_URL", "")).strip()
        endpoint_id = str(job.metadata.get("vast_endpoint_id") or os.getenv("VAST_PYWORKER_ENDPOINT_ID", "")).strip()
        workergroup_id = str(job.metadata.get("vast_workergroup_id") or os.getenv("VAST_PYWORKER_WORKERGROUP_ID", "")).strip()
        stdout = ""
        stderr = ""
        try:
            if not endpoint_url:
                raise RuntimeError("Vast pyworker serverless gpu_task requires vast_serverless_url or VAST_PYWORKER_ENDPOINT_URL")
            execution_plan = dict((job.metadata.get("provider_plan") or {}).get("execution_plan") or build_execution_plan(job, self.name))
            timeout = max(30, int(job.limits.get("max_runtime_minutes", 10)) * 60)
            request_payload = {
                "job": job.to_dict(),
                "command": execution_plan.get("command") or [],
                "workload": (job.metadata.get("input") or {}).get("workload") if isinstance(job.metadata.get("input"), dict) else {},
                "artifact_contract": execution_plan.get("artifact_contract") or [],
            }
            headers = {"Content-Type": "application/json", "User-Agent": "gpu-job-control"}
            api_key = os.getenv("VAST_API_KEY", "").strip()
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            req = urllib.request.Request(
                endpoint_url,
                data=json.dumps(request_payload).encode(),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                output = json.loads(response.read().decode())
            result = {
                "provider": self.name,
                "job_id": job.job_id,
                "job_type": job.job_type,
                "lane": "vast_pyworker_serverless",
                "ok": bool(output.get("ok")) if isinstance(output, dict) else False,
                "endpoint_id": endpoint_id,
                "workergroup_id": workergroup_id,
                "raw_output": output,
            }
            stdout = json.dumps(output, ensure_ascii=False, sort_keys=True) + "\n"
            if not result["ok"]:
                raise RuntimeError(f"Vast pyworker serverless returned non-ok output: {stdout.strip()}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            result = {
                "provider": self.name,
                "job_id": job.job_id,
                "job_type": job.job_type,
                "lane": "vast_pyworker_serverless",
                "ok": False,
                "endpoint_id": endpoint_id,
                "workergroup_id": workergroup_id,
                "error": f"http {exc.code}: {body}",
            }
            stderr = result["error"]
            job.error = stderr
            job.exit_code = 1
        except Exception as exc:
            result = {
                "provider": self.name,
                "job_id": job.job_id,
                "job_type": job.job_type,
                "lane": "vast_pyworker_serverless",
                "ok": False,
                "endpoint_id": endpoint_id,
                "workergroup_id": workergroup_id,
                "error": str(exc),
            }
            stderr = str(exc)
            job.error = stderr
            job.exit_code = 1
        metrics = {
            "provider": self.name,
            "job_id": job.job_id,
            "job_type": job.job_type,
            "runtime_seconds": max(0, now_unix() - start),
            "endpoint_id": endpoint_id,
            "workergroup_id": workergroup_id,
        }
        probe_info = {
            "provider": self.name,
            "worker_image": job.worker_image,
            "execution_mode": "vast_pyworker_serverless",
            "endpoint_id": endpoint_id,
            "workergroup_id": workergroup_id,
            "serverless_contract": self._serverless_contract_from_job(job),
        }
        (artifact_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        (artifact_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        (artifact_dir / "probe_info.json").write_text(json.dumps(probe_info, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        (artifact_dir / "stdout.log").write_text(stdout)
        (artifact_dir / "stderr.log").write_text(stderr)
        verify_data = {
            "ok": bool(result.get("ok")),
            "required": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
            "missing": [],
        }
        (artifact_dir / "verify.json").write_text(json.dumps(verify_data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        verify = verify_artifacts(artifact_dir)
        (artifact_dir / "verify.json").write_text(json.dumps(verify, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        job.finished_at = now_unix()
        job.runtime_seconds = max(0, job.finished_at - start)
        job.artifact_count = verify["artifact_count"]
        job.artifact_bytes = verify["artifact_bytes"]
        job.exit_code = 0 if not job.error and verify["ok"] and result.get("ok") else 1
        job.status = "succeeded" if job.exit_code == 0 else "failed"
        job.provider_job_id = endpoint_id
        store.save(job)
        return job

    def _wait_ssh_parts(self, instance_id: str, *, timeout_seconds: int) -> list[str]:
        binary = vast_bin()
        deadline = time.time() + timeout_seconds
        last = ""
        last_probe = ""
        while time.time() < deadline:
            proc = run([binary, "ssh-url", instance_id], capture_output=True, text=True, timeout=30)
            text = (proc.stdout or proc.stderr).strip()
            last = text.splitlines()[-1] if text else ""
            parts = _vast_ssh_parts(last)
            if parts:
                probe = run([*parts, "true"], capture_output=True, text=True, timeout=20)
                if probe.returncode == 0:
                    return parts
                last_probe = (probe.stderr or probe.stdout or f"ssh probe exit={probe.returncode}").strip()
            time.sleep(5)
        raise RuntimeError(f"Vast ssh not ready for {instance_id}: ssh_url={last!r} probe={last_probe!r}")

    def _asr_onstart_command(self) -> str:
        return "sleep infinity"

    def _serverless_contract_from_job(self, job: Job) -> dict[str, Any]:
        return {
            "endpoint_name": str(job.metadata.get("vast_endpoint_name") or f"gpu-job-{job.job_type}-{job.gpu_profile}"),
            "template_hash": str(job.metadata.get("vast_template_hash") or ""),
            "workergroup_id": str(job.metadata.get("vast_workergroup_id") or ""),
            "expected_image": job.worker_image,
            "expected_model": job.model,
            "cold_workers": int(job.metadata.get("vast_cold_workers") or 0),
            "test_workers": int(job.metadata.get("vast_test_workers") or 0),
            "max_workers": int(job.metadata.get("vast_max_workers") or 1),
        }

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

    def destroy_instance(self, instance_id: str, *, timeout_seconds: int = 90) -> dict[str, Any]:
        binary = vast_bin()
        if not binary:
            return {"ok": False, "instance_id": str(instance_id), "stdout": "", "stderr": "vastai binary not found", "exit_code": 127}
        proc = run(
            [binary, "destroy", "instance", str(instance_id), "-y", "--raw"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return {
            "ok": proc.returncode == 0,
            "instance_id": str(instance_id),
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "exit_code": proc.returncode,
        }

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


def _resolve_vast_local_input(input_uri: str) -> Path:
    parsed = urlparse(input_uri)
    if parsed.scheme == "file":
        path = Path(parsed.path)
    elif parsed.scheme == "local":
        path = Path(parsed.netloc + parsed.path)
    elif not parsed.scheme:
        path = Path(input_uri)
    else:
        raise ValueError(f"unsupported Vast ASR input_uri: {input_uri}")
    if not path.is_file():
        raise FileNotFoundError(f"Vast ASR input file not found: {path}")
    return path


def _parse_vast_instance_id(raw: str) -> str:
    raw = str(raw or "").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            data = ast.literal_eval(raw)
        except Exception:
            return ""
    if not isinstance(data, dict):
        return ""
    parsed_id = data.get("new_contract") or data.get("id")
    instance = data.get("instance")
    if not parsed_id and isinstance(instance, dict):
        parsed_id = instance.get("id")
    return str(parsed_id or "")


def _vast_ssh_parts(raw: str) -> list[str]:
    raw = str(raw or "").strip()
    if not raw:
        return []
    if raw.startswith("ssh://"):
        parsed = urlparse(raw)
        if not parsed.hostname:
            return []
        user = parsed.username or "root"
        parts = ["ssh"]
        if parsed.port:
            parts.extend(["-p", str(parsed.port)])
        parts.extend(["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", f"{user}@{parsed.hostname}"])
        return parts
    if raw.startswith("ssh "):
        parts = shlex.split(raw)
        parts.extend(["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"])
        return parts
    return []


def _scp_parts_from_ssh_parts(ssh_parts: list[str]) -> list[str]:
    parts = ["scp"]
    index = 1
    while index < len(ssh_parts) - 1:
        item = ssh_parts[index]
        if item == "-p" and index + 1 < len(ssh_parts) - 1:
            parts.extend(["-P", ssh_parts[index + 1]])
            index += 2
            continue
        if item == "-o" and index + 1 < len(ssh_parts) - 1:
            parts.extend(["-o", ssh_parts[index + 1]])
            index += 2
            continue
        index += 1
    return parts


def _run_checked(command: list[str], *, timeout: int) -> str:
    proc = run(command, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"command failed: {command[:3]}")
    return proc.stdout


def _secret_token_from_env(secret_ref: str) -> str:
    if secret_ref != "hf_token":
        return ""
    return str(os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or "")


def _vast_image_login_from_distribution(distribution: dict[str, Any], *, metadata: dict[str, Any] | None = None) -> str:
    metadata = metadata or {}
    override = str(metadata.get("vast_image_login") or "").strip()
    if override:
        return override
    registry_auth = dict(distribution.get("registry_auth") or {})
    if registry_auth.get("type") != "vast_image_login":
        return ""
    registry = str(registry_auth.get("registry") or "docker.io").strip()
    username_env = str(registry_auth.get("username_env") or "").strip()
    password_env = str(registry_auth.get("password_env") or "").strip()
    username = str(metadata.get("vast_registry_username") or os.environ.get(username_env) or "noiehoie").strip()
    password = str(metadata.get("vast_registry_password") or os.environ.get(password_env) or "").strip()
    if not password:
        raise RuntimeError(
            "Vast private image requires registry credentials before GPU allocation: "
            f"set {password_env or 'registry password env'} or job.metadata.vast_registry_password"
        )
    return f"-u {shlex.quote(username)} -p {shlex.quote(password)} {shlex.quote(registry)}"


def _vast_login_args(image_login: str) -> list[str]:
    return ["--login", image_login] if image_login else []


def _augment_provider_image_probe_artifacts(artifact_dir: Path, *, provider_image: str, image_contract_id: str) -> None:
    image_name, image_digest = _split_image_digest(provider_image)
    for filename in ("metrics.json", "probe_info.json", "result.json"):
        path = artifact_dir / filename
        payload: dict[str, Any] = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text())
                payload = data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                payload = {}
        payload["provider_image"] = provider_image
        payload["provider_image_name"] = image_name
        if image_digest:
            payload["provider_image_digest"] = image_digest
        if image_contract_id:
            payload["image_contract_id"] = image_contract_id
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _split_image_digest(image: str) -> tuple[str, str]:
    if "@sha256:" not in image:
        return image, ""
    name, digest = image.rsplit("@", 1)
    return name, digest


def _vast_remote_asr_script() -> str:
    return r"""from __future__ import annotations

from pathlib import Path
import argparse
import json
import os
import subprocess
import time


def run_command(command: str, *, timeout: int | None = None) -> dict:
    proc = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
    row = {"cmd": command, "returncode": proc.returncode, "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]}
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"command failed: {command}")
    return row


def render_srt(segments: list[dict]) -> str:
    def fmt(seconds: float) -> str:
        millis = int(round(max(0.0, seconds) * 1000))
        hours, rem = divmod(millis, 3600000)
        minutes, rem = divmod(rem, 60000)
        secs, ms = divmod(rem, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"
    blocks = []
    for index, segment in enumerate(segments, start=1):
        speaker = str(segment.get("speaker") or "").strip()
        label = f"{speaker}: " if speaker else ""
        start = fmt(float(segment.get("start") or 0))
        end = fmt(float(segment.get("end") or 0))
        text = str(segment.get("text") or "").strip()
        blocks.append(f"{index}\n{start} --> {end}\n{label}{text}")
    return "\n\n".join(blocks).strip() + ("\n" if blocks else "")


def assign_speakers(segments: list[dict], speaker_segments: list[dict]) -> list[dict]:
    out = []
    for segment in segments:
        row = dict(segment)
        overlaps: dict[str, float] = {}
        start = float(row.get("start") or 0)
        end = float(row.get("end") or 0)
        for speaker in speaker_segments:
            label = str(speaker.get("speaker") or "")
            overlap = max(0.0, min(end, float(speaker.get("end") or 0)) - max(start, float(speaker.get("start") or 0)))
            if label and overlap > 0:
                overlaps[label] = overlaps.get(label, 0.0) + overlap
        row["speaker"] = sorted(overlaps.items(), key=lambda item: (-item[1], item[0]))[0][0] if overlaps else ""
        out.append(row)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--input-uri", required=True)
    parser.add_argument("--provider", default="vast")
    parser.add_argument("--model-name", default="large-v3")
    parser.add_argument("--language", default="ja")
    parser.add_argument("--compute-type", default="int8_float16")
    parser.add_argument("--diarize", action="store_true")
    parser.add_argument("--speaker-model", default="pyannote/speaker-diarization-3.1")
    args = parser.parse_args()
    started = time.time()
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    logs = []
    error = ""
    result = {}
    try:
        if Path("hf_token").is_file():
            token = Path("hf_token").read_text(encoding="utf-8").strip()
            os.environ["HF_TOKEN"] = token
            os.environ["HUGGINGFACE_TOKEN"] = token
        logs.append(run_command("nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader"))
        from faster_whisper import WhisperModel

        model_name = {"whisper-large-v3": "large-v3", "openai/whisper-large-v3": "large-v3"}.get(args.model_name, args.model_name)
        model = WhisperModel(model_name, device="cuda", compute_type=args.compute_type)
        segments_iter, info = model.transcribe(args.input_uri, beam_size=5, vad_filter=True, language=args.language)
        segments = [
            {"id": i, "start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()}
            for i, s in enumerate(segments_iter)
        ]
        speaker_segments = []
        diarization_error = ""
        if args.diarize:
            try:
                from pyannote.audio import Pipeline
                try:
                    pipeline = Pipeline.from_pretrained(args.speaker_model, token=os.environ.get("HF_TOKEN"))
                except TypeError as exc:
                    if "token" not in str(exc):
                        raise
                    pipeline = Pipeline.from_pretrained(args.speaker_model, use_auth_token=os.environ.get("HF_TOKEN"))
                diarization = pipeline(args.input_uri)
                for turn, _, speaker in diarization.itertracks(yield_label=True):
                    speaker_segments.append(
                        {"start": round(float(turn.start), 3), "end": round(float(turn.end), 3), "speaker": str(speaker)}
                    )
                segments = assign_speakers(segments, speaker_segments)
            except Exception as exc:
                diarization_error = str(exc)
        text = "".join(segment["text"] for segment in segments).strip()
        gpu_probe = run_command("nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits")
        logs.append(gpu_probe)
        fields = [item.strip() for item in gpu_probe["stdout"].strip().split(",")]
        gpu_memory_used_mb = int(float(fields[1])) if len(fields) > 1 and fields[1] else 0
        gpu_utilization_percent = int(float(fields[3])) if len(fields) > 3 and fields[3] else 0
        result = {
            "job_id": args.job_id,
            "provider": args.provider,
            "model": model_name,
            "device": "cuda",
            "compute_type": args.compute_type,
            "language": getattr(info, "language", ""),
            "duration_seconds": round(float(getattr(info, "duration", 0.0)), 3),
            "text": text,
            "segments": segments,
            "diarization_requested": args.diarize,
            "diarization_enabled": args.diarize,
            "diarization_model": args.speaker_model if args.diarize else "",
            "diarization_error": diarization_error,
            "speaker_count": len({str(item.get("speaker")) for item in speaker_segments if item.get("speaker")}),
            "speaker_segments": speaker_segments,
            "segment_count": len(segments),
            "text_chars": len(text),
            "runtime_seconds": round(time.time() - started, 3),
            "gpu_probe": gpu_probe["stdout"].strip(),
        }
    except Exception as exc:
        error = str(exc)
        result = {
            "job_id": args.job_id,
            "provider": args.provider,
            "text": "",
            "segments": [],
            "error": error,
            "runtime_seconds": round(time.time() - started, 3),
        }
        gpu_memory_used_mb = 0
        gpu_utilization_percent = 0
    diarization_ok = (not result.get("diarization_requested")) or (
        bool(result.get("speaker_segments")) and not result.get("diarization_error")
    )
    verify = {
        "ok": bool(result.get("text")) and bool(result.get("segments")) and diarization_ok and not bool(error or result.get("error")),
        "required": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
        "missing": [],
        "checks": {
            "text_nonempty": bool(result.get("text")),
            "segments_nonempty": bool(result.get("segments")),
            "diarization_ok": diarization_ok,
            "no_error": not bool(error or result.get("error")),
        },
    }
    metrics = {
        "job_id": args.job_id,
        "provider": args.provider,
        "model": result.get("model") or args.model_name,
        "runtime_seconds": round(time.time() - started, 3),
        "remote_runtime_seconds": result.get("runtime_seconds"),
        "text_chars": len(result.get("text") or ""),
        "segment_count": len(result.get("segments") or []),
        "speaker_count": result.get("speaker_count", 0),
        "diarization_requested": bool(result.get("diarization_requested")),
        "diarization_ok": diarization_ok,
        "gpu_memory_used_mb": gpu_memory_used_mb,
        "gpu_utilization_percent": gpu_utilization_percent,
    }
    (artifact_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (artifact_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (artifact_dir / "verify.json").write_text(json.dumps(verify, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (artifact_dir / "transcript.srt").write_text(render_srt(list(result.get("segments") or [])), encoding="utf-8")
    (artifact_dir / "speaker_timeline.json").write_text(
        json.dumps(result.get("speaker_segments") or [], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (artifact_dir / "stdout.log").write_text(json.dumps(logs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (artifact_dir / "stderr.log").write_text(error + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": verify["ok"],
                "text_chars": metrics["text_chars"],
                "segments": metrics["segment_count"],
                "runtime_seconds": metrics["runtime_seconds"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if verify["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
"""
