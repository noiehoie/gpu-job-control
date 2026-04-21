from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re
import time

from .error_class import classify_error
from .image_contracts import load_image_contract_registry
from .manifest import build_manifest
from .models import Job, app_data_dir, make_job_id, now_unix
from .requirements import load_requirement_registry
from .store import JobStore
from .verify import DEFAULT_REQUIRED, collect_hardware_utilization_evidence, verify_artifacts


CONTRACT_PROBE_VERSION = "gpu-job-provider-contract-probe-v1"
WORKSPACE_OBSERVATION_CATEGORIES = [
    "provider_resource_identity",
    "image_contract",
    "secret_availability",
    "workspace_cache",
    "startup_phases",
    "queue_or_reservation",
    "model_load",
    "gpu_execution",
    "artifact_contract",
    "cost_guard",
    "cleanup_result",
    "provider_residue",
]


DEFAULT_CONTRACT_PROBES: dict[str, dict[str, Any]] = {
    "modal.llm_heavy.qwen2_5_32b": {
        "provider": "modal",
        "workload_family": "llm_heavy",
        "job_type": "llm_heavy",
        "gpu_profile": "llm_heavy",
        "expected_model": "Qwen/Qwen2.5-32B-Instruct",
        "expected_image": "gpu-job-modal-llm",
        "expected_image_digest": "",
        "forbidden_models": ["Qwen/Qwen2.5-0.5B-Instruct"],
        "required_files": [*DEFAULT_REQUIRED],
        "require_gpu_utilization": True,
        "cache_required": True,
    },
    "runpod.llm_heavy.endpoint_openai": {
        "provider": "runpod",
        "workload_family": "llm_heavy",
        "job_type": "llm_heavy",
        "gpu_profile": "llm_heavy",
        "expected_model": "",
        "expected_image": "",
        "expected_image_digest": "",
        "forbidden_models": [],
        "required_files": [*DEFAULT_REQUIRED],
        "require_gpu_utilization": True,
        "cache_required": False,
    },
    "runpod.llm_heavy.pod_http": {
        "provider": "runpod",
        "workload_family": "llm_heavy",
        "job_type": "llm_heavy",
        "gpu_profile": "llm_heavy",
        "expected_model": "",
        "expected_image": "",
        "expected_image_digest": "",
        "forbidden_models": [],
        "required_files": [*DEFAULT_REQUIRED],
        "require_gpu_utilization": True,
        "cache_required": False,
    },
    "vast.asr.serverless_template": {
        "provider": "vast",
        "workload_family": "asr",
        "job_type": "asr",
        "gpu_profile": "asr_fast",
        "expected_model": "whisper-large-v3",
        "expected_image": "gpu-job-asr-worker",
        "expected_image_digest": "",
        "forbidden_models": [],
        "required_files": [*DEFAULT_REQUIRED],
        "require_gpu_utilization": True,
        "cache_required": False,
    },
    "vast.asr_diarization.pyannote": {
        "provider": "vast",
        "workload_family": "asr",
        "job_type": "asr",
        "gpu_profile": "asr_diarization",
        "expected_model": "pyannote/speaker-diarization-3.1",
        "expected_image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
        "expected_image_digest": "",
        "forbidden_models": [],
        "required_files": [*DEFAULT_REQUIRED],
        "require_gpu_utilization": False,
        "cache_required": True,
        "workspace_contract_required": True,
    },
    "modal.asr_diarization.pyannote": {
        "provider": "modal",
        "workload_family": "asr",
        "job_type": "asr",
        "gpu_profile": "asr_diarization",
        "expected_model": "pyannote/speaker-diarization-3.1",
        "expected_image": "gpu-job-modal-asr",
        "expected_image_digest": "",
        "forbidden_models": [],
        "required_files": [*DEFAULT_REQUIRED],
        "require_gpu_utilization": False,
        "cache_required": False,
    },
    "runpod.asr_diarization.pyannote": {
        "provider": "runpod",
        "workload_family": "asr",
        "job_type": "asr",
        "gpu_profile": "asr_diarization",
        "expected_model": "pyannote/speaker-diarization-3.1",
        "expected_image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
        "expected_image_digest": "",
        "forbidden_models": [],
        "required_files": [*DEFAULT_REQUIRED],
        "require_gpu_utilization": False,
        "cache_required": True,
        "workspace_contract_required": True,
    },
    "runpod.asr_diarization.serverless_handler": {
        "provider": "runpod",
        "workload_family": "asr",
        "job_type": "asr",
        "gpu_profile": "asr_diarization",
        "expected_model": "pyannote/speaker-diarization-3.1",
        "expected_image": "gpu-job/asr-diarization-runpod-serverless:large-v3-pyannote3.3.2-cuda12.4",
        "expected_image_digest": "",
        "forbidden_models": [],
        "required_files": [*DEFAULT_REQUIRED],
        "require_gpu_utilization": False,
        "cache_required": True,
        "workspace_contract_required": True,
        "image_contract_id": "asr-diarization-runpod-serverless-large-v3-pyannote3.3.2-cuda12.4",
        "serverless_handler_contract_required": True,
    },
}

