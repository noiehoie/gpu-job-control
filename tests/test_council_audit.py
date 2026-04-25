from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_council_audit import _coverage_errors, _load_records


def _record(phase: str, member: str) -> dict:
    return {
        "schema_version": "gpu-job-council-audit-v1",
        "task_id": "task-1",
        "phase": phase,
        "member": member,
        "utc_timestamp": "2026-04-23T00:00:00+00:00",
        "git_sha": "abc123",
        "prompt_sha256": "0" * 64,
        "exit_code": 0,
        "ok": True,
    }


class CouncilAuditTests(unittest.TestCase):
    def test_council_audit_loader_accepts_valid_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "docs" / "council-audit"
            log_dir.mkdir(parents=True)
            path = log_dir / "task-1.jsonl"
            path.write_text(json.dumps(_record("research", "gemini")) + "\n", encoding="utf-8")

            records, errors = _load_records(log_dir)

        self.assertEqual(errors, [])
        self.assertEqual(records[0]["phase"], "research")
        self.assertEqual(records[0]["member"], "gemini")

    def test_council_audit_loader_preserves_unsuccessful_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "docs" / "council-audit"
            log_dir.mkdir(parents=True)
            record = _record("audit", "composer2")
            record["ok"] = False
            record["exit_code"] = 1
            (log_dir / "task-1.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

            records, errors = _load_records(log_dir)

        self.assertEqual(errors, [])
        self.assertFalse(records[0]["ok"])

    def test_council_coverage_requires_every_phase_and_required_member(self) -> None:
        records = [_record(phase, member) for phase in ("research", "design", "code", "audit") for member in ("gemini", "composer2")]

        self.assertEqual(_coverage_errors(records, "task-1"), [])

    def test_council_coverage_reports_missing_member(self) -> None:
        records = [_record("research", "gemini")]

        errors = _coverage_errors(records, "task-1")

        self.assertIn("missing successful council record phase=research member=composer2", errors)
        self.assertIn("missing successful council record phase=audit member=gemini", errors)

    def test_council_coverage_allows_failure_followed_by_success(self) -> None:
        records = [_record(phase, member) for phase in ("research", "design", "code", "audit") for member in ("gemini", "composer2")]
        failed = _record("research", "composer2")
        failed["ok"] = False
        failed["exit_code"] = 124
        records.insert(0, failed)

        self.assertEqual(_coverage_errors(records, "task-1"), [])

    def test_council_coverage_rejects_latest_failure(self) -> None:
        records = [_record(phase, member) for phase in ("research", "design", "code", "audit") for member in ("gemini", "composer2")]
        failed = _record("research", "composer2")
        failed["ok"] = False
        failed["exit_code"] = 124
        records.append(failed)

        errors = _coverage_errors(records, "task-1")

        self.assertIn("latest council record unsuccessful phase=research member=composer2 exit_code=124", errors)


if __name__ == "__main__":
    unittest.main()
