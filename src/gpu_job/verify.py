from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .manifest import verify_manifest


DEFAULT_REQUIRED = ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"]
GPU_UTILIZATION_KEYS = {
    "gpu_utilization_percent",
    "gpu_utilization",
    "gpu_memory_used_mb",
    "gpu_memory_mb",
    "vram_used_mb",
    "cuda_memory_allocated_mb",
}


def artifact_stats(path: Path) -> tuple[int, int]:
    files = [p for p in path.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def _positive_number(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _gpu_utilization_matches(value: Any, *, path: str = "") -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            next_path = f"{path}.{key}" if path else str(key)
            if key in GPU_UTILIZATION_KEYS and _positive_number(item):
                matches.append({"path": next_path, "key": key, "value": item})
            matches.extend(_gpu_utilization_matches(item, path=next_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            matches.extend(_gpu_utilization_matches(item, path=f"{path}[{index}]"))
    return matches


def _contains_gpu_utilization(value: Any) -> bool:
    return bool(_gpu_utilization_matches(value))


def collect_hardware_utilization_evidence(metrics_path: Path, execution_class: str = "gpu") -> dict[str, Any]:
    if not metrics_path.is_file():
        return {"ok": False, "execution_class": execution_class, "reason": "metrics.json missing", "matches": []}
    try:
        metrics = json.loads(metrics_path.read_text())
    except json.JSONDecodeError:
        return {"ok": False, "execution_class": execution_class, "reason": "metrics.json invalid json", "matches": []}
    matches = _gpu_utilization_matches(metrics)
    return {
        "ok": bool(matches),
        "execution_class": execution_class,
        "reason": "gpu utilization evidence present" if matches else "gpu utilization evidence missing",
        "matches": matches,
    }


def assert_hardware_utilization(metrics_path: Path, execution_class: str = "gpu") -> dict[str, Any]:
    evidence = collect_hardware_utilization_evidence(metrics_path, execution_class=execution_class)
    return {key: value for key, value in evidence.items() if key != "matches"}


def verify_artifacts(
    path: Path,
    required: list[str] | None = None,
    *,
    require_manifest: bool = False,
    require_gpu_utilization: bool = False,
    execution_class: str = "gpu",
) -> dict[str, Any]:
    required = required or DEFAULT_REQUIRED
    missing = [name for name in required if not (path / name).is_file()]
    count, bytes_total = artifact_stats(path)
    parsed_json: dict[str, bool] = {}
    for name in required:
        if name.endswith(".json") and (path / name).is_file():
            try:
                json.loads((path / name).read_text())
                parsed_json[name] = True
            except json.JSONDecodeError:
                parsed_json[name] = False
    verify_payload_ok = True
    application_verify: dict[str, Any] | None = None
    if (path / "verify.json").is_file() and parsed_json.get("verify.json"):
        verify_payload = json.loads((path / "verify.json").read_text())
        if isinstance(verify_payload, dict) and "ok" in verify_payload:
            verify_payload_ok = bool(verify_payload.get("ok"))
            nested_application_verify = verify_payload.get("application_verify")
            application_verify = nested_application_verify if isinstance(nested_application_verify, dict) else verify_payload
        else:
            verify_payload_ok = False
    ok = not missing and all(parsed_json.values()) and verify_payload_ok
    manifest = verify_manifest(path)
    ok = ok and bool(manifest.get("ok")) and (not require_manifest or bool(manifest.get("manifest_present")))
    hardware_verify = None
    if require_gpu_utilization:
        hardware_verify = assert_hardware_utilization(path / "metrics.json", execution_class=execution_class)
        ok = ok and bool(hardware_verify.get("ok"))
    return {
        "ok": ok,
        "artifact_dir": str(path),
        "required": required,
        "missing": missing,
        "artifact_count": count,
        "artifact_bytes": bytes_total,
        "json_valid": parsed_json,
        "application_verify": application_verify,
        "manifest": manifest,
        "require_manifest": require_manifest,
        "hardware_verify": hardware_verify,
        "require_gpu_utilization": require_gpu_utilization,
    }


def application_verify_payload(job_type: str, result: dict[str, Any], *, error: str = "") -> dict[str, Any]:
    checks: dict[str, bool] = {
        "result_is_object": isinstance(result, dict),
        "no_error": not bool(error or result.get("error")),
    }
    if job_type == "embedding":
        items = result.get("items")
        count = result.get("count")
        dimensions = result.get("dimensions")
        checks["items_nonempty"] = isinstance(items, list) and bool(items)
        checks["count_matches_items"] = isinstance(items, list) and count == len(items)
        checks["dimensions_positive"] = isinstance(dimensions, int) and dimensions > 0
    elif job_type in {"llm_heavy", "vlm_ocr", "pdf_ocr"}:
        text = result.get("text")
        if text is None and isinstance(result.get("answer"), str):
            text = result.get("answer")
        checks["text_nonempty"] = isinstance(text, str) and bool(text.strip())
    elif "ok" in result:
        checks["provider_ok"] = bool(result.get("ok"))
    return {
        "ok": all(checks.values()),
        "checks": checks,
    }
