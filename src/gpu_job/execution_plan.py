from __future__ import annotations

from typing import Any

from .image_contracts import image_contract_status
from .models import Job
from .provider_catalog import provider_capability
from .requirements import load_requirement_registry


EXECUTION_PLAN_VERSION = "gpu-job-execution-plan-v1"
STAGED_INPUT_PLACEHOLDER = "<staged-input-path>"
DEFAULT_ARTIFACT_DIR_PLACEHOLDER = "/workspace/artifacts"


def build_execution_plan(job: Job, provider: str) -> dict[str, Any]:
    metadata_input = job.metadata.get("input") if isinstance(job.metadata.get("input"), dict) else {}
    backends = {"transcription": "faster_whisper"} if job.job_type == "asr" else {}
    if bool(metadata_input.get("diarize") or metadata_input.get("speaker_diarization")):
        backends["speaker_diarization"] = "pyannote"
    runtime = _provider_runtime(provider, job.gpu_profile)
    image_status = _normalize_image_status(image_contract_status(runtime, backends) if backends else {"ok": True, "status": "not_required"})
    contract = dict(image_status.get("contract") or {})
    image_distribution = dict((contract.get("provider_images") or {}).get(provider) or {})
    provider_image = str(image_distribution.get("image") or "")
    secret_refs = sorted(set(str(item) for item in (contract.get("required_secrets") or job.metadata.get("secret_refs") or []) if item))
    command = [
        contract.get("entrypoint") or _default_entrypoint(job),
        "--job-id",
        job.job_id,
        "--artifact-dir",
        DEFAULT_ARTIFACT_DIR_PLACEHOLDER,
        "--gpu-profile",
        job.gpu_profile,
        "--input-uri",
        STAGED_INPUT_PLACEHOLDER,
        "--provider",
        provider,
    ]
    if job.model:
        command.extend(["--model-name", job.model])
    if metadata_input.get("language"):
        command.extend(["--language", str(metadata_input["language"])])
    if metadata_input.get("compute_type"):
        command.extend(["--compute-type", str(metadata_input["compute_type"])])
    if bool(metadata_input.get("diarize") or metadata_input.get("speaker_diarization")):
        command.append("--diarize")
        command.extend(["--speaker-model", str(metadata_input.get("speaker_model") or "pyannote/speaker-diarization-3.1")])
    return {
        "execution_plan_version": EXECUTION_PLAN_VERSION,
        "provider": provider,
        "job_id": job.job_id,
        "job_type": job.job_type,
        "gpu_profile": job.gpu_profile,
        "provider_support_contract": dict(provider_capability(provider).get("support_contract") or {}),
        "image_contract": image_status,
        "worker_image": provider_image or contract.get("image") or runtime.get("worker_image") or job.worker_image,
        "provider_image": provider_image,
        "image_distribution": image_distribution,
        "entrypoint": command[0],
        "command": command,
        "staging": {
            "input_uri": job.input_uri,
            "input_mode": "staged_input_path",
            "staged_input_placeholder": STAGED_INPUT_PLACEHOLDER,
            "artifact_dir_placeholder": DEFAULT_ARTIFACT_DIR_PLACEHOLDER,
            "artifact_dir_binding": "provider_adapter_must_bind_to_workspace_plan_or_store_artifact_dir",
        },
        "command_shape": {
            "tokenized": True,
            "entrypoint_is_command_argv0": True,
        },
        "artifact_contract": contract.get("artifact_contract")
        or ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
        "required_backends": sorted(set(backends.values())),
        "secret_refs": secret_refs,
    }


def execution_plan_schema() -> dict[str, Any]:
    return {
        "execution_plan_version": EXECUTION_PLAN_VERSION,
        "required_fields": [
            "execution_plan_version",
            "provider",
            "job_id",
            "job_type",
            "gpu_profile",
            "provider_support_contract",
            "image_contract",
            "worker_image",
            "provider_image",
            "image_distribution",
            "entrypoint",
            "command",
            "staging",
            "command_shape",
            "artifact_contract",
            "required_backends",
            "secret_refs",
        ],
        "invariants": [
            "provider equals the provider argument passed to build_execution_plan",
            "command is tokenized and entrypoint equals command[0]",
            "required_backends is sorted and unique",
            "secret_refs is sorted and unique",
            "staging.staged_input_placeholder marks the provider-specific input path replacement point",
            "worker_image resolves in order: provider image, logical contract image, runtime worker_image, job.worker_image",
            "provider adapters must not reinterpret command as a shell string",
        ],
    }


def _provider_runtime(provider: str, gpu_profile: str) -> dict[str, Any]:
    registry = load_requirement_registry()
    return dict((registry.get("provider_runtimes") or {}).get(f"{provider}:{gpu_profile}") or {})


def _normalize_image_status(status: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(status)
    normalized.setdefault("ok", False)
    normalized.setdefault("status", "unknown")
    normalized.setdefault("contract_id", "")
    normalized.setdefault("contract", {})
    normalized.setdefault("required_backends", [])
    normalized.setdefault("reason", "")
    normalized["required_backends"] = sorted(set(str(item) for item in normalized.get("required_backends") or []))
    return normalized


def _default_entrypoint(job: Job) -> str:
    if job.job_type == "asr":
        return "gpu-job-asr-worker"
    return "gpu-job-worker"
