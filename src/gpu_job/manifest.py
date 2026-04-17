from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json


MANIFEST_VERSION = "gpu-job-artifact-manifest-v1"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(path: Path) -> dict[str, Any]:
    files = []
    if path.is_file():
        targets = [path]
        root = path.parent
    else:
        targets = sorted(p for p in path.rglob("*") if p.is_file())
        root = path
    for item in targets:
        stat = item.stat()
        files.append(
            {
                "path": str(item.relative_to(root)),
                "bytes": stat.st_size,
                "sha256": sha256_file(item),
            }
        )
    return {
        "manifest_version": MANIFEST_VERSION,
        "root": str(path),
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(int(item["bytes"]) for item in files),
    }


def write_manifest(path: Path, manifest_name: str = "manifest.json") -> Path:
    manifest = build_manifest(path)
    target = path / manifest_name if path.is_dir() else path.with_suffix(path.suffix + ".manifest.json")
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return target


def verify_manifest(path: Path, manifest_name: str = "manifest.json") -> dict[str, Any]:
    manifest_path = path / manifest_name
    if not manifest_path.is_file():
        return {"ok": True, "manifest_present": False, "missing": [], "mismatched": []}
    manifest = json.loads(manifest_path.read_text())
    missing = []
    mismatched = []
    for item in manifest.get("files", []):
        if item.get("path") == manifest_name:
            continue
        file_path = path / str(item["path"])
        if not file_path.is_file():
            missing.append(str(item["path"]))
            continue
        actual = sha256_file(file_path)
        if actual != item.get("sha256"):
            mismatched.append({"path": str(item["path"]), "expected": item.get("sha256"), "actual": actual})
    return {"ok": not missing and not mismatched, "manifest_present": True, "missing": missing, "mismatched": mismatched}
