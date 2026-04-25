from __future__ import annotations

from gpu_job.lanes import LANES, resolve_lane_id
from gpu_job.public_ops import plan_public_job, route_public_job, schema_snapshot, validate_public_job


def _job_dict() -> dict:
    return {
        "job_type": "smoke",
        "input_uri": "text://public-ops",
        "output_uri": "local://public-ops",
        "worker_image": "local/canary:latest",
        "gpu_profile": "llm_heavy",
        "metadata": {},
    }


def test_lane_registry_contains_all_launch_surfaces() -> None:
    assert set(LANES) == {
        "modal_function",
        "runpod_pod",
        "runpod_serverless",
        "vast_instance",
        "vast_pyworker_serverless",
    }


def test_resolve_lane_id_prefers_explicit_provider_module_id() -> None:
    assert resolve_lane_id("runpod", {"provider_module_id": "runpod_serverless"}) == "runpod_serverless"
    assert resolve_lane_id("vast", {"provider_module_id": "vast_pyworker_serverless"}) == "vast_pyworker_serverless"


def test_route_public_job_is_deterministic_and_records_lane() -> None:
    first = route_public_job(_job_dict())
    second = route_public_job(_job_dict())

    assert first["selected_provider"] == second["selected_provider"]
    assert first["selected_lane_id"] == second["selected_lane_id"]


def test_validate_public_job_reports_lane_per_provider() -> None:
    result = validate_public_job(_job_dict(), provider="modal")

    assert result["ok"] is True
    assert result["providers"]["modal"]["lane_id"] == "modal_function"


def test_validate_public_job_default_provider_list_uses_catalog_providers_only() -> None:
    result = validate_public_job(_job_dict())

    assert result["ok"] is True
    assert "catalog_snapshot_id" not in result["providers"]
    assert set(result["providers"]) == {"local", "modal", "ollama", "runpod", "vast"}
    assert result["providers"]["local"]["lane_id"] == ""
    assert result["providers"]["ollama"]["lane_id"] == ""


def test_plan_public_job_wraps_provider_plan_with_lane() -> None:
    result = plan_public_job(_job_dict(), provider="modal")

    assert result["ok"] is True
    assert result["selected_provider"] == "modal"
    assert result["selected_lane_id"] == "modal_function"
    assert isinstance(result["plan"], dict)


def test_schema_snapshot_includes_new_public_contract_bundle() -> None:
    result = schema_snapshot()

    assert result["ok"] is True
    assert "contracts" in result
    assert "provider_module" in result
    assert "provider_contract_probe" in result
