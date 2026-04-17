from __future__ import annotations

from gpu_job.providers.base import Provider
from gpu_job.providers.local import LocalProvider
from gpu_job.providers.modal import ModalProvider
from gpu_job.providers.ollama import OllamaProvider
from gpu_job.providers.runpod import RunPodProvider
from gpu_job.providers.vast import VastProvider


PROVIDERS: dict[str, Provider] = {
    "local": LocalProvider(),
    "modal": ModalProvider(),
    "ollama": OllamaProvider(),
    "runpod": RunPodProvider(),
    "vast": VastProvider(),
}


def get_provider(name: str) -> Provider:
    try:
        return PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(f"unknown provider: {name}") from exc
