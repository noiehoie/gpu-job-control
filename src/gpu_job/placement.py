from __future__ import annotations

from typing import Any

from .models import Job


PLACEMENT_VERSION = "gpu-job-placement-v1"


def placement_check(job: Job, provider: str = "") -> dict[str, Any]:
    provider = provider or str(job.metadata.get("selected_provider") or job.provider or "")
    resources = job.metadata.get("resources")
    resources = resources if isinstance(resources, dict) else {}
    gpu_count = float(resources.get("gpu_count") or 1)
    fractional_gpu = resources.get("fractional_gpu")
    exclusive = bool(resources.get("exclusive_gpu", False))
    multi_gpu = gpu_count > 1
    fractional = fractional_gpu is not None and float(fractional_gpu) < 1
    provider_support = {
        "local": {"multi_gpu": False, "fractional": False},
        "ollama": {"multi_gpu": False, "fractional": False},
        "modal": {"multi_gpu": False, "fractional": False},
        "runpod": {"multi_gpu": True, "fractional": False},
        "vast": {"multi_gpu": True, "fractional": False},
    }.get(provider, {"multi_gpu": False, "fractional": False})
    multi_ok = (not multi_gpu) or bool(provider_support["multi_gpu"])
    fractional_ok = (not fractional) or (bool(provider_support["fractional"]) and not exclusive)
    return {
        "ok": multi_ok and fractional_ok,
        "placement_version": PLACEMENT_VERSION,
        "provider": provider,
        "gpu_count": gpu_count,
        "fractional_gpu": fractional_gpu,
        "exclusive_gpu": exclusive,
        "multi_gpu_ok": multi_ok,
        "fractional_ok": fractional_ok,
        "provider_support": provider_support,
    }
