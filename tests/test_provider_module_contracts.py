from __future__ import annotations

from unittest.mock import patch

from gpu_job.cli import build_parser
from gpu_job.contracts import plan_workload
from gpu_job.execution_plan import build_execution_plan
from gpu_job.execution_record import build_execution_record
from gpu_job.models import Job
from gpu_job.provider_contract_probe import contract_probe_spec, plan_contract_probe
from gpu_job.provider_module_contracts import (
    MODAL_FUNCTION,
    PROVIDER_MODULE_CONTRACTS,
    RUNPOD_POD,
    RUNPOD_SERVERLESS,
    VAST_INSTANCE,
    VAST_PYWORKER_SERVERLESS,
    apply_provider_module_metadata,
    provider_module_canary_evidence,
    provider_module_canary_evidence_schema,
    provider_module_probe_name,
    provider_module_contract_for_job,
    provider_module_contract_schema,
    provider_module_routing_flag_schema,
    provider_module_validation,
)
from gpu_job.workspace_registry import provider_workspace_plan, workspace_registry_schema


def test_provider_module_contract_registry_exposes_required_modules() -> None:
    schema = provider_module_contract_schema()

    assert schema["provider_module_contract_version"] == "gpu-job-provider-module-contract-v1"
    assert set(schema["registered_modules"]) >= {
        MODAL_FUNCTION,
        RUNPOD_SERVERLESS,
        RUNPOD_POD,
        VAST_INSTANCE,
        VAST_PYWORKER_SERVERLESS,
    }

    runpod = provider_module_contract_for_job({"provider_module_id": RUNPOD_POD}, "runpod")
    vast = provider_module_contract_for_job({"provider_module_id": VAST_PYWORKER_SERVERLESS}, "vast")

    assert runpod["parent_provider"] == "runpod"
    assert runpod["active_module_id"] == RUNPOD_POD
    assert {item["module_id"] for item in runpod["available_modules"]} == {RUNPOD_SERVERLESS, RUNPOD_POD}
    assert vast["parent_provider"] == "vast"
    assert vast["active_module_id"] == VAST_PYWORKER_SERVERLESS
    assert {item["module_id"] for item in vast["available_modules"]} == {VAST_INSTANCE, VAST_PYWORKER_SERVERLESS}


def test_provider_module_contract_entries_satisfy_schema_required_fields() -> None:
    schema = provider_module_contract_schema()
    required = set(schema["required_module_fields"])

    for module_id, contract in PROVIDER_MODULE_CONTRACTS.items():
        assert required.issubset(contract), module_id
        assert contract["module_id"] == module_id
        assert "terminal_states" in contract["status_model"], module_id


def test_provider_module_routing_flag_schema_is_design_only_and_disabled() -> None:
    schema = provider_module_routing_flag_schema()
    contract_schema = provider_module_contract_schema()

    assert schema["flag"] == "provider_module_routing.routing_by_module_enabled"
    assert schema["default"] is False
    assert schema["current_allowed_values"] == [False]
    assert schema["activation_stage"] == "design_only"
    assert contract_schema["provider_module_routing_flag"] == schema


def test_provider_module_canary_evidence_schema_maps_every_requirement_to_shared_categories() -> None:
    schema = provider_module_canary_evidence_schema()

    assert schema["provider_module_canary_evidence_version"] == "gpu-job-provider-module-canary-evidence-v1"
    assert set(schema["modules"]) == set(PROVIDER_MODULE_CONTRACTS)
    for module_id, contract in PROVIDER_MODULE_CONTRACTS.items():
        module_schema = schema["modules"][module_id]
        mapped_requirements = set(module_schema["requirement_observation_mapping"])
        assert mapped_requirements == set(contract["canary_requirements"]), module_id
        assert set(module_schema["required_observation_categories"]).issubset(schema["observation_categories"])


def test_provider_module_canary_evidence_uses_same_category_vocabulary_per_module() -> None:
    coverage = {
        "categories": {
            category: {"observed": True, "ok": True, "evidence_fields": [category]}
            for category in provider_module_canary_evidence_schema()["observation_categories"]
        }
    }

    runpod = provider_module_canary_evidence(
        module_id=RUNPOD_POD,
        parent_provider="runpod",
        provider_module_probe_name="runpod_pod.asr_diarization.pyannote",
        workspace_observation_coverage=coverage,
    )
    vast = provider_module_canary_evidence(
        module_id=VAST_INSTANCE,
        parent_provider="vast",
        provider_module_probe_name="vast_instance.asr_diarization.pyannote",
        workspace_observation_coverage=coverage,
    )

    assert set(runpod["observation_categories"]) == set(vast["observation_categories"])
    assert runpod["ok"] is True
    assert vast["ok"] is True
    assert runpod["requirements"]["terminate"]["ok"] is True
    assert vast["requirements"]["destroy_instance"]["ok"] is True


