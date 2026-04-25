from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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
        self.assertIn("caller", commands)

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

    def test_public_validate_accepts_caller_request_shape(self) -> None:
        payload = {
            "contract_version": "gpu-job-caller-request-v1",
            "operation": "llm.generate",
            "input": {"uri": "text://Return exactly: ok", "parameters": {"prompt": "Return exactly: ok"}},
            "output_expectation": {
                "target_uri": "local://cli-caller-validate",
                "required_files": ["result.json", "metrics.json", "verify.json", "stdout.log", "stderr.log"],
            },
            "limits": {"max_runtime_minutes": 5, "max_cost_usd": 1, "max_output_gb": 1},
            "idempotency": {"key": "cli-caller-validate-001"},
            "caller": {
                "system": "cli-test",
                "operation": "validate",
                "request_id": "cli-caller-validate-001",
                "version": "2026.04.25",
            },
        }
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "caller.json"
            path.write_text(json.dumps(payload))
            proc = subprocess.run(
                [sys.executable, "-m", "gpu_job.cli_public", "validate", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
        result = json.loads(proc.stdout)
        self.assertTrue(result["ok"])
        self.assertEqual(result["job"]["job_type"], "llm_heavy")

    def test_public_caller_surface_is_read_only_metadata(self) -> None:
        parser = cli_public.build_parser()
        caller = _subparser(parser, "caller")
        self.assertEqual(_subcommands(caller), {"schema", "catalog", "prompt"})


if __name__ == "__main__":
    unittest.main()
