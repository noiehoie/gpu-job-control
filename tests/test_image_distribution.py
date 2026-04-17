from __future__ import annotations

import unittest

from gpu_job.image import image_mirror, image_mirror_plan


class ImageDistributionTest(unittest.TestCase):
    def test_mirror_plan_uses_remote_builder_when_given(self) -> None:
        result = image_mirror_plan(
            "ghcr.io/example/gpu-job@sha256:abc",
            "registry.example.com/gpu-job@sha256:abc",
            builder="gpu-builder",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["builder"], "gpu-builder")
        self.assertEqual(result["command"][0], "ssh")
        self.assertIn("operator-controlled registry", result["runtime_policy"])

    def test_mirror_without_execute_is_plan_only(self) -> None:
        result = image_mirror(
            "ghcr.io/example/gpu-job@sha256:abc",
            "registry.example.com/gpu-job@sha256:abc",
            builder="gpu-builder",
            execute=False,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["planned"])


if __name__ == "__main__":
    unittest.main()
