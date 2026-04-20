from __future__ import annotations

from pathlib import Path
import json

from gpu_job.execution_record import build_execution_record
from gpu_job.models import Job
from gpu_job.providers import get_provider


def test_provider_plans_remain_provider_native_and_include_execution_plan(tmp_path: Path) -> None:
    job_file = tmp_path / "job.json"
    job_file.write_text(
        json.dumps(
            {
                "job_id": "test-job-123",
                "job_type": "smoke",
                "gpu_profile": "smoke",
                "input_uri": "local://nothing",
                "output_uri": f"file://{tmp_path}/out",
                "worker_image": "nvidia/cuda:12.4.1-base-ubuntu22.04",
            }
        )
    )
    job = Job.from_file(job_file)

    for name in ["modal", "runpod", "vast"]:
        provider = get_provider(name)
        plan = provider.plan(job)

        assert plan["provider"] == name
        assert "execution_plan" in plan
        assert plan["execution_plan"]["execution_plan_version"] == "gpu-job-execution-plan-v1"


def test_plan_quote_is_execution_record_contract_not_provider_native_plan() -> None:
    job = Job(
        job_id="standardized-plan-exec-record",
        job_type="smoke",
        input_uri="local://nothing",
        output_uri="local://out",
        worker_image="nvidia/cuda:12.4.1-base-ubuntu22.04",
        gpu_profile="smoke",
        metadata={
            "selected_provider": "modal",
            "workspace_plan": {
                "workspace_plan_id": "workspace-standardized",
                "provider": "modal",
                "decision": "ready",
                "required_actions": [],
            },
        },
    )

    record = build_execution_record(job)

    assert record["plan_quote"]["quote_version"] == "gpu-job-plan-quote-v1"
    assert record["plan_quote"]["selected_option"]["provider"] == "modal"
    assert record["plan_quote"]["selected_option"]["workspace_plan_id"] == "workspace-standardized"
