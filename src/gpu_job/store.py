from __future__ import annotations

from pathlib import Path
import json
import os
import fcntl

from .models import Job, app_data_dir, now_unix


class JobStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or app_data_dir()
        self.jobs_dir = self.root / "jobs"
        self.artifacts_dir = self.root / "artifacts"
        self.logs_dir = self.root / "logs"
        self.locks_dir = self.root / "locks"

    def ensure(self) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)

    def job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def artifact_dir(self, job_id: str) -> Path:
        return self.artifacts_dir / job_id

    def save(self, job: Job) -> Path:
        self.ensure()
        job.updated_at = now_unix()
        path = self.job_path(job.job_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
        return path

    def load(self, job_id: str) -> Job:
        return Job.from_file(self.job_path(job_id))

    def list_jobs(self, status: str | None = None, limit: int = 100) -> list[Job]:
        self.ensure()
        jobs = []
        for path in sorted(self.jobs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                job = Job.from_file(path)
            except Exception:
                continue
            if status and job.status != status:
                continue
            jobs.append(job)
            if len(jobs) >= limit:
                break
        return jobs

    def next_queued(self) -> Job | None:
        queued = self.list_jobs(status="queued", limit=1000)
        if not queued:
            return None
        queued.sort(key=lambda job: (job.created_at, job.job_id))
        return queued[0]

    def lock_path(self, name: str = "worker") -> Path:
        self.ensure()
        return self.locks_dir / f"{name}.lock"


class StoreLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None

    def __enter__(self) -> "StoreLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fh:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
