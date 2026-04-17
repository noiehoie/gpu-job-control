from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from gpu_job.models import Job
from gpu_job.store import JobStore


class Provider(ABC):
    name: str

    @abstractmethod
    def doctor(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def plan(self, job: Job) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def submit(self, job: Job, store: JobStore, execute: bool = False) -> Job:
        raise NotImplementedError

    def signal(self, profile: dict[str, Any]) -> dict[str, Any]:
        health = self.doctor()
        available = bool(health.get("ok"))
        return {
            "provider": self.name,
            "healthy": available,
            "available": available,
            "reason": "healthy" if available else "provider health check failed",
            "health": health,
            "active_jobs": None,
            "capacity_hint": "unknown",
            "estimated_startup_seconds": None,
            "offer_count": None,
            "cheapest_offer": None,
            "estimated_max_runtime_cost_usd": None,
        }

    def cost_guard(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "ok": True,
            "billable_resources": [],
            "estimated_hourly_usd": 0.0,
            "reason": "no billable resources known for provider",
        }
