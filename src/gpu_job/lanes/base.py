from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import Job


@dataclass(frozen=True)
class Lane:
    lane_id: str
    provider: str
    resource_kind: str
    description: str

    def validate_request(self, job: Job) -> dict[str, Any]:
        from ..provider_module_contracts import provider_module_validation

        return provider_module_validation({"provider_module_id": self.lane_id, **dict(job.metadata)}, self.provider)

    def plan(self, job: Job) -> dict[str, Any]:
        from ..providers import get_provider

        return get_provider(self.provider).plan(job)

    def execute(self, job: Job, *, execute: bool) -> dict[str, Any]:
        from ..runner import submit_job

        return submit_job(job, provider_name=self.provider, execute=execute)

    def poll(self, job_id: str) -> dict[str, Any]:
        from ..store import JobStore

        return {"ok": True, "job": JobStore().load(job_id).to_dict(), "lane_id": self.lane_id}

    def cancel(self, job_id: str, *, force: bool = False, reason: str = "") -> dict[str, Any]:
        from ..queue import cancel_job

        return cancel_job(job_id, force=force, reason=reason)

    def collect_artifacts(self, job_id: str) -> dict[str, Any]:
        from ..store import JobStore
        from ..verify import verify_artifacts

        return verify_artifacts(JobStore().artifact_dir(job_id))