def test_provider_module_canary_evidence_fails_when_required_category_is_missing() -> None:
    categories = {
        category: {"observed": True, "ok": True, "evidence_fields": [category]}
        for category in provider_module_canary_evidence_schema()["observation_categories"]
    }
    categories["cleanup_result"] = {"observed": False, "ok": None, "evidence_fields": []}
    evidence = provider_module_canary_evidence(
        module_id=RUNPOD_POD,
        parent_provider="runpod",
        provider_module_probe_name="runpod_pod.asr_diarization.pyannote",
        workspace_observation_coverage={"categories": categories},
    )

    assert evidence["ok"] is False
    assert "cleanup_result" in evidence["missing_categories"]
    assert evidence["requirements"]["terminate"]["observed"] is False
    assert evidence["requirements"]["terminate"]["ok"] is False


def test_vast_pyworker_module_rejects_instance_only_identity_evidence() -> None:
    categories = {
        category: {"observed": True, "ok": True, "evidence_fields": [category]}
        for category in provider_module_canary_evidence_schema()["observation_categories"]
    }
    categories["provider_resource_identity"] = {
        "observed": True,
        "ok": True,
        "evidence_fields": ["instance_id", "provider_job_id"],
        "evidence_values": {
            "endpoint_id": "",
            "workergroup_id": "",
            "instance_id": "vast-instance-123",
            "provider_job_id": "vast-instance-123",
        },
    }

    evidence = provider_module_canary_evidence(
        module_id=VAST_PYWORKER_SERVERLESS,
        parent_provider="vast",
        provider_module_probe_name="vast_pyworker_serverless.asr.serverless_template",
        workspace_observation_coverage={"categories": categories},
    )

    assert evidence["ok"] is False
    assert "provider_resource_identity" in evidence["failed_categories"]
    assert evidence["requirements"]["endpoint_create"]["ok"] is False
    assert "endpoint_id and workergroup_id" in evidence["module_specific_failures"][0]["reason"]


def test_runpod_serverless_module_rejects_pod_only_identity_evidence() -> None:
    categories = {
        category: {"observed": True, "ok": True, "evidence_fields": [category]}
        for category in provider_module_canary_evidence_schema()["observation_categories"]
    }
    categories["provider_resource_identity"] = {
        "observed": True,
        "ok": True,
        "evidence_fields": ["pod_id", "provider_job_id"],
        "evidence_values": {
            "endpoint_id": "",
            "pod_id": "pod-123",
            "provider_job_id": "pod-123",
        },
    }

    evidence = provider_module_canary_evidence(
        module_id=RUNPOD_SERVERLESS,
        parent_provider="runpod",
        provider_module_probe_name="runpod_serverless.asr_diarization.serverless_handler",
        workspace_observation_coverage={"categories": categories},
    )

    assert evidence["ok"] is False
    assert "provider_resource_identity" in evidence["failed_categories"]
    assert evidence["requirements"]["status_poll"]["ok"] is False
    assert "endpoint_id evidence" in evidence["module_specific_failures"][0]["reason"]


def test_provider_module_contract_unknown_provider_is_empty_and_non_routing() -> None:
    contract = provider_module_contract_for_job(None, "local")

    assert contract["parent_provider"] == "local"
    assert contract["active_module_id"] == ""
    assert contract["available_module_ids"] == []
    assert contract["active_module"] == {}
    assert contract["selection"]["routing_by_module_enabled"] is False


def test_invalid_requested_provider_module_falls_back_to_parent_default() -> None:
    contract = provider_module_contract_for_job({"provider_module_id": VAST_INSTANCE}, "runpod")

    assert contract["requested_module_id"] == VAST_INSTANCE
    assert contract["active_module_id"] == RUNPOD_SERVERLESS
    assert contract["selection"]["requested_module_valid"] is False


def test_provider_module_validation_is_formal_non_routing_output() -> None:
    validation = provider_module_validation({"provider_contract_unit": RUNPOD_POD}, "runpod")

    assert validation["ok"] is True
    assert validation["input_schema"]["fields"] == ["metadata.provider_module_id", "metadata.provider_contract_unit"]
    assert validation["provider_module_contract"]["active_module_id"] == RUNPOD_POD
    assert validation["provider_module_contract"]["selection"]["routing_by_module_enabled"] is False


def test_apply_provider_module_metadata_prefers_explicit_field() -> None:
    metadata = apply_provider_module_metadata({"source_system": "test"}, provider_module_id=VAST_INSTANCE)

    assert metadata["source_system"] == "test"
    assert metadata["provider_module_id"] == VAST_INSTANCE


def test_workspace_plan_exposes_provider_module_contract_without_changing_parent_provider() -> None:
    job = _asr_job("workspace-provider-module-runpod")
    job.metadata["provider_module_id"] = RUNPOD_POD

    plan = provider_workspace_plan(job, "runpod")
    module = plan["provider_module_contract"]

    assert plan["provider"] == "runpod"
    assert module["parent_provider"] == "runpod"
    assert module["active_module_id"] == RUNPOD_POD
    assert module["selection"]["routing_by_module_enabled"] is False
    assert any(item["surface"] == "rest_v1" for item in module["active_module"]["api_surfaces"])
    assert "provider_module_contract" in workspace_registry_schema()["optional_fields"]