MODEL_RE = re.compile(r"(?:Qwen/)?Qwen[0-9.]+-[A-Za-z0-9_.-]+(?:-[A-Za-z0-9_.-]+)*")
FETCHING_RE = re.compile(r"Fetching\s+\d+\s+files", re.IGNORECASE)
HTTP_STATUS_RE = re.compile(r"\bHTTP\s+([1-5][0-9]{2})\b", re.IGNORECASE)


def contract_probe_dir() -> Path:
    path = app_data_dir() / "provider-contract-probes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def provider_contract_probe_schema() -> dict[str, Any]:
    return {
        "contract_probe_version": CONTRACT_PROBE_VERSION,
        "required_top_level_fields": [
            "contract_probe_version",
            "provider",
            "probe_name",
            "execution_mode",
            "spec",
            "observed",
            "checks",
            "failure",
            "ok",
            "recorded_at",
        ],
        "execution_modes": ["planned", "fixture", "executed"],
        "verdicts": ["pass", "fail", "partial"],
        "probe_names": sorted(DEFAULT_CONTRACT_PROBES),
        "workspace_observation_categories": [*WORKSPACE_OBSERVATION_CATEGORIES],
        "workspace_observation_coverage": {
            "coverage_version": "gpu-job-workspace-observation-coverage-v1",
            "location": "record.observed.workspace_observation_coverage",
            "rule": "coverage is a read-side observation surface only; existing checks remain the pass/fail gate",
            "category_shape": {
                "observed": "bool; whether deterministic evidence was present in artifact files",
                "ok": "bool|null; category verdict when observed, null when missing",
                "evidence_fields": "list[str]; deterministic source fields used for the category",
            },
        },
        "canary_rule": "live provider canaries are admin-only and must record cleanup_result and provider_residue for billable resources",
    }


def list_contract_probes() -> dict[str, Any]:
    return {
        "ok": True,
        "contract_probe_version": CONTRACT_PROBE_VERSION,
        "probes": DEFAULT_CONTRACT_PROBES,
        "recent": recent_contract_probe_summary(),
    }


def contract_probe_spec(provider: str, probe_name: str = "") -> dict[str, Any]:
    if probe_name:
        spec = DEFAULT_CONTRACT_PROBES.get(probe_name)
        if spec is None:
            raise ValueError(f"unknown contract probe: {probe_name}")
        if provider and spec["provider"] != provider:
            raise ValueError(f"probe {probe_name} belongs to provider {spec['provider']}, not {provider}")
        enriched = _enrich_probe_spec(dict(spec))
        enriched["probe_name"] = probe_name
        return enriched
    matches = [_enrich_probe_spec(dict(spec)) for spec in DEFAULT_CONTRACT_PROBES.values() if spec["provider"] == provider]
    if not matches:
        raise ValueError(f"no default contract probe for provider: {provider}")
    return matches[0]


def plan_contract_probe(provider: str, probe_name: str = "") -> dict[str, Any]:
    spec = contract_probe_spec(provider, probe_name)
    return {
        "ok": True,
        "contract_probe_version": CONTRACT_PROBE_VERSION,
        "execution_mode": "planned",
        "provider": spec["provider"],
        "probe_name": _probe_name(spec),
        "spec": spec,
        "note": "contract probes do not submit live cloud work unless an explicit execute path is added by the caller",
    }


def active_contract_probe(provider: str, probe_name: str = "", *, execute: bool = True) -> dict[str, Any]:
    spec = contract_probe_spec(provider, probe_name)
    if not execute:
        return plan_contract_probe(provider, probe_name)
    from .runner import submit_job

    job = _canary_job(spec)
    result = submit_job(job, provider_name=spec["provider"], execute=True)
    artifact_dir = JobStore().artifact_dir(job.job_id)
    record = parse_contract_probe_artifact(
        artifact_dir,
        provider=spec["provider"],
        probe_name=_probe_name(spec, probe_name),
        spec=spec,
        execution_mode="executed",
        append=True,
    )
    return {
        "ok": bool(record.get("ok")) and bool(result.get("ok")),
        "contract_probe_version": CONTRACT_PROBE_VERSION,
        "provider": spec["provider"],
        "probe_name": _probe_name(spec, probe_name),
        "job_id": job.job_id,
        "submit_result": result,
        "record": record,
    }


