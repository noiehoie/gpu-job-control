from __future__ import annotations

import unittest
from unittest.mock import patch

from gpu_job.guard import collect_cost_guard


class GuardScopeTest(unittest.TestCase):
    def test_remote_provider_guard_skips_local_resource_guard(self) -> None:
        with (
            patch("gpu_job.guard.collect_resource_guard", side_effect=AssertionError("resource guard should be skipped")),
            patch("gpu_job.guard.PROVIDERS", {"modal": _ProviderGuard({"ok": True, "estimated_hourly_usd": 0.0})}),
        ):
            result = collect_cost_guard(["modal"])

        self.assertTrue(result["ok"])
        self.assertTrue(result["resource"]["skipped"])

    def test_local_provider_guard_uses_local_resource_guard(self) -> None:
        with (
            patch("gpu_job.guard.collect_resource_guard", return_value={"ok": False, "reason": "disk full"}),
            patch("gpu_job.guard.PROVIDERS", {"ollama": _ProviderGuard({"ok": True, "estimated_hourly_usd": 0.0})}),
        ):
            result = collect_cost_guard(["ollama"])

        self.assertFalse(result["ok"])
        self.assertEqual(result["resource"]["reason"], "disk full")


class _ProviderGuard:
    def __init__(self, result: dict) -> None:
        self._result = result

    def cost_guard(self) -> dict:
        return dict(self._result)


if __name__ == "__main__":
    unittest.main()
