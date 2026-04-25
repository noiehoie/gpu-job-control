from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import tempfile

from .models import Job
from .queue import enqueue_job, work_once
from .readiness import launch_readiness
from .router import route_job
from .runner import submit_job
from .store import JobStore
from .verify import verify_artifacts
from .wal import append_wal
from .providers import get_provider


SELFTEST_VERSION = "gpu-job-selftest-v1"


def run_selftest() -> dict[str, Any]:
    old_xdg = os.environ.get("XDG_DATA_HOME")
    with tempfile.TemporaryDirectory(prefix="gpu-job-selftest-") as tmp:
        tmp_path = Path(tmp)
        os.environ["XDG_DATA_HOME"] = str(tmp_path / "data")
        try:
            checks = []
            routing_config = tmp_path / "gpu-profiles.json"
            routing_config.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "llm_heavy": {
                                "preferred_provider": "local",
                                "fallback_providers": [],
                                "max_runtime_minutes": 1,
                                "max_cost_usd": 0,
                            }
                        }
                    }
                )
            )
            smoke = Job.from_dict(
                {
                    "job_type": "smoke",
                    "input_uri": "text://selftest",
                    "output_uri": "/tmp/gpu-job-selftest",
                    "worker_image": "local/canary:latest",
                    "gpu_profile": "llm_heavy",
                }
            )
            route = route_job(smoke, config_path=routing_config)
            selected_decision = route["provider_decisions"][route["selected_provider"]]
            score_components = selected_decision["workload_policy"].get("score_components", [])
            checks.append({"name": "route_score_components", "ok": bool(score_components)})

            local_smoke = Job.from_dict(
                {
                    "job_type": "smoke",
                    "input_uri": "text://selftest-local",
                    "output_uri": "/tmp/gpu-job-selftest",
                    "worker_image": "local/canary:latest",
                    "gpu_profile": "llm_heavy",
                }
            )
            store = JobStore()
            saved = get_provider("local").submit(local_smoke, store=store, execute=True)
            artifact_dir = store.artifact_dir(saved.job_id)
            verify = verify_artifacts(artifact_dir)
            checks.append({"name": "local_submit", "ok": saved.status == "succeeded"})
            checks.append({"name": "artifact_manifest", "ok": bool(verify.get("ok") and verify.get("manifest", {}).get("ok"))})

            secret_fail = Job.from_dict(
                {
                    "job_type": "smoke",
                    "input_uri": "text://secret",
                    "output_uri": "/tmp/gpu-job-selftest",
                    "worker_image": "local/canary:latest",
                    "gpu_profile": "llm_heavy",
                    "metadata": {"secret_refs": ["forbidden-secret"]},
                }
            )
            blocked = submit_job(secret_fail, provider_name="local", execute=False)
            checks.append({"name": "secret_gate_blocks", "ok": not blocked.get("ok") and blocked.get("error") == "secret gate failed"})

            wal_job = Job.from_dict(
                {
                    "job_type": "smoke",
                    "input_uri": "text://wal",
                    "output_uri": "/tmp/gpu-job-selftest",
                    "worker_image": "local/canary:latest",
                    "gpu_profile": "llm_heavy",
                    "job_id": "selftest-wal-ambiguous",
                }
            )
            append_wal(
                wal_job, transition="created->provider_submit", intent="provider_submit", extra={"provider": "local", "execute": True}
            )
            enqueue_job(smoke, provider_name="auto")
            worker = work_once()
            checks.append({"name": "wal_fail_closed", "ok": worker.get("ok") is False and worker.get("worked") is False})

            readiness = launch_readiness(limit=20, guard_provider_names=["local"], include_provider_stability=False)
            checks.append(
                {
                    "name": "readiness_detects_wal",
                    "ok": readiness.get("ok") is False and readiness.get("checks", {}).get("wal_recovery") is False,
                }
            )
            return {"ok": all(item["ok"] for item in checks), "selftest_version": SELFTEST_VERSION, "checks": checks}
        finally:
            if old_xdg is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = old_xdg