def parse_contract_probe_artifact(
    artifact_dir: str | Path,
    *,
    provider: str = "",
    probe_name: str = "",
    spec: dict[str, Any] | None = None,
    execution_mode: str = "fixture",
    append: bool = False,
) -> dict[str, Any]:
    path = Path(artifact_dir)
    spec = dict(spec or contract_probe_spec(provider, probe_name))
    provider = str(provider or spec["provider"])
    required = list(spec.get("required_files") or DEFAULT_REQUIRED)
    text = _artifact_text(path)
    result = _read_json(path / "result.json")
    metrics = _read_json(path / "metrics.json")
    verify_payload = _read_json(path / "verify.json")
    probe_info = _read_json(path / "probe_info.json")
    artifact_verify = verify_artifacts(
        path,
        required=required,
        require_manifest=bool(spec.get("require_manifest", False)),
        require_gpu_utilization=bool(spec.get("require_gpu_utilization", False)),
        execution_class="gpu",
    )
    observed_model = _observed_model(result, metrics, verify_payload, probe_info, text)
    observed = {
        "model": observed_model,
        "image": _observed_image(result, metrics, probe_info),
        "artifact_contract": build_manifest(path) if path.exists() else {"files": [], "file_count": 0, "total_bytes": 0},
        "hardware": _hardware_summary(metrics, probe_info),
        "gpu_utilization_evidence": collect_hardware_utilization_evidence(path / "metrics.json"),
        "cache": _cache_summary(result, metrics, probe_info, text),
        "workspace_contract": _workspace_contract_summary(result, metrics, probe_info),
        "http_statuses": [int(item) for item in HTTP_STATUS_RE.findall(text)],
    }
    observed["workspace_observation_coverage"] = workspace_observation_coverage(provider, observed, artifact_verify)
    checks = _checks(spec, observed, artifact_verify, result, verify_payload)
    failure = _failure(provider, spec, checks, observed, text, artifact_verify)
    ok = all(bool(value) for value in checks.values())
    record = {
        "contract_probe_version": CONTRACT_PROBE_VERSION,
        "provider": provider,
        "probe_name": _probe_name(spec, probe_name),
        "workload_family": spec.get("workload_family"),
        "execution_mode": execution_mode,
        "spec": spec,
        "observed": observed,
        "checks": checks,
        "failure": failure,
        "verdict": "pass" if ok else "fail",
        "ok": ok,
        "recorded_at": now_unix(),
        "artifact_dir": str(path),
    }
    if append:
        append_contract_probe(record)
    return record


def recent_contract_probe_summary(limit: int = 100) -> dict[str, Any]:
    path = contract_probe_dir() / "provider-contract-probes.jsonl"
    rows: list[dict[str, Any]] = []
    if path.is_file():
        for line in path.read_text().splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    latest: dict[str, Any] = {}
    for row in rows:
        latest[str(row.get("probe_name") or row.get("provider") or "")] = row
    return {"ok": True, "contract_probe_version": CONTRACT_PROBE_VERSION, "count": len(rows), "latest": latest, "path": str(path)}


def append_contract_probe(record: dict[str, Any]) -> None:
    path = contract_probe_dir() / "provider-contract-probes.jsonl"
    with path.open("a") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _probe_name(spec: dict[str, Any], fallback: str = "") -> str:
    if fallback:
        return fallback
    if spec.get("probe_name"):
        return str(spec["probe_name"])
    for name, candidate in DEFAULT_CONTRACT_PROBES.items():
        keys = ("provider", "job_type", "gpu_profile", "expected_model", "image_contract_id")
        if all(candidate.get(key) == spec.get(key) for key in keys if key in candidate or key in spec):
            return name
    return f"{spec.get('provider')}.{spec.get('job_type')}.{int(time.time())}"


