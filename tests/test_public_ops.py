from __future__ import annotations

import gpu_job.public_ops as public_ops
from gpu_job.lanes import LANES, resolve_lane_id
from gpu_job.public_ops import plan_public_job, route_public_job, schema_snapshot, submit_public_job, validate_public_job


def _job_dict() -> dict:
    return {
        "job_type": "smoke",
        "input_uri": "text://public-ops",
        "output_uri": "local://public-ops",
        "worker_image": "local/canary:latest",
        "gpu_profile": "llm_heavy",
        "metadata": {},
    }


def _caller_request() -> dict:
    return {
        "contract_version": "gpu-job-caller-request-v1",
        "operation": "llm.generate",
        "input": {
            "uri": "text://Summarize the common GPU job control contract.",
            "parameters": {"prompt": "Summarize the common GPU job control contract."},
        },
        "output_expectation": {
            "target_uri": "local://caller-plan",
            "required_files": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
        },
        "limits": {"max_runtime_minutes": 5, "max_cost_usd": 1, "max_output_gb": 1},
        "idempotency": {"key": "caller-plan-001"},
        "caller": {
            "system": "integration-test",
            "operation": "summary",
            "request_id": "caller-plan-001",
            "version": "2026.04.25",
        },
        "trace_context": {"trace_id": "trace-001"},
    }


def _stub_route_job(job) -> dict:
    return {
        "ok": True,
        "job_id": job.job_id,
        "selected_provider": "modal",
        "eligible_ranked": [{"provider": "modal", "score": 0.0}],
        "provider_decisions": {},
        "provider_signals": {},
        "decision": {"reason": "stubbed for deterministic unit test", "strategy": "unit_test"},
        "candidates": ["modal"],
        "gpu_profile": job.gpu_profile,
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


def test_resolve_lane_id_returns_empty_for_fixed_capacity_providers() -> None:
    assert resolve_lane_id("local") == ""
    assert resolve_lane_id("ollama") == ""


def test_route_public_job_is_deterministic_and_records_lane(monkeypatch) -> None:
    monkeypatch.setattr(public_ops, "route_job", _stub_route_job)
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


def test_plan_public_job_wraps_provider_plan_with_lane(monkeypatch) -> None:
    monkeypatch.setattr(public_ops, "route_job", _stub_route_job)
    result = plan_public_job(_job_dict(), provider="modal")

    assert result["ok"] is True
    assert result["selected_provider"] == "modal"
    assert result["selected_lane_id"] == "modal_function"
    assert isinstance(result["plan"], dict)


def test_plan_public_job_accepts_caller_request_shape(monkeypatch) -> None:
    monkeypatch.setattr(public_ops, "route_job", _stub_route_job)
    result = plan_public_job(_caller_request(), provider="modal")

    assert result["ok"] is True
    assert result["selected_provider"] == "modal"
    assert result["selected_lane_id"] == "modal_function"
    assert result["plan"]["execution_plan"]["job_type"] == "llm_heavy"
    assert result["route_result"]["job_id"].startswith("llm_heavy-")


def test_plan_public_job_accepts_ollama_transport_provider(monkeypatch) -> None:
    monkeypatch.setattr(public_ops, "route_job", _stub_route_job)
    result = plan_public_job(_caller_request(), provider="ollama")

    assert result["ok"] is True
    assert result["selected_provider"] == "ollama"
    assert result["selected_lane_id"] == ""
    assert result["plan"]["provider"] == "ollama"


def test_submit_public_job_accepts_ollama_transport_provider_without_execute(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(public_ops, "route_job", _stub_route_job)
    result = submit_public_job(_caller_request(), provider="ollama", execute=False)

    assert result["ok"] is True
    assert result["selected_provider"] == "ollama"
    assert result["selected_lane_id"] == ""
    assert result["job"]["status"] == "planned"


def test_validate_public_job_fail_closes_invalid_caller_request() -> None:
    payload = _caller_request()
    del payload["idempotency"]

    result = validate_public_job(payload, provider="modal")

    assert result["ok"] is False
    assert "idempotency.key is required" in result["errors"]


def test_schema_snapshot_includes_new_public_contract_bundle() -> None:
    result = schema_snapshot()

    assert result["ok"] is True
    assert "contracts" in result
    assert "provider_module" in result
    assert "provider_contract_probe" in result
    assert "caller_request" in result
