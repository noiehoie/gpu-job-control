from __future__ import annotations

from gpu_job.readiness import launch_readiness
from gpu_job.selftest import run_selftest


def test_launch_readiness_can_skip_external_provider_checks() -> None:
    result = launch_readiness(limit=1, guard_provider_names=["local"], include_provider_stability=False)

    assert result["guard_summary"]["providers"].keys() == {"local"}
    assert result["provider_stability"]["ok"] is True
    assert result["provider_stability"]["skipped"] is True


def test_run_selftest_reports_all_expected_checks() -> None:
    result = run_selftest()

    assert result["ok"] is True
    names = {item["name"] for item in result["checks"]}
    assert names == {
        "route_selected",
        "local_submit",
        "artifact_manifest",
        "secret_gate_blocks",
        "wal_fail_closed",
        "readiness_detects_wal",
    }
