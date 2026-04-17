from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .manifest import verify_manifest


DEFAULT_REQUIRED = ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"]


def artifact_stats(path: Path) -> tuple[int, int]:
    files = [p for p in path.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def verify_artifacts(path: Path, required: list[str] | None = None) -> dict[str, Any]:
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
    ok = not missing and all(parsed_json.values())
    manifest = verify_manifest(path)
    ok = ok and bool(manifest.get("ok"))
    return {
        "ok": ok,
        "artifact_dir": str(path),
        "required": required,
        "missing": missing,
        "artifact_count": count,
        "artifact_bytes": bytes_total,
        "json_valid": parsed_json,
        "manifest": manifest,
    }