def _enrich_probe_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Attach provider-distributed image facts without changing the logical worker contract."""
    provider = str(spec.get("provider") or "")
    gpu_profile = str(spec.get("gpu_profile") or "")
    if not provider or not gpu_profile:
        return spec
    try:
        runtime = dict((load_requirement_registry().get("provider_runtimes") or {}).get(f"{provider}:{gpu_profile}") or {})
        contract_id = str(spec.get("image_contract_id") or runtime.get("image_contract_id") or "")
        contract = dict((load_image_contract_registry().get("image_contracts") or {}).get(contract_id) or {})
        distribution = dict((contract.get("provider_images") or {}).get(provider) or {})
    except Exception:
        return spec
    provider_image = str(distribution.get("image") or "")
    if not provider_image:
        return spec
    logical_image = str(spec.get("expected_image") or contract.get("image") or runtime.get("worker_image") or "")
    image_name, digest = _split_image_digest(provider_image)
    accepted = [
        item for item in (logical_image, provider_image, image_name, _image_basename(logical_image), _image_basename(image_name)) if item
    ]
    spec["logical_image"] = logical_image
    spec["provider_image"] = provider_image
    spec["accepted_images"] = sorted(set(accepted))
    if digest and not spec.get("expected_image_digest"):
        spec["expected_image_digest"] = digest
    return spec


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _artifact_text(path: Path) -> str:
    parts: list[str] = []
    for name in ("stdout.log", "stderr.log", "result.json", "metrics.json", "verify.json", "probe_info.json"):
        file = path / name
        if file.is_file():
            parts.append(file.read_text(errors="replace"))
    return "\n".join(parts)


def _first_string(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _observed_model(*payloads_and_text: Any) -> str | None:
    for payload in payloads_and_text[:-1]:
        if isinstance(payload, dict):
            value = _nested_first(payload, ("loaded_model_id", "served_model", "result_model", "model", "model_id", "observed_model"))
            if value:
                return value
    text = str(payloads_and_text[-1] or "")
    matches = MODEL_RE.findall(text)
    if matches:
        for match in matches:
            if "0.5B" not in match:
                return _normalize_model(match)
        return _normalize_model(matches[0])
    return None


def _normalize_model(value: str) -> str:
    return value if value.startswith("Qwen/") else f"Qwen/{value}" if value.startswith("Qwen") else value


def _observed_image(result: dict[str, Any], metrics: dict[str, Any], probe_info: dict[str, Any]) -> dict[str, Any]:
    name = _first_string(
        _nested_first(probe_info, ("provider_image", "image", "image_name", "worker_image")),
        _nested_first(metrics, ("provider_image", "image", "image_name", "worker_image")),
        _nested_first(result, ("provider_image", "image", "image_name", "worker_image")),
    )
    digest = _first_string(
        _nested_first(probe_info, ("provider_image_digest", "image_digest", "digest")),
        _nested_first(metrics, ("provider_image_digest", "image_digest", "digest")),
        _nested_first(result, ("provider_image_digest", "image_digest", "digest")),
    )
    return {"name": name or None, "digest": digest or None}


def _hardware_summary(metrics: dict[str, Any], probe_info: dict[str, Any]) -> dict[str, Any]:
    return {
        "gpu_name": _first_string(_nested_first(probe_info, ("gpu_name",)), _nested_first(metrics, ("gpu_name",))),
        "gpu_count": _nested_number(probe_info, ("gpu_count",)) or _nested_number(metrics, ("gpu_count",)),
        "vram_mb": _nested_number(probe_info, ("vram_mb", "gpu_memory_mb", "vram_used_mb"))
        or _nested_number(metrics, ("vram_mb", "gpu_memory_mb", "vram_used_mb")),
    }


def _cache_summary(result: dict[str, Any], metrics: dict[str, Any], probe_info: dict[str, Any], text: str) -> dict[str, Any]:
    cache_hit = _nested_bool(probe_info, ("cache_hit", "cache_warm"))
    if cache_hit is None:
        cache_hit = _nested_bool(metrics, ("cache_hit", "cache_warm"))
    cold_signals = []
    lowered = text.lower()
    if FETCHING_RE.search(text):
        cold_signals.append("hf_fetching_files")
    if "snapshot_download" in lowered or "huggingface" in lowered and "download" in lowered:
        cold_signals.append("hf_download")
    if "download timeout" in lowered or "read timed out" in lowered:
        cold_signals.append("download_timeout")
    return {
        "cache_hit": cache_hit,
        "cold_start_observed": bool(cold_signals),
        "cold_start_signals": cold_signals,
        "download_seconds": _nested_number(metrics, ("hf_download_seconds", "download_seconds"))
        or _nested_number(result, ("hf_download_seconds", "download_seconds")),
    }


def _workspace_contract_summary(result: dict[str, Any], metrics: dict[str, Any], probe_info: dict[str, Any]) -> dict[str, Any]:
    explicit_ok = _nested_bool(probe_info, ("workspace_contract_ok",))
    if explicit_ok is None:
        explicit_ok = _nested_bool(metrics, ("workspace_contract_ok",))
    if explicit_ok is None:
        explicit_ok = _nested_bool(result, ("workspace_contract_ok",))
    cleanup = (
        _nested_dict(probe_info, ("cleanup", "cleanup_status"))
        or _nested_dict(metrics, ("cleanup", "cleanup_status"))
        or _nested_dict(result, ("cleanup", "cleanup_status"))
    )
    actual_cost_guard = (
        _nested_dict(probe_info, ("actual_cost_guard",))
        or _nested_dict(metrics, ("actual_cost_guard",))
        or _nested_dict(result, ("actual_cost_guard",))
    )
    volume_probe = (
        _nested_dict(probe_info, ("volume_probe",)) or _nested_dict(metrics, ("volume_probe",)) or _nested_dict(result, ("volume_probe",))
    )
    runtime_checks = (
        _nested_dict(probe_info, ("runtime_checks", "checks"))
        or _nested_dict(metrics, ("runtime_checks", "checks"))
        or _nested_dict(result, ("runtime_checks", "checks"))
    )
    gpu_probe = _nested_dict(probe_info, ("gpu_probe",)) or _nested_dict(metrics, ("gpu_probe",)) or _nested_dict(result, ("gpu_probe",))
    return {
        "ok": explicit_ok,
        "hf_token_present": _first_bool(result, metrics, probe_info, keys=("hf_token_present",)),
        "image_contract_marker_present": _first_bool(result, metrics, probe_info, keys=("image_contract_marker_present",)),
        "runtime_imports_ok": _runtime_imports_ok(runtime_checks),
        "cache_hit": _first_bool(result, metrics, probe_info, keys=("cache_hit", "cache_warm")),
        "volume_required": _first_bool(result, metrics, probe_info, keys=("volume_required",)),
        "volume_probe_ok": bool(volume_probe.get("ok")) if isinstance(volume_probe, dict) and "ok" in volume_probe else None,
        "worker_startup_ok": _first_bool(
            result,
            metrics,
            probe_info,
            keys=("worker_startup_ok", "observed_http_worker", "asr_diarization_runtime_ok"),
        ),
        "cleanup_ok": bool(cleanup.get("ok"))
        if isinstance(cleanup, dict) and "ok" in cleanup
        else _first_bool(result, metrics, probe_info, keys=("cleanup_ok",)),
        "cleanup": cleanup,
        "cost_guard_ok": bool(actual_cost_guard.get("ok"))
        if isinstance(actual_cost_guard, dict) and "ok" in actual_cost_guard
        else _first_bool(result, metrics, probe_info, keys=("cost_guard_ok",)),
        "actual_cost_guard": actual_cost_guard,
        "gpu_probe": gpu_probe,
        "pod_id": _first_string(
            _nested_first(probe_info, ("pod_id",)),
            _nested_first(metrics, ("pod_id",)),
            _nested_first(result, ("pod_id",)),
        ),
        "instance_id": _first_string(
            _nested_first(probe_info, ("instance_id",)),
            _nested_first(metrics, ("instance_id",)),
            _nested_first(result, ("instance_id",)),
        ),
        "provider_job_id": _first_string(
            _nested_first(probe_info, ("provider_job_id",)),
            _nested_first(metrics, ("provider_job_id",)),
            _nested_first(result, ("provider_job_id",)),
        ),
    }


def workspace_observation_coverage(
    provider: str, observed: dict[str, Any], artifact_verify: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Normalize provider-specific workspace evidence into the public 12-category canary contract."""
    artifact_verify = artifact_verify or {}
    workspace = observed.get("workspace_contract") if isinstance(observed.get("workspace_contract"), dict) else {}
    cache = observed.get("cache") if isinstance(observed.get("cache"), dict) else {}
    hardware = observed.get("hardware") if isinstance(observed.get("hardware"), dict) else {}
    image = observed.get("image") if isinstance(observed.get("image"), dict) else {}
    gpu_evidence = observed.get("gpu_utilization_evidence") if isinstance(observed.get("gpu_utilization_evidence"), dict) else {}
    runtime_imports_ok = _bool_or_none(workspace.get("runtime_imports_ok"))
    cache_hit = _bool_or_none(workspace.get("cache_hit"))
    if cache_hit is None:
        cache_hit = _bool_or_none(cache.get("cache_hit"))
    cleanup_ok = _bool_or_none(workspace.get("cleanup_ok"))
    cost_guard_ok = _bool_or_none(workspace.get("cost_guard_ok"))
    startup_ok = _bool_or_none(workspace.get("worker_startup_ok"))
    workspace_ok = _bool_or_none(workspace.get("ok"))
    resource_id = _first_string(
        str(workspace.get("pod_id") or ""),
        str(workspace.get("instance_id") or ""),
        str(workspace.get("provider_job_id") or ""),
    )
    image_observed = bool(
        image.get("name")
        or image.get("digest")
        or workspace.get("image_contract_marker_present") is not None
        or workspace.get("provider_image")
    )
    gpu_probe = workspace.get("gpu_probe") if isinstance(workspace.get("gpu_probe"), dict) else {}
    gpu_ok = _gpu_probe_ok(gpu_probe)
    if gpu_ok is None and "ok" in gpu_evidence:
        gpu_ok = bool(gpu_evidence.get("ok"))
    if gpu_ok is None and (hardware.get("gpu_name") or hardware.get("gpu_count")):
        gpu_ok = True
    coverage = {
        "provider_resource_identity": _coverage_entry(
            bool(resource_id), bool(resource_id) if resource_id else None, ["pod_id", "instance_id", "provider_job_id"]
        ),
        "image_contract": _coverage_entry(
            image_observed,
            _coverage_bool(workspace.get("image_contract_marker_present"), default=bool(image.get("name") or image.get("digest"))),
            ["image", "provider_image", "image_contract_marker_present"],
        ),
        "secret_availability": _coverage_entry(
            workspace.get("hf_token_present") is not None,
            _bool_or_none(workspace.get("hf_token_present")),
            ["hf_token_present"],
        ),
        "workspace_cache": _coverage_entry(cache_hit is not None, cache_hit, ["cache_hit", "cache_warm"]),
        "startup_phases": _coverage_entry(
            startup_ok is not None or workspace_ok is not None,
            startup_ok if startup_ok is not None else workspace_ok,
            ["worker_startup_ok", "workspace_contract_ok"],
        ),
        "queue_or_reservation": _coverage_entry(
            bool(resource_id), bool(resource_id) if resource_id else None, ["pod_id", "instance_id", "provider_job_id"]
        ),
        "model_load": _coverage_entry(
            bool(observed.get("model")) or runtime_imports_ok is not None,
            runtime_imports_ok if runtime_imports_ok is not None else bool(observed.get("model")) if observed.get("model") else None,
            ["model", "loaded_model_id", "runtime_imports_ok"],
        ),
        "gpu_execution": _coverage_entry(
            gpu_ok is not None,
            gpu_ok,
            ["gpu_probe", "gpu_utilization_evidence", "hardware"],
        ),
        "artifact_contract": _coverage_entry(
            bool(observed.get("artifact_contract")),
            bool(artifact_verify.get("ok")) if "ok" in artifact_verify else None,
            ["artifact_contract", "verify_artifacts"],
        ),
        "cost_guard": _coverage_entry(
            cost_guard_ok is not None or bool(workspace.get("actual_cost_guard")),
            cost_guard_ok,
            ["actual_cost_guard", "cost_guard_ok"],
        ),
        "cleanup_result": _coverage_entry(
            cleanup_ok is not None or bool(workspace.get("cleanup")),
            cleanup_ok,
            ["cleanup", "cleanup_ok"],
        ),
        "provider_residue": _coverage_entry(
            cleanup_ok is not None or bool(workspace.get("cleanup")),
            cleanup_ok,
            ["cleanup", "cleanup_ok"],
        ),
    }
    return {
        "coverage_version": "gpu-job-workspace-observation-coverage-v1",
        "provider": provider,
        "categories": coverage,
        "observed_categories": [name for name, row in coverage.items() if row["observed"]],
        "missing_categories": [name for name, row in coverage.items() if not row["observed"]],
        "failed_categories": [name for name, row in coverage.items() if row["ok"] is False],
    }


