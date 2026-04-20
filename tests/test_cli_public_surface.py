from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

from gpu_job import cli_public


def _subcommands(parser: argparse.ArgumentParser) -> set[str]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def _subparser(parser: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices[name]
    raise AssertionError(f"missing subparser: {name}")


class PublicCLISurfaceTest(unittest.TestCase):
    def test_project_entry_points_split_public_and_admin_surfaces(self) -> None:
        pyproject = tomllib.loads(Path("pyproject.toml").read_text())
        scripts = pyproject["project"]["scripts"]
        self.assertEqual(scripts["gpu-job"], "gpu_job.cli_public:main")
        self.assertEqual(scripts["gpu-job-admin"], "gpu_job.cli:main")

    def test_public_cli_excludes_active_provider_commands(self) -> None:
        commands = _subcommands(cli_public.build_parser())
        forbidden = {
            "submit",
            "enqueue",
            "intake",
            "cancel",
            "retry",
            "replan",
            "worker",
            "offers",
            "signals",
            "stability",
            "guard",
            "orphan-inventory",
            "orphan-reaper",
            "reconcile",
            "vast-orphan-inventory",
            "vast-orphan-reaper",
            "runpod",
            "serve",
        }
        self.assertFalse(commands & forbidden)

    def test_contract_probe_public_surface_is_non_executing(self) -> None:
        parser = cli_public.build_parser()
        contract_probe = _subparser(parser, "contract-probe")
        subcommands = _subcommands(contract_probe)
        self.assertEqual(subcommands, {"list", "schema", "summary", "plan", "parse"})
        with self.assertRaises(SystemExit):
            parser.parse_args(["contract-probe", "run", "--provider", "runpod"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["contract-probe", "parse", "artifact", "--provider", "runpod", "--append"])

    def test_image_public_surface_is_non_building(self) -> None:
        parser = cli_public.build_parser()
        image = _subparser(parser, "image")
        self.assertEqual(_subcommands(image), {"plan", "check", "contract-plan", "contract-check"})
        for command in ("build", "mirror", "contract-build", "contract-probe"):
            with self.assertRaises(SystemExit):
                parser.parse_args(["image", command])

    def test_workflow_public_surface_is_non_executing(self) -> None:
        parser = cli_public.build_parser()
        workflow = _subparser(parser, "workflow")
        self.assertEqual(_subcommands(workflow), {"list", "status", "plan"})
        for command in ("validate", "execute", "bulk", "approve", "drain"):
            with self.assertRaises(SystemExit):
                parser.parse_args(["workflow", command])

    def test_decision_public_surface_is_read_only_no_replay(self) -> None:
        parser = cli_public.build_parser()
        parser.parse_args(["decision", "job-1"])
        for args in (["decision", "job-1", "--replay"], ["decision", "--replay-all"]):
            with self.assertRaises(SystemExit):
                parser.parse_args(args)

    def test_capabilities_public_surface_is_registry_only(self) -> None:
        parser = cli_public.build_parser()
        capabilities = _subparser(parser, "capabilities")
        self.assertEqual(_subcommands(capabilities), {"list"})
        with self.assertRaises(SystemExit):
            parser.parse_args(["capabilities", "check", "job.json", "--provider", "runpod"])

    def test_public_cli_import_does_not_load_provider_package(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json, sys; import gpu_job.cli_public; "
                    "print(json.dumps(sorted(name for name in sys.modules "
                    "if name in {'gpu_job.providers.modal','gpu_job.providers.ollama',"
                    "'gpu_job.providers.runpod','gpu_job.providers.vast'})))"
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(proc.stdout), [])


if __name__ == "__main__":
    unittest.main()
