from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json
import os
import time
import uuid


VALID_JOB_TYPES = {
    "asr",
    "pdf_ocr",
    "vlm_ocr",
    "avatar_video",
    "image_generation",
    "llm_heavy",
    "embedding",
    "smoke",
    "cpu_workflow_helper",
}

VALID_STATUSES = {
    "created",
    "buffered",
    "planned",
    "queued",
    "starting",
    "running",
    "succeeded",
    "failed",
    "cancelled",
}


def now_unix() -> int:
    return int(time.time())


def make_job_id(job_type: str) -> str:
    return f"{job_type}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


def xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def app_data_dir() -> Path:
    return xdg_data_home() / "gpu-job-control"


@dataclass
class Job:
    job_type: str
    input_uri: str
    output_uri: str
    worker_image: str
    gpu_profile: str
    model: str = ""
    job_id: str = ""
    provider: str = ""
    provider_job_id: str = ""
    status: str = "created"
    limits: dict[str, Any] = field(default_factory=dict)
    verify: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: int = field(default_factory=now_unix)
    updated_at: int = field(default_factory=now_unix)
    started_at: int | None = None
    finished_at: int | None = None
    exit_code: int | None = None
    runtime_seconds: int | None = None
    artifact_count: int | None = None
    artifact_bytes: int | None = None
    error: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Job":
        missing = [key for key in ["job_type", "input_uri", "output_uri", "worker_image", "gpu_profile"] if not data.get(key)]
        if missing:
            raise ValueError(f"missing required field(s): {', '.join(missing)}")
        job_type = str(data["job_type"])
        if job_type not in VALID_JOB_TYPES:
            raise ValueError(f"invalid job_type: {job_type}")
        status = str(data.get("status", "created"))
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        job_id = str(data.get("job_id") or make_job_id(job_type))
        return cls(
            job_type=job_type,
            input_uri=str(data["input_uri"]),
            output_uri=str(data["output_uri"]),
            worker_image=str(data["worker_image"]),
            gpu_profile=str(data["gpu_profile"]),
            model=str(data.get("model", "")),
            job_id=job_id,
            provider=str(data.get("provider", "")),
            provider_job_id=str(data.get("provider_job_id", "")),
            status=status,
            limits=dict(data.get("limits", {})),
            verify=dict(data.get("verify", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=int(data.get("created_at", now_unix())),
            updated_at=int(data.get("updated_at", now_unix())),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            exit_code=data.get("exit_code"),
            runtime_seconds=data.get("runtime_seconds"),
            artifact_count=data.get("artifact_count"),
            artifact_bytes=data.get("artifact_bytes"),
            error=str(data.get("error", "")),
        )

    @classmethod
    def from_file(cls, path: Path) -> "Job":
        return cls.from_dict(json.loads(path.read_text()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "input_uri": self.input_uri,
            "output_uri": self.output_uri,
            "worker_image": self.worker_image,
            "gpu_profile": self.gpu_profile,
            "model": self.model,
            "provider": self.provider,
            "provider_job_id": self.provider_job_id,
            "status": self.status,
            "limits": self.limits,
            "verify": self.verify,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "runtime_seconds": self.runtime_seconds,
            "artifact_count": self.artifact_count,
            "artifact_bytes": self.artifact_bytes,
            "error": self.error,
        }