def _coverage_entry(observed: bool, ok: bool | None, evidence_fields: list[str]) -> dict[str, Any]:
    return {
        "observed": bool(observed),
        "ok": ok if observed else None,
        "evidence_fields": evidence_fields,
    }


def _coverage_bool(value: Any, *, default: bool | None = None) -> bool | None:
    boolean = _bool_or_none(value)
    if boolean is not None:
        return boolean
    return default


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _gpu_probe_ok(probe: dict[str, Any]) -> bool | None:
    if not probe:
        return None
    if isinstance(probe.get("ok"), bool):
        return bool(probe.get("ok"))
    if isinstance(probe.get("exit_code"), int):
        return int(probe.get("exit_code")) == 0
    stdout = str(probe.get("stdout") or "")
    if "nvidia" in stdout.lower() or "cuda" in stdout.lower():
        return True
    return None


def _checks(
    spec: dict[str, Any],
    observed: dict[str, Any],
    artifact_verify: dict[str, Any],
    result: dict[str, Any],
    verify_payload: dict[str, Any],
) -> dict[str, bool]:
    expected_model = str(spec.get("expected_model") or "")
    expected_image = str(spec.get("expected_image") or "")
    expected_digest = str(spec.get("expected_image_digest") or "")
    accepted_images = [str(item) for item in spec.get("accepted_images") or [] if str(item or "").strip()]
    forbidden_models = {str(item).lower() for item in spec.get("forbidden_models") or []}
    observed_model = str(observed.get("model") or "")
    observed_image = observed.get("image") if isinstance(observed.get("image"), dict) else {}
    observed_image_name = str(observed_image.get("name") or "")
    observed_image_digest = str(observed_image.get("digest") or "")
    text = result.get("text")
    if text is None and isinstance(result.get("answer"), str):
        text = result.get("answer")
    return {
        "artifact_contract_ok": bool(artifact_verify.get("ok")),
        "verify_ok": bool(verify_payload.get("ok", artifact_verify.get("ok"))),
        "text_nonempty": spec.get("job_type") not in {"llm_heavy", "vlm_ocr", "asr"} or isinstance(text, str) and bool(text.strip()),
        "model_match": not expected_model or observed_model == expected_model,
        "forbidden_model_absent": not observed_model or observed_model.lower() not in forbidden_models,
        "image_match": _image_matches(observed_image_name, expected_image=expected_image, accepted_images=accepted_images),
        "image_digest_match": not expected_digest or observed_image_digest == expected_digest,
        "gpu_contract_ok": not spec.get("require_gpu_utilization") or bool(observed["gpu_utilization_evidence"].get("ok")),
        "cache_contract_ok": not spec.get("cache_required") or observed["cache"].get("cache_hit") is True,
        "workspace_contract_ok": not spec.get("workspace_contract_required") or observed["workspace_contract"].get("ok") is True,
    }


