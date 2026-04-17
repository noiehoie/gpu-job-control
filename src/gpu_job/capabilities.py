from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .config import config_path
from .models import Job
from .router import _estimated_input_tokens


CAPABILITY_VERSION = "gpu-job-capability-v1"


def default_capability_path() -> Path:
    return config_path("GPU_JOB_CAPABILITIES_CONFIG", "model-capabilities.json")


def load_capabilities(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or default_capability_path()).read_text())


def model_key(provider: str, model: str) -> str:
    if model:
        return f"{provider}:{model}"
    return f"{provider}:default"


def evaluate_model_capability(job: Job, provider: str = "") -> dict[str, Any]:
    provider = provider or str(job.metadata.get("selected_provider") or job.provider or "")
    registry = load_capabilities()
    models = dict(registry.get("models", {}))
    key = model_key(provider, job.model)
    capability = models.get(key) or models.get(f"{provider}:default")
    requirements = job.metadata.get("model_requirements")
    requirements = requirements if isinstance(requirements, dict) else {}
    require_registered = bool(requirements.get("require_registered_model"))
    if not capability:
        return {
            "ok": not require_registered,
            "capability_version": CAPABILITY_VERSION,
            "provider": provider,
            "model": job.model,
            "model_key": key,
            "registered": False,
            "reason": "model not registered",
        }
    job_type_ok = job.job_type in set(capability.get("job_types") or [])
    tokens = _estimated_input_tokens(job)
    max_tokens = int(capability.get("max_input_tokens") or 0)
    tokens_ok = not max_tokens or tokens <= max_tokens
    vision_ok = True
    if requirements.get("vision") is not None:
        vision_ok = bool(capability.get("vision")) == bool(requirements.get("vision"))
    asr_ok = True
    if requirements.get("asr") is not None:
        asr_ok = bool(capability.get("asr")) == bool(requirements.get("asr"))
    quality_ok = _quality_ok(str(capability.get("quality_tier") or ""), str(requirements.get("min_quality_tier") or ""), registry)
    ok = job_type_ok and tokens_ok and vision_ok and asr_ok and quality_ok
    return {
        "ok": ok,
        "capability_version": CAPABILITY_VERSION,
        "provider": provider,
        "model": job.model,
        "model_key": key,
        "registered": True,
        "capability": capability,
        "checks": {
            "job_type_ok": job_type_ok,
            "tokens_ok": tokens_ok,
            "vision_ok": vision_ok,
            "asr_ok": asr_ok,
            "quality_ok": quality_ok,
        },
        "estimated_input_tokens": tokens,
        "reason": "model capability accepted" if ok else "model capability requirement failed",
    }


def _quality_ok(actual: str, required: str, registry: dict[str, Any]) -> bool:
    if not required:
        return True
    order = [str(item) for item in registry.get("quality_order", [])]
    if actual not in order or required not in order:
        return actual == required
    return order.index(actual) >= order.index(required)
