from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib import request
import base64
import hashlib
import json
import time

from gpu_job.models import Job, now_unix
from gpu_job.providers.base import Provider
from gpu_job.resource import ollama_resource_ok
from gpu_job.store import JobStore
from gpu_job.verify import verify_artifacts


OLLAMA_URL = "http://127.0.0.1:11434"


class OllamaProvider(Provider):
    name = "ollama"

    def doctor(self) -> dict[str, Any]:
        try:
            data = _ollama_json("GET", "/api/tags", timeout=10)
        except Exception as exc:
            return {"provider": self.name, "ok": False, "reason": str(exc), "models": []}
        models = sorted(item.get("name", "") for item in data.get("models", []) if item.get("name"))
        return {
            "provider": self.name,
            "ok": bool(models),
            "url": OLLAMA_URL,
            "models": models,
            "has_text_model": "qwen2.5:72b" in models or "qwen2.5:32b" in models,
            "has_vision_model": "gemma3:4b" in models,
            "has_embedding_model": "bge-m3:latest" in models or "bge-m3" in models,
        }

    def signal(self, profile: dict[str, Any]) -> dict[str, Any]:
        health = self.doctor()
        resources = ollama_resource_ok()
        available = bool(health.get("ok")) and bool(resources.get("ok"))
        return {
            "provider": self.name,
            "healthy": bool(health.get("ok")),
            "available": available,
            "reason": "local Ollama available"
            if available
            else f"Ollama unavailable: {resources.get('reason') if health.get('ok') else 'health check failed'}",
            "health": health,
            "resource": resources,
            "active_jobs": 0,
            "capacity_hint": "resident Ollama; no marginal GPU cloud spend",
            "estimated_startup_seconds": 1,
            "offer_count": 1 if available else 0,
            "cheapest_offer": {"name": "local-ollama", "dph_total": 0.0},
            "estimated_max_runtime_cost_usd": 0.0,
        }

    def cost_guard(self) -> dict[str, Any]:
        resources = ollama_resource_ok()
        return {
            "provider": self.name,
            "ok": bool(resources.get("ok")),
            "billable_resources": [],
            "estimated_hourly_usd": 0.0,
            "reason": "local Ollama is fixed capacity; resource guard ok"
            if resources.get("ok")
            else f"local Ollama resource guard failed: {resources.get('reason')}",
            "resource": resources,
        }

    def plan(self, job: Job) -> dict[str, Any]:
        return {
            "provider": self.name,
            "job_id": job.job_id,
            "mode": "local ollama execution",
            "job_type": job.job_type,
            "model": _select_model(job),
            "artifact_contract": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
            "notes": [
                "Uses resident Ollama capacity for zero marginal cloud GPU spend.",
                "embedding uses bge-m3 when available.",
                "llm_heavy uses qwen2.5:72b when available.",
                "vlm_ocr uses gemma3:4b vision when available.",
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
        if job.job_type not in {"embedding", "llm_heavy", "vlm_ocr"}:
            job.status = "failed"
            job.error = f"ollama execute does not support job_type: {job.job_type}"
            job.exit_code = 2
            store.save(job)
            return job

        artifact_dir = store.artifact_dir(job.job_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        start = now_unix()
        job.started_at = start
        job.status = "running"
        store.save(job)
        stdout = ""
        stderr = ""
        try:
            model = _select_model(job)
            timeout = max(30, int(job.limits.get("max_runtime_minutes", 10)) * 60)
            remote_start = time.monotonic()
            if job.job_type == "embedding":
                texts = _embedding_input_texts(job)
                response = _ollama_embedding_json(model=model, texts=texts, timeout=timeout)
                vectors = _embedding_vectors(response)
                remote_runtime = time.monotonic() - remote_start
                result = {
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "provider": self.name,
                    "model": model,
                    "dimensions": len(vectors[0]) if vectors else 0,
                    "count": len(vectors),
                    "items": [
                        {"index": index, "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "vector": vector}
                        for index, (text, vector) in enumerate(zip(texts, vectors, strict=True))
                    ],
                }
                stdout = f"ollama embed completed model={model} count={len(vectors)}\n"
            else:
                prompt = _prompt(job)
                payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
                max_tokens = _max_tokens(job)
                if max_tokens:
                    payload["options"] = {"num_predict": max_tokens}
                if job.job_type == "vlm_ocr":
                    payload["images"] = [_image_base64(job)]
                response = _ollama_json("POST", "/api/generate", payload=payload, timeout=timeout)
                remote_runtime = time.monotonic() - remote_start
                text = str(response.get("response") or "")
                result = {
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "provider": self.name,
                    "model": model,
                    "text": text,
                    "done": response.get("done", False),
                }
                stdout = f"ollama generate completed model={model} chars={len(text)}\n"
            metrics = {
                "job_id": job.job_id,
                "job_type": job.job_type,
                "provider": self.name,
                "model": model,
                "runtime_seconds": round(remote_runtime, 3),
                "gpu_profile": job.gpu_profile,
                "worker_image": job.worker_image,
            }
            (artifact_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            (artifact_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        except Exception as exc:
            stderr = str(exc)
            job.error = stderr
            job.exit_code = 1
        (artifact_dir / "stdout.log").write_text(stdout)
        (artifact_dir / "stderr.log").write_text(stderr)
        if not (artifact_dir / "result.json").exists():
            (artifact_dir / "result.json").write_text(json.dumps({"text": "", "error": stderr}, ensure_ascii=False, indent=2) + "\n")
        if not (artifact_dir / "metrics.json").exists():
            (artifact_dir / "metrics.json").write_text(json.dumps({"job_id": job.job_id, "provider": self.name}, indent=2) + "\n")
        (artifact_dir / "verify.json").write_text("{}\n")
        verify = verify_artifacts(artifact_dir)
        (artifact_dir / "verify.json").write_text(json.dumps(verify, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        job.finished_at = now_unix()
        job.runtime_seconds = max(0, job.finished_at - start)
        job.artifact_count = verify["artifact_count"]
        job.artifact_bytes = verify["artifact_bytes"]
        if not job.error and verify["ok"]:
            job.status = "succeeded"
            job.exit_code = 0
        else:
            job.status = "failed"
            if job.exit_code is None:
                job.exit_code = 1
        store.save(job)
        return job


def _ollama_json(method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    req = request.Request(f"{OLLAMA_URL}{path}", data=data, method=method, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _select_model(job: Job) -> str:
    requested = (job.model or "").lower()
    if job.job_type == "embedding":
        return job.model or "bge-m3"
    if job.job_type == "vlm_ocr":
        return "gemma3:4b"
    if "32b" in requested:
        return "qwen2.5:32b"
    return "qwen2.5:72b"


def _prompt(job: Job) -> str:
    payload = job.metadata.get("input")
    if isinstance(payload, dict):
        prompt = str(payload.get("prompt") or "")
        system_prompt = str(payload.get("system_prompt") or "")
        if system_prompt:
            return f"{system_prompt}\n\n{prompt}"
        if prompt:
            return prompt
    if job.input_uri.startswith("text://"):
        return job.input_uri.removeprefix("text://")
    return job.input_uri


def _max_tokens(job: Job) -> int:
    payload = job.metadata.get("input")
    if isinstance(payload, dict):
        try:
            value = int(payload.get("max_tokens") or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, min(value, 8192))
    return 0


def _image_base64(job: Job) -> str:
    payload = job.metadata.get("input")
    if isinstance(payload, dict) and payload.get("image_base64"):
        return str(payload["image_base64"])
    image_path = _image_path(job)
    return base64.b64encode(image_path.read_bytes()).decode()


def _image_path(job: Job) -> Path:
    if job.input_uri.startswith("file://"):
        return Path(job.input_uri.removeprefix("file://"))
    return Path(job.input_uri)


def _embedding_input_texts(job: Job) -> list[str]:
    payload = job.metadata.get("input")
    if isinstance(payload, dict):
        texts = payload.get("texts")
        if isinstance(texts, list):
            return [str(item) for item in texts]
        text = payload.get("text")
        if text is not None:
            return [str(text)]
        prompt = payload.get("prompt")
        if prompt is not None:
            return [str(prompt)]
    if job.input_uri.startswith("text://"):
        return [job.input_uri.removeprefix("text://")]
    return [job.input_uri]


def _ollama_embedding_json(model: str, texts: list[str], timeout: int) -> dict[str, Any]:
    try:
        return _ollama_json("POST", "/api/embed", payload={"model": model, "input": texts}, timeout=timeout)
    except Exception:
        if len(texts) != 1:
            raise
        return _ollama_json("POST", "/api/embeddings", payload={"model": model, "prompt": texts[0]}, timeout=timeout)


def _embedding_vectors(response: dict[str, Any]) -> list[list[float]]:
    embeddings = response.get("embeddings")
    if isinstance(embeddings, list):
        return [[float(value) for value in vector] for vector in embeddings if isinstance(vector, list)]
    embedding = response.get("embedding")
    if isinstance(embedding, list):
        return [[float(value) for value in embedding]]
    raise ValueError("Ollama embedding response did not contain embeddings")