def _failure(
    provider: str,
    spec: dict[str, Any],
    checks: dict[str, bool],
    observed: dict[str, Any],
    text: str,
    artifact_verify: dict[str, Any],
) -> dict[str, Any]:
    if all(checks.values()):
        return {"class": None, "retryable": False, "reason": ""}
    native = classify_error(text, provider=provider)
    klass = str(native.get("class") or "unknown")
    retryable = bool(native.get("retryable"))
    reason = str(native.get("reason") or "")

    if klass == "unknown":
        if not checks["artifact_contract_ok"]:
            klass, retryable, reason = "artifact_contract_failure", False, "artifact verification failed"
        if not checks["verify_ok"]:
            klass, retryable, reason = "verification_failed", False, "verify.json reports failure"
        if not checks["text_nonempty"]:
            klass, retryable, reason = "empty_output_success", False, "provider completed but output text is empty"
    if not checks["model_match"] or not checks["forbidden_model_absent"]:
        klass, retryable, reason = "model_contract_mismatch", False, "observed model does not satisfy provider contract"
    if not checks["image_match"] or not checks["image_digest_match"]:
        klass, retryable, reason = "image_contract_mismatch", False, "observed image does not satisfy provider contract"
    if "gptqmodel" in text.lower():
        klass, retryable, reason = "image_missing_dependency", False, "provider image missing quantization dependency"
    if not checks["workspace_contract_ok"]:
        klass, retryable, reason = "workspace_contract_missing", False, "workspace contract evidence missing or failed"
    if not checks["cache_contract_ok"]:
        klass, retryable, reason = "cache_contract_missing", True, "cache contract missing or cold model download observed"
    if "timed out" in text.lower() and observed.get("cache", {}).get("cold_start_observed"):
        klass, retryable, reason = "cold_start_timeout", True, "model cold start or download timed out"
    if klass == "unknown" and not checks["gpu_contract_ok"]:
        klass, retryable, reason = "gpu_contract_mismatch", False, "GPU utilization evidence missing"
    return {
        "class": klass,
        "retryable": retryable,
        "reason": reason,
        "provider_message": _snippet(text),
        "artifact_verify": artifact_verify,
    }