def test_cli_accepts_provider_module_id_without_changing_provider_argument() -> None:
    parser = build_parser()
    args = parser.parse_args(["plan", "job.json", "--provider", "runpod", "--provider-module-id", RUNPOD_POD])

    assert args.provider == "runpod"
    assert args.provider_module_id == RUNPOD_POD


def test_workspace_plan_id_is_stable_when_module_contract_expands() -> None:
    job = _asr_job("workspace-provider-module-hash-stable")
    first = provider_workspace_plan(job, "runpod")

    expanded_contract = {
        "provider_module_contract_version": "test-expanded",
        "parent_provider": "runpod",
        "active_module_id": RUNPOD_SERVERLESS,
        "available_module_ids": [RUNPOD_SERVERLESS, RUNPOD_POD, "future_runpod_mode"],
        "available_modules": [{"module_id": "future_runpod_mode", "parent_provider": "runpod"}],
        "selection": {"routing_by_module_enabled": False},
    }
    with patch("gpu_job.workspace_registry.provider_module_contract_for_job", return_value=expanded_contract):
        second = provider_workspace_plan(job, "runpod")

    assert first["workspace_plan_id"] == second["workspace_plan_id"]
    assert first["provider_module_contract"] != second["provider_module_contract"]


def test_execution_record_includes_visible_provider_module_contract() -> None:
    job = _asr_job("execution-record-provider-module")
    job.metadata["selected_provider"] = "vast"
    job.metadata["provider_module_id"] = VAST_PYWORKER_SERVERLESS
    job.metadata["workspace_plan"] = provider_workspace_plan(job, "vast")

    record = build_execution_record(job)
    module = record["workspace_plan"]["provider_module_contract"]

    assert record["provider"] == "vast"
    assert module["active_module_id"] == VAST_PYWORKER_SERVERLESS
    assert module["parent_provider"] == "vast"
    assert record["plan_quote"]["selected_option"]["provider_module_id"] == VAST_PYWORKER_SERVERLESS
    assert record["plan_quote"]["selected_option"]["routing_by_module_enabled"] is False


def test_execution_plan_still_routes_by_parent_provider_not_module() -> None:
    job = _asr_job("execution-plan-provider-module-parent")
    job.metadata["provider_module_id"] = RUNPOD_POD

    plan = build_execution_plan(job, "runpod")

    assert plan["provider"] == "runpod"
    assert "--provider" in plan["command"]
    assert "runpod" in plan["command"]
    assert RUNPOD_POD not in plan["command"]


def test_contract_probe_specs_expose_module_probe_names_without_renaming_probe() -> None:
    spec = contract_probe_spec("runpod", "runpod.asr_diarization.serverless_handler")
    planned = plan_contract_probe("runpod", "runpod.asr_diarization.serverless_handler")

    assert spec["provider_module_id"] == RUNPOD_SERVERLESS
    assert planned["probe_name"] == "runpod.asr_diarization.serverless_handler"
    assert planned["provider_module_probe_name"] == "runpod_serverless.asr_diarization.serverless_handler"
    assert planned["provider_module_contract"]["active_module_id"] == RUNPOD_SERVERLESS


def test_provider_module_probe_name_is_deterministic_alias() -> None:
    assert (
        provider_module_probe_name("vast.asr_diarization.pyannote", {"provider": "vast", "provider_module_id": VAST_INSTANCE})
        == "vast_instance.asr_diarization.pyannote"
    )


def test_workload_plan_records_requested_provider_module_in_options() -> None:
    result = plan_workload(
        {
            "workload_kind": "transcription.whisper",
            "input_uri": "file:///tmp/input.mp4",
            "provider_module_id": RUNPOD_POD,
            "business_context": {"budget_class": "standard"},
        }
    )

    runpod_option = next(item for item in result["plan"]["options"] if item["provider"] == "runpod")
    module = runpod_option["provider_module_contract"]
    assert module["active_module_id"] == RUNPOD_POD
    assert module["selection"]["routing_by_module_enabled"] is False
    if result["plan"]["selected_option"]["provider"] == "runpod":
        assert result["plan_quote"]["explanation"]["selected_provider_module_id"] == RUNPOD_POD


def _asr_job(job_id: str) -> Job:
    return Job(
        job_id=job_id,
        job_type="asr",
        input_uri="file:///tmp/input.mp4",
        output_uri="local://out",
        worker_image="gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
        gpu_profile="asr_diarization",
        model="large-v3",
        metadata={"input": {"diarize": True, "language": "ja"}, "secret_refs": ["hf_token"]},
    )
