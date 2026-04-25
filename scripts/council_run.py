from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "gpu-job-council-audit-v1"
VALID_PHASES = {"research", "design", "code", "audit"}
DEFAULT_LOG_DIR = Path("docs/council-audit")
MEMBER_COMMANDS = {
    "gemini": ["gemini", "-p"],
    "composer2": ["agent", "--print", "--mode", "ask", "--model", "composer-2", "--trust"],
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one external CLI council member and append a JSONL audit record.")
    parser.add_argument("--phase", required=True, choices=sorted(VALID_PHASES))
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--member", required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--allow-failure", action="store_true", help="Record a failed member without returning non-zero.")
    parser.add_argument("prompt", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    prompt = _prompt_text(args.prompt)
    repo_root = _repo_root()
    log_dir = repo_root / args.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{_safe_id(args.task_id)}.jsonl"

    command = _member_command(args.member, args.model, prompt, repo_root)
    started = time.monotonic()
    exit_code = 2
    timed_out = False
    stdout = ""
    stderr = ""
    error = ""
    try:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(1, args.timeout_seconds),
            check=False,
        )
        exit_code = int(completed.returncode)
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = 124
        stdout = _decode_timeout_stream(exc.stdout)
        stderr = _decode_timeout_stream(exc.stderr)
        error = f"timeout after {args.timeout_seconds}s"
    except FileNotFoundError as exc:
        exit_code = 127
        error = str(exc)
    except Exception as exc:  # pragma: no cover - defensive path
        exit_code = 2
        error = str(exc)

    duration_ms = int((time.monotonic() - started) * 1000)
    record = {
        "schema_version": SCHEMA_VERSION,
        "task_id": args.task_id,
        "phase": args.phase,
        "member": args.member,
        "model": args.model or _default_model(args.member),
        "utc_timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "git_sha": _git(["rev-parse", "HEAD"], repo_root),
        "git_status_short_hash": _hash_text(_git(["status", "--short"], repo_root)),
        "command": _redacted_command(command),
        "cwd": str(repo_root),
        "prompt_sha256": _hash_text(prompt),
        "prompt_preview": _truncate(prompt, 800),
        "artifact_paths": [str(item) for item in args.artifact],
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "ok": exit_code == 0 and not timed_out,
        "stdout_sha256": _hash_text(stdout),
        "stderr_sha256": _hash_text(stderr),
        "stdout_preview": _truncate(_mask(stdout), 4000),
        "stderr_preview": _truncate(_mask(stderr or error), 4000),
    }
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    sys.stdout.write(json.dumps({"ok": record["ok"], "log_path": str(log_path), "exit_code": exit_code}, ensure_ascii=False) + "\n")
    if record["ok"] or args.allow_failure:
        return 0
    return exit_code or 1


def _prompt_text(raw: list[str]) -> str:
    items = list(raw)
    if items and items[0] == "--":
        items = items[1:]
    if items:
        return " ".join(items)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("prompt is required after -- or on stdin")


def _repo_root() -> Path:
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], text=True, stdout=subprocess.PIPE, check=True)
    return Path(result.stdout.strip())


def _member_command(member: str, model: str, prompt: str, repo_root: Path) -> list[str]:
    if member in MEMBER_COMMANDS:
        base = list(MEMBER_COMMANDS[member])
        if member == "composer2":
            return base + ["--workspace", str(repo_root), prompt]
        return base + [prompt]
    if model:
        return ["agent", "--print", "--mode", "ask", "--model", model, "--trust", "--workspace", str(repo_root), prompt]
    raise SystemExit(f"unknown council member {member!r}; pass --model to use agent --model <model>")


def _default_model(member: str) -> str:
    if member == "gemini":
        return "gemini-cli-default"
    if member == "composer2":
        return "composer-2"
    return ""


def _git(args: list[str], cwd: Path) -> str:
    try:
        completed = subprocess.run(["git", *args], cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        return completed.stdout.strip()
    except Exception:
        return ""


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value).strip("-") or "council"


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"


def _mask(value: str) -> str:
    redacted = value
    for key, secret in os.environ.items():
        if not secret or len(secret) < 8:
            continue
        if any(token in key.upper() for token in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def _redacted_command(command: list[str]) -> list[str]:
    return [_truncate(_mask(part), 2000) for part in command]


def _decode_timeout_stream(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