def _canary_job(spec: dict[str, Any]) -> Job:
    provider = str(spec["provider"])
    job_type = str(spec["job_type"])
    gpu_profile = str(spec["gpu_profile"])
    model = str(spec.get("expected_model") or "")
    job_id = make_job_id(f"contract-probe-{job_type}")
    metadata: dict[str, Any] = {
        "source_system": "contract-probe",
        "contract_probe": {
            "contract_probe_version": CONTRACT_PROBE_VERSION,
            "probe_name": _probe_name(spec),
            "provider": provider,
        },
        "input": {
            "prompt": "Return exactly: GPU_JOB_CONTRACT_PROBE_OK",
            "max_tokens": 32,
        },
        "routing": {
            "quality_requires_gpu": True,
            "estimated_gpu_runtime_seconds": 120,
        },
        "model_requirements": {
            job_type: True,
            "min_quality_tier": "external_gpu",
        },
        "hardware_verification": {
            "require_gpu_utilization": bool(spec.get("require_gpu_utilization", False)),
        },
    }
    limits = {"max_runtime_minutes": 30, "max_cost_usd": 3.0, "max_startup_seconds": 900}
    if job_type == "asr" and gpu_profile == "asr_diarization":
        metadata["input"] = {
            "language": "ja",
            "model": "large-v3",
            "diarize": True,
            "speaker_diarization": True,
            "speaker_model": "pyannote/speaker-diarization-3.1",
        }
        metadata["model_requirements"] = {
            "asr": True,
            "speaker_diarization": True,
            "min_quality_tier": "external_gpu",
        }
        metadata["secret_refs"] = ["hf_token"]
        model = "large-v3"
        if provider == "runpod":
            runpod_image = str(spec.get("provider_image") or spec.get("expected_image") or "")
            metadata.update(
                {
                    "runpod_execution_mode": "pod_http",
                    "runpod_pod_image": runpod_image,
                    "runpod_gpu_type_id": "NVIDIA GeForce RTX 3090",
                    "runpod_cloud_type": "ALL",
                    "runpod_gpu_count": 1,
                    "runpod_volume_in_gb": 0,
                    "runpod_container_disk_in_gb": 80,
                    "runpod_min_vcpu_count": 4,
                    "runpod_min_memory_in_gb": 16,
                    "runpod_hf_secret_name": "gpu_job_hf_read",
                    "max_uptime_seconds": 600,
                    "max_estimated_cost_usd": 0.15,
                }
            )
            limits = {"max_runtime_minutes": 10, "max_cost_usd": 0.15, "max_startup_seconds": 600}
    if provider == "runpod" and "pod_http" in _probe_name(spec):
        metadata["runpod_execution_mode"] = "pod_http"
        limits = {"max_runtime_minutes": 10, "max_cost_usd": 0.25, "max_startup_seconds": 300}
    if provider == "vast" and not (job_type == "asr" and gpu_profile == "asr_diarization"):
        metadata["allow_vast_direct_instance_smoke"] = True
        metadata["min_vram_gb"] = 16
        metadata["min_compute_cap"] = 750
        job_type = "smoke"
        limits = {"max_runtime_minutes": 3, "max_cost_usd": 0.25, "max_startup_seconds": 90}
    return Job.from_dict(
        {
            "job_id": job_id,
            "job_type": job_type,
            "input_uri": "text://GPU_JOB_CONTRACT_PROBE_OK",
            "output_uri": f"local://contract-probes/{job_id}",
            "worker_image": str(spec.get("expected_image") or "auto"),
            "gpu_profile": gpu_profile,
            "model": model,
            "provider": provider,
            "limits": limits,
            "metadata": metadata,
        }
    )


