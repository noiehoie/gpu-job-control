from __future__ import annotations

from typing import Any
import hashlib
import json

from gpu_job.models import Job, now_unix
from gpu_job.providers.base import Provider
from gpu_job.store import JobStore
from gpu_job.verify import application_verify_payload, verify_artifacts
from gpu_job.workflow_helpers import helper_metrics, run_cpu_workflow_helper


class LocalProvider(Provider):
    name = "local"

    def doctor(self) -> dict[str, Any]:
        return {"provider": self.name, "ok": True, "mode": "local canary"}

    def signal(self, profile: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": self.name,
            "healthy": True,
            "available": True,
            "reason": "local canary always available",
            "health": self.doctor(),
            "active_jobs": 0,
            "capacity_hint": "local deterministic canary only",
            "estimated_startup_seconds": 0,
            "offer_count": 1,
            "cheapest_offer": {
                "name": "local-canary",
                "dph_total": 0.0,
                "gpu_ram_mb": 0,
                "compute_cap": 0,
            },
            "estimated_max_runtime_cost_usd": 0.0,
        }

    def plan(self, job: Job) -> dict[str, Any]:
        return {
            "provider": self.name,
            "action": "create deterministic local canary artifacts",
            "job_id": job.job_id,
            "artifact_dir": str(JobStore().artifact_dir(job.job_id)),
        }

    def submit(self, job: Job, store: JobStore, execute: bool = False) -> Job:
        job.provider = self.name
        if not execute:
            job.status = "planned"
            store.save(job)
            return job
        start = now_unix()
        job.status = "running"
        job.started_at = start
        store.save(job)
        artifact_dir = store.artifact_dir(job.job_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        stdout = "local provider canary executed\n"
        stderr = ""
        result: dict[str, Any] = {
            "job_id": job.job_id,
            "job_type": job.job_type,
            "provider": self.name,
            "input_uri": job.input_uri,
            "output_uri": job.output_uri,
            "message": "deterministic local canary result",
        }
        if job.job_type == "embedding":
            texts = _embedding_input_texts(job)
            vectors = [_deterministic_vector(text) for text in texts]
            result.update(
                {
                    "model": job.model or "local-deterministic-embedding",
                    "dimensions": len(vectors[0]) if vectors else 0,
                    "count": len(vectors),
                    "items": [
                        {"index": index, "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "vector": vector}
                        for index, (text, vector) in enumerate(zip(texts, vectors, strict=True))
                    ],
                }
            )
        elif job.job_type == "llm_heavy":
            prompt = _llm_prompt(job)
            result.update(
                {
                    "model": job.model or "local-deterministic-llm",
                    "answer": _deterministic_answer(prompt),
                    "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                    "citations": [],
                }
            )
        elif job.job_type == "pdf_ocr":
            pages = _ocr_pages(job)
            result.update(
                {
                    "model": job.model or "local-deterministic-pdf-ocr",
                    "page_count": len(pages),
                    "text": "\n".join(page["text"] for page in pages),
                    "pages": pages,
                }
            )
        elif job.job_type == "vlm_ocr":
            frames = _vlm_frames(job)
            result.update(
                {
                    "model": job.model or "local-deterministic-vlm-ocr",
                    "frame_count": len(frames),
                    "text": "\n".join(frame["text"] for frame in frames),
                    "frames": frames,
                }
            )
        elif job.job_type == "cpu_workflow_helper":
            result = {
                **result,
                **run_cpu_workflow_helper(job, artifact_dir),
                "model": job.model or "local-cpu-workflow-helper",
            }
        metrics: dict[str, Any] = {
            "job_id": job.job_id,
            "runtime_seconds": 0,
            "model": job.model,
            "gpu_profile": job.gpu_profile,
            "worker_image": job.worker_image,
            "job_type": job.job_type,
        }
        if job.job_type == "cpu_workflow_helper":
            metrics.update(helper_metrics(job, result, runtime_seconds=0))
        (artifact_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        (artifact_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
        (artifact_dir / "stdout.log").write_text(stdout)
        (artifact_dir / "stderr.log").write_text(stderr)
        app_verify = application_verify_payload(job.job_type, result)
        (artifact_dir / "verify.json").write_text(json.dumps(app_verify, indent=2, sort_keys=True) + "\n")
        verify = verify_artifacts(artifact_dir)
        (artifact_dir / "verify.json").write_text(json.dumps(verify, indent=2, sort_keys=True) + "\n")
        count, bytes_total = verify["artifact_count"], verify["artifact_bytes"]
        job.status = "succeeded" if verify["ok"] else "failed"
        job.finished_at = now_unix()
        job.runtime_seconds = max(0, job.finished_at - start)
        job.exit_code = 0 if verify["ok"] else 1
        job.artifact_count = count
        job.artifact_bytes = bytes_total
        store.save(job)
        return job


def _embedding_input_texts(job: Job) -> list[str]:
    payload = job.metadata.get("input")
    if isinstance(payload, dict):
        texts = payload.get("texts")
        if isinstance(texts, list):
            return [str(item) for item in texts]
    if job.input_uri.startswith("text://"):
        return [job.input_uri.removeprefix("text://")]
    return [job.input_uri]


def _deterministic_vector(text: str, dimensions: int = 8) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [round(int.from_bytes(digest[i * 2 : i * 2 + 2], "big") / 65535, 6) for i in range(dimensions)]


def _llm_prompt(job: Job) -> str:
    payload = job.metadata.get("input")
    if isinstance(payload, dict):
        prompt = payload.get("prompt")
        if prompt:
            return str(prompt)
    if job.input_uri.startswith("text://"):
        return job.input_uri.removeprefix("text://")
    return f"job_type={job.job_type} input_uri={job.input_uri}"


def _deterministic_answer(prompt: str) -> str:
    words = prompt.strip().split()
    preview = " ".join(words[:32]) if words else prompt[:120]
    return f"local deterministic llm canary response: {preview}"


def _ocr_pages(job: Job) -> list[dict[str, Any]]:
    payload = job.metadata.get("input")
    raw_pages = []
    if isinstance(payload, dict) and isinstance(payload.get("pages"), list):
        raw_pages = payload["pages"]
    elif job.input_uri.startswith("text://"):
        raw_pages = [job.input_uri.removeprefix("text://")]
    else:
        raw_pages = [job.input_uri]
    return [
        {
            "page": index + 1,
            "text": str(text),
            "text_sha256": hashlib.sha256(str(text).encode()).hexdigest(),
            "confidence": 1.0,
        }
        for index, text in enumerate(raw_pages)
    ]


def _vlm_frames(job: Job) -> list[dict[str, Any]]:
    payload = job.metadata.get("input")
    frames: list[Any] = []
    if isinstance(payload, dict) and isinstance(payload.get("frames"), list):
        frames = payload["frames"]
    elif job.input_uri.startswith("text://"):
        frames = [{"frame_id": "frame-0001", "description": job.input_uri.removeprefix("text://")}]
    else:
        frames = [{"frame_id": "frame-0001", "description": job.input_uri}]
    out = []
    for index, frame in enumerate(frames):
        if isinstance(frame, dict):
            frame_id = str(frame.get("frame_id") or f"frame-{index + 1:04d}")
            text = str(frame.get("description") or frame.get("text") or "")
        else:
            frame_id = f"frame-{index + 1:04d}"
            text = str(frame)
        out.append(
            {
                "frame_id": frame_id,
                "text": text,
                "objects": [],
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "confidence": 1.0,
            }
        )
    return out
