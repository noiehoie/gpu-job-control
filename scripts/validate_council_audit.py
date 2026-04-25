from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "gpu-job-council-audit-v1"
DEFAULT_LOG_DIR = Path("docs/council-audit")
REQUIRED_PHASES = ("research", "design", "code", "audit")
REQUIRED_MEMBERS = ("gemini", "composer2")
MATERIAL_PATH_PREFIXES = ("src/", "tests/", "scripts/", "config/", "schemas/", ".github/workflows/")
MATERIAL_EXACT_PATHS = ("AGENTS.md", "pyproject.toml", "uv.lock")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate external CLI council audit JSONL records.")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument(
        "--require",
        action="store_true",
        help="Require complete phase/member coverage even if no material diff is detected.",
    )
    parser.add_argument("--base-ref", default="")
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    log_dir = repo_root / args.log_dir
    all_records, errors = _load_records(log_dir)
    records = all_records
    if args.task_id:
        records = [record for record in records if record.get("task_id") == args.task_id]

    changed = _changed_paths(repo_root, args.base_ref)
    material = args.require or any(_is_material_path(path) for path in changed)
    if material:
        changed_audit_paths = _changed_audit_paths(repo_root, changed)
        if changed_audit_paths and not args.task_id:
            changed_records, changed_errors = _load_records_from_paths(changed_audit_paths)
            errors.extend(changed_errors)
            records = changed_records
        elif not args.task_id:
            errors.append("material diff requires a changed docs/council-audit/*.jsonl file")
        errors.extend(_coverage_errors(records, args.task_id))

    if errors:
        for error in errors:
            print(f"council-audit-error: {error}")
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "records": len(records),
                "total_records": len(all_records),
                "material_diff": material,
                "changed_paths": changed[:200],
                "task_id": args.task_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _repo_root() -> Path:
    completed = subprocess.run(["git", "rev-parse", "--show-toplevel"], text=True, stdout=subprocess.PIPE, check=True)
    return Path(completed.stdout.strip())


def _load_records(log_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not log_dir.exists():
        return [], []
    return _load_records_from_paths(sorted(log_dir.glob("*.jsonl")))


def _load_records_from_paths(paths: list[Path]) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in sorted(paths):
        if not path.exists():
            errors.append(f"{path}: council audit file does not exist")
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{path}:{lineno}: invalid JSONL: {exc}")
                continue
            missing = [
                key
                for key in (
                    "schema_version",
                    "task_id",
                    "phase",
                    "member",
                    "utc_timestamp",
                    "git_sha",
                    "prompt_sha256",
                    "exit_code",
                    "ok",
                )
                if key not in record
            ]
            if missing:
                errors.append(f"{path}:{lineno}: missing required fields: {', '.join(missing)}")
            if record.get("schema_version") != SCHEMA_VERSION:
                errors.append(f"{path}:{lineno}: unsupported schema_version {record.get('schema_version')!r}")
            records.append(record)
    return records, errors


def _coverage_errors(records: list[dict[str, Any]], task_id: str) -> list[str]:
    errors: list[str] = []
    if not records:
        suffix = f" for task_id={task_id}" if task_id else ""
        return [f"missing council audit records{suffix}"]
    for phase in REQUIRED_PHASES:
        for member in REQUIRED_MEMBERS:
            matches = [record for record in records if record.get("phase") == phase and record.get("member") == member]
            if not any(_record_success(record) for record in matches):
                errors.append(f"missing successful council record phase={phase} member={member}")
                continue
            latest = matches[-1]
            if not _record_success(latest):
                errors.append(f"latest council record unsuccessful phase={phase} member={member} exit_code={latest.get('exit_code')}")
    return errors


def _record_success(record: dict[str, Any]) -> bool:
    return record.get("ok") is True and record.get("exit_code") == 0


def _changed_paths(repo_root: Path, base_ref: str) -> list[str]:
    if base_ref:
        diff_args = ["diff", "--name-only", f"{base_ref}...HEAD"]
    else:
        merge_base = _git(["merge-base", "HEAD", "origin/main"], repo_root)
        if merge_base:
            diff_args = ["diff", "--name-only", f"{merge_base}...HEAD"]
        else:
            diff_args = ["diff", "--name-only", "HEAD^...HEAD"]
    output = _git(diff_args, repo_root)
    paths = [line.strip() for line in output.splitlines() if line.strip()]
    status = _git(["status", "--short"], repo_root)
    for line in status.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and path not in paths:
            paths.append(path)
    return paths


def _changed_audit_paths(repo_root: Path, changed: list[str]) -> list[Path]:
    paths: list[Path] = []
    prefix = f"{DEFAULT_LOG_DIR}/"
    for path in changed:
        if path.startswith(prefix) and path.endswith(".jsonl"):
            paths.append(repo_root / path)
        elif path.rstrip("/") == str(DEFAULT_LOG_DIR):
            paths.extend(sorted((repo_root / DEFAULT_LOG_DIR).glob("*.jsonl")))
    return paths


def _git(args: list[str], cwd: Path) -> str:
    completed = subprocess.run(["git", *args], cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _is_material_path(path: str) -> bool:
    return path in MATERIAL_EXACT_PATHS or path.startswith(MATERIAL_PATH_PREFIXES)


if __name__ == "__main__":
    raise SystemExit(main())