def _image_matches(observed: str, *, expected_image: str, accepted_images: list[str]) -> bool:
    if not expected_image and not accepted_images:
        return True
    candidates = [item for item in [expected_image, *accepted_images] if item]
    observed_values = {observed, _strip_digest(observed), _image_basename(observed), _image_basename(_strip_digest(observed))}
    for candidate in candidates:
        candidate_values = {candidate, _strip_digest(candidate), _image_basename(candidate), _image_basename(_strip_digest(candidate))}
        if any(value and value in observed for value in candidate_values):
            return True
        if observed_values & candidate_values:
            return True
    return False


def _split_image_digest(image: str) -> tuple[str, str]:
    if "@sha256:" not in image:
        return image, ""
    name, digest = image.rsplit("@", 1)
    return name, digest


def _strip_digest(image: str) -> str:
    return _split_image_digest(image)[0]


def _image_basename(image: str) -> str:
    return _strip_digest(image).rsplit("/", 1)[-1]


def _nested_first(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    stack = [data]
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        stack.extend(value for value in item.values() if isinstance(value, dict))
    return ""


def _nested_number(data: dict[str, Any], keys: tuple[str, ...]) -> float | int | None:
    stack = [data]
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = item.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return value
        stack.extend(value for value in item.values() if isinstance(value, dict))
    return None


def _nested_dict(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    stack = [data]
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = item.get(key)
            if isinstance(value, dict):
                return value
        stack.extend(value for value in item.values() if isinstance(value, dict))
    return {}


def _nested_bool(data: dict[str, Any], keys: tuple[str, ...]) -> bool | None:
    stack = [data]
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = item.get(key)
            if isinstance(value, bool):
                return value
        stack.extend(value for value in item.values() if isinstance(value, dict))
    return None


def _first_bool(*payloads: dict[str, Any], keys: tuple[str, ...]) -> bool | None:
    for payload in payloads:
        value = _nested_bool(payload, keys)
        if value is not None:
            return value
    return None


def _runtime_imports_ok(runtime_checks: dict[str, Any]) -> bool | None:
    keys = ("faster_whisper_import", "pyannote_import", "matplotlib_import")
    values = [runtime_checks.get(key) for key in keys]
    if all(isinstance(value, bool) for value in values):
        return all(bool(value) for value in values)
    return None


def _snippet(text: str, limit: int = 500) -> str:
    compact = " ".join(str(text or "").split())
    return compact[:limit]
