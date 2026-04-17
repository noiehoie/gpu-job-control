from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from tempfile import TemporaryDirectory
import os
import unittest

from gpu_job.cli import main


class ConfigCliTest(unittest.TestCase):
    def test_config_init_creates_user_config_without_overwrite(self) -> None:
        old_xdg = os.environ.get("XDG_CONFIG_HOME")
        with TemporaryDirectory() as tmp:
            os.environ["XDG_CONFIG_HOME"] = tmp
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["config", "init"]), 0)
            config_dir = os.path.join(tmp, "gpu-job-control")
            policy_path = os.path.join(config_dir, "execution-policy.json")
            profiles_path = os.path.join(config_dir, "gpu-profiles.json")
            capabilities_path = os.path.join(config_dir, "model-capabilities.json")
            self.assertTrue(os.path.exists(policy_path))
            self.assertTrue(os.path.exists(profiles_path))
            self.assertTrue(os.path.exists(capabilities_path))

            with open(policy_path, "w", encoding="utf-8") as handle:
                handle.write("{}")
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["config", "init"]), 0)
            with open(policy_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "{}")
        if old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = old_xdg


if __name__ == "__main__":
    unittest.main()
