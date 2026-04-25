from __future__ import annotations

from pathlib import Path
import json
import re

from gpu_job.caller_contract import FORBIDDEN_TOP_LEVEL_FIELDS, caller_request_schema, validate_caller_request


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def test_finished_product_gate_has_35_acceptance_rows() -> None:
    text = _read("docs/finished-product-gate.md")
    rows = re.findall(r"^\| FPG-\d{2} \|", text, flags=re.MULTILINE)
    assert len(rows) == 35
    assert "provider_adapter_diff=[]" in text
    assert "routing_by_module_enabled=false" in text
    assert "stop_conditions=[]" in text


def test_finished_product_gate_linked_from_public_docs() -> None:
    assert "docs/finished-product-gate.md" in _read("README.md")
    assert "finished-product-gate.md" in _read("docs/index.md")


def test_public_product_docs_exist_and_are_linked() -> None:
    required = {
        "docs/error-codes.md": ["provider_backpressure", "Retry", "Provider Responsibility Boundary"],
        "docs/data-lifecycle.md": ["retention", "deletion", "privacy"],
        "docs/product-invariants.md": ["routing_by_module_enabled=false", "provider_adapter_diff=[]"],
        "schemas/gpu-job-public-api.openapi.json": ["openapi", "/validate", "/submit"],
    }
    index = _read("docs/index.md")
    for path, needles in required.items():
        text = _read(path)
        assert Path(path).name in index or path.startswith("schemas/")
        for needle in needles:
            assert needle in text


def test_public_openapi_contains_closed_endpoint_set() -> None:
    spec = json.loads(_read("schemas/gpu-job-public-api.openapi.json"))
    assert spec["openapi"] == "3.1.0"
    assert set(spec["paths"]) == {
        "/schemas/caller-request",
        "/schemas/contracts",
        "/schemas/plan-quote",
        "/schemas/execution-record",
        "/schemas/provider-workspace",
        "/schemas/provider-module",
        "/schemas/provider-contract-probe",
        "/catalog/operations",
        "/catalog/caller-prompt",
        "/validate",
        "/route",
        "/plan",
        "/submit",
        "/jobs/{job_id}",
        "/verify/{job_id}",
    }


def test_public_api_golden_response_contract_is_documented() -> None:
    public_api = _read("docs/public-api.md")
    error_codes = _read("docs/error-codes.md")
    assert "| `POST` | `/validate` |" in public_api
    assert "| `POST` | `/submit` |" in public_api
    assert "| 429 | `backpressure` | yes |" in error_codes
    assert "| 409 | `quota_block` | no |" in error_codes


def test_generic_gpu_lane_examples_are_documented() -> None:
    caller_contract = _read("docs/caller-contract.md")
    integration = _read("docs/client-integration-guide.md")
    operation_catalog = _read("docs/operation-catalog.md")
    for lane in (
        "modal_function",
        "runpod_pod",
        "runpod_serverless",
        "vast_instance",
        "vast_pyworker_serverless",
    ):
        example_name = f"gpu.container.run.{lane}.json"
        assert lane in caller_contract
        assert lane in integration
        assert example_name in caller_contract
        assert example_name in integration
    assert "preferences.execution_lane_id" in operation_catalog
    assert "does not fall back" in operation_catalog


def test_caller_schema_and_python_forbidden_fields_stay_aligned() -> None:
    schema = caller_request_schema()
    payload = {
        "contract_version": "gpu-job-caller-request-v1",
        "operation": "llm.generate",
        "input": {"uri": "text://hello", "parameters": {"prompt": "hello"}},
        "output_expectation": {"target_uri": "local://out", "required_files": ["result.json"]},
        "limits": {"max_runtime_minutes": 1, "max_cost_usd": 1, "max_output_gb": 1},
        "idempotency": {"key": "k"},
        "caller": {"system": "s", "operation": "o", "request_id": "r", "version": "v"},
    }
    assert schema["additionalProperties"] is False
    for field in sorted(FORBIDDEN_TOP_LEVEL_FIELDS):
        candidate = dict(payload)
        candidate[field] = "forbidden"
        result = validate_caller_request(candidate)
        assert result["ok"] is False, field
        assert any(field in err for err in result["errors"]), field
