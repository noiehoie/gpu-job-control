from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .manifest import verify_manifest


DEFAULT_REQUIRED = ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"]


def artifact_stats(path: Path) -> tuple[int, int]:
    files = [p for p in path.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def verify_artifacts(path: Path, required: list[str] | None = None, *, require_manifest: bool = False) -> dict[str, Any]:
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
