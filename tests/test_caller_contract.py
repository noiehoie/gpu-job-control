from __future__ import annotations

from pathlib import Path
import json

from gpu_job.caller_contract import (
    CALLER_CONTRACT_VERSION,
    caller_request_schema,
    compile_caller_request,
    operation_catalog_snapshot,
    validate_caller_request,
)


def _caller_request() -> dict:
    return {
        "contract_version": CALLER_CONTRACT_VERSION,
        "operation": "llm.generate",
        "input": {
            "uri": "text://Summarize the contract.",
            "parameters": {"prompt": "Summarize the contract.", "max_tokens": 128},
        },
        "output_expectation": {
            "target_uri": "local://caller-output",
            "required_files": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
        },
        "limits": {"max_runtime_minutes": 10, "max_cost_usd": 1, "max_output_gb": 1},
        "idempotency": {"key": "caller-req-001"},
        "caller": {
            "system": "news-system",
            "operation": "smoke",
            "request_id": "req-001",
            "version": "2026.04.25",
        },
        "trace_context": {"trace_id": "trace-001"},
        "preferences": {"quality_requires_gpu": True},
    }


def test_caller_request_schema_exposes_contract_rules() -> None:
    schema = caller_request_schema()
    assert schema["properties"]["contract_version"]["const"] == CALLER_CONTRACT_VERSION
    assert "job_type" in schema["forbidden_top_level_fields"]
    assert schema["backward_compatibility_policy"]["current_contract_version"] == CALLER_CONTRACT_VERSION


def test_operation_catalog_snapshot_is_closed() -> None:
    snapshot = operation_catalog_snapshot()
    assert snapshot["ok"] is True
    assert snapshot["free_form_job_type_allowed"] is False
    assert "llm.generate" in snapshot["operations"]
    assert "gpu.container.run" in snapshot["operations"]


def test_cloud_gpu_lanes_are_generic_catalog_candidates() -> None:
    snapshot = operation_catalog_snapshot()
    expected_lanes = {
        "modal_function",
        "runpod_pod",
        "runpod_serverless",
        "vast_instance",
        "vast_pyworker_serverless",
    }
    for operation in (
        "asr.transcribe",
        "asr.transcribe_diarize",
        "llm.generate",
        "embedding.embed",
        "ocr.document",
        "ocr.image",
        "gpu.container.run",
        "smoke.gpu",
    ):
        spec = snapshot["operations"][operation]
        assert set(spec["allowed_lanes"]) == expected_lanes
        assert spec["forbidden_lanes"] == []


def test_validate_caller_request_accepts_valid_payload() -> None:
    result = validate_caller_request(_caller_request())
    assert result["ok"] is True
    assert result["errors"] == []


def test_validate_caller_request_rejects_job_shape_fields() -> None:
    payload = _caller_request()
    payload["job_type"] = "llm_heavy"
    result = validate_caller_request(payload)
    assert result["ok"] is False
    assert "forbidden top-level fields present: job_type" in result["errors"]


def test_compile_caller_request_produces_job_shape() -> None:
    result = compile_caller_request(_caller_request())
    assert result["ok"] is True
    job = result["job"]
    assert job["job_type"] == "llm_heavy"
    assert job["gpu_profile"] == "llm_heavy"
    assert job["metadata"]["task_family"] == "llm.generate"
    assert job["metadata"]["caller_request_id"] == "req-001"
    assert job["metadata"]["idempotency_key"] == "caller-req-001"
    assert job["metadata"]["routing"]["quality_tier"] == "development"
    assert job["metadata"]["routing"]["local_fixed_resource_policy"] == "unknown"


def test_compile_caller_request_requires_closed_operation() -> None:
    payload = _caller_request()
    payload["operation"] = "unknown.op"
    result = compile_caller_request(payload)
    assert result["ok"] is False
    assert "unsupported operation: unknown.op" in result["errors"]


def test_compile_caller_request_is_deterministic() -> None:
    first = compile_caller_request(_caller_request())
    second = compile_caller_request(_caller_request())

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["job"] == second["job"]


def test_all_caller_request_examples_validate_and_compile() -> None:
    base = Path("examples/caller-requests")
    for path in sorted(base.glob("*.json")):
        payload = json.loads(path.read_text())
        validation = validate_caller_request(payload)
        compiled = compile_caller_request(payload)
        assert validation["ok"] is True, path.name
        assert compiled["ok"] is True, path.name


def test_generic_gpu_lane_examples_cover_every_public_lane() -> None:
    expected_lanes = {
        "modal_function",
        "runpod_pod",
        "runpod_serverless",
        "vast_instance",
        "vast_pyworker_serverless",
    }
    observed_lanes = set()
    for path in sorted(Path("examples/caller-requests").glob("gpu.container.run.*.json")):
        payload = json.loads(path.read_text())
        compiled = compile_caller_request(payload)
        assert compiled["ok"] is True, path.name
        job = compiled["job"]
        assert job["job_type"] == "gpu_task", path.name
        observed_lanes.add(job["metadata"].get("execution_lane_id"))
        assert job["metadata"].get("execution_lane_id") == payload["preferences"]["execution_lane_id"]
        assert job["metadata"].get("provider_module_id") == payload["preferences"]["execution_lane_id"]

    assert observed_lanes == expected_lanes


