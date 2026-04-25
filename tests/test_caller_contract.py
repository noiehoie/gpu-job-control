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
            "operation": "daily-summary",
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