def test_validate_caller_request_enforces_production_quality_constraints() -> None:
    payload = _caller_request()
    payload["preferences"] = {
        "quality_tier": "production_quality",
        "model_size_billion_parameters": 32,
        "quality_requires_gpu": True,
        "local_fixed_resource_policy": "unsuitable",
    }
    result = validate_caller_request(payload)
    assert result["ok"] is False
    assert "production_quality llm.generate requires >=70B model" in result["errors"]

    payload["preferences"]["model_size_billion_parameters"] = 72
    payload["preferences"]["quality_requires_gpu"] = False
    result = validate_caller_request(payload)
    assert result["ok"] is False
    assert "production_quality llm.generate requires quality_requires_gpu=true" in result["errors"]

    payload["preferences"]["quality_requires_gpu"] = True
    payload["preferences"]["local_fixed_resource_policy"] = "suitable"
    result = validate_caller_request(payload)
    assert result["ok"] is False
    assert "production_quality llm.generate requires local_fixed_resource_policy=unsuitable" in result["errors"]

    payload["preferences"]["local_fixed_resource_policy"] = "unsuitable"
    result = validate_caller_request(payload)
    assert result["ok"] is True


def test_validate_caller_request_allows_small_models_only_outside_production_quality() -> None:
    payload = _caller_request()
    payload["preferences"] = {
        "quality_tier": "degraded",
        "model_size_billion_parameters": 32,
        "quality_requires_gpu": True,
        "local_fixed_resource_policy": "unsuitable",
    }
    result = validate_caller_request(payload)
    assert result["ok"] is True

    payload["preferences"] = {
        "quality_tier": "production_quality",
        "model_size_class": "under_70b",
        "quality_requires_gpu": True,
        "local_fixed_resource_policy": "unsuitable",
    }
    result = validate_caller_request(payload)
    assert result["ok"] is False
    assert "production_quality llm.generate requires >=70B model" in result["errors"]

    payload["preferences"]["model_size_class"] = "at_least_70b"
    result = compile_caller_request(payload)
    assert result["ok"] is True
    assert result["job"]["metadata"]["routing"]["quality_tier"] == "production_quality"
    assert result["job"]["metadata"]["routing"]["model_size_class"] == "at_least_70b"


def test_validate_caller_request_rejects_unknown_preferences() -> None:
    payload = _caller_request()
    payload["preferences"] = {"quality_tier": "degraded", "provider": "modal"}

    result = validate_caller_request(payload)

    assert result["ok"] is False
    assert "unsupported preferences present: provider" in result["errors"]


def test_generic_gpu_container_operation_requires_workload_and_compiles() -> None:
    payload = _caller_request()
    payload["operation"] = "gpu.container.run"
    payload["input"] = {
        "uri": "none://generic-gpu-task",
        "parameters": {
            "workload": {
                "kind": "container",
                "entrypoint": ["python", "-m", "worker"],
            }
        },
    }
    payload["preferences"] = {
        "worker_image": "auto",
        "quality_requires_gpu": True,
        "quality_tier": "development",
        "local_fixed_resource_policy": "unsuitable",
    }

    result = compile_caller_request(payload)

    assert result["ok"] is True
    assert result["job"]["job_type"] == "gpu_task"
    assert result["job"]["gpu_profile"] == "generic_gpu"
    assert result["job"]["metadata"]["operation_contract"]["allowed_lanes"] == [
        "modal_function",
        "runpod_pod",
        "runpod_serverless",
        "vast_instance",
        "vast_pyworker_serverless",
    ]

    del payload["input"]["parameters"]["workload"]
    result = validate_caller_request(payload)
    assert result["ok"] is False
    assert "input.parameters.workload is required for operation gpu.container.run" in result["errors"]


def test_generic_gpu_container_operation_accepts_explicit_execution_lane() -> None:
    payload = _caller_request()
    payload["operation"] = "gpu.container.run"
    payload["input"] = {
        "uri": "none://generic-gpu-task",
        "parameters": {"workload": {"kind": "container", "entrypoint": ["true"]}},
    }
    payload["preferences"] = {
        "execution_lane_id": "runpod_serverless",
        "worker_image": "auto",
        "quality_requires_gpu": True,
        "quality_tier": "development",
        "local_fixed_resource_policy": "unsuitable",
    }

    result = compile_caller_request(payload)

    assert result["ok"] is True
    assert result["job"]["metadata"]["execution_lane_id"] == "runpod_serverless"
    assert result["job"]["metadata"]["provider_module_id"] == "runpod_serverless"


def test_execution_lane_id_is_closed_by_operation_catalog() -> None:
    payload = _caller_request()
    payload["operation"] = "gpu.container.run"
    payload["input"] = {
        "uri": "none://generic-gpu-task",
        "parameters": {"workload": {"kind": "container", "entrypoint": ["true"]}},
    }
    payload["preferences"] = {
        "execution_lane_id": "unknown_lane",
        "worker_image": "auto",
        "quality_requires_gpu": True,
        "quality_tier": "development",
        "local_fixed_resource_policy": "unsuitable",
    }

    result = validate_caller_request(payload)

    assert result["ok"] is False
    assert "preferences.execution_lane_id is not allowed for operation gpu.container.run: unknown_lane" in result["errors"]
