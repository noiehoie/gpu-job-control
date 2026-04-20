from __future__ import annotations

import unittest
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gpu_job.models import Job
from gpu_job.provider_catalog import build_provider_catalog
from gpu_job.providers.modal import ModalProvider
from gpu_job.providers.runpod import RunPodProvider
from gpu_job.providers.vast import (
    VastProvider,
    _parse_vast_instance_id,
    _scp_parts_from_ssh_parts,
    _vast_image_login_from_distribution,
    _vast_login_args,
    _vast_ssh_parts,
)
from gpu_job.store import JobStore
from gpu_job.timing import timing_summary


class VastAsrProviderTest(unittest.TestCase):
    def test_vast_ssh_url_normalizes_to_ssh_command(self) -> None:
        parts = _vast_ssh_parts("ssh://root@142.112.39.215:8579")

        self.assertEqual(parts[0], "ssh")
        self.assertIn("-p", parts)
        self.assertIn("8579", parts)
        self.assertEqual(parts[-1], "root@142.112.39.215")

    def test_scp_parts_use_uppercase_port_flag(self) -> None:
        ssh_parts = _vast_ssh_parts("ssh://root@142.112.39.215:8579")
        scp_parts = _scp_parts_from_ssh_parts(ssh_parts)

        self.assertEqual(scp_parts[0], "scp")
        self.assertIn("-P", scp_parts)
        self.assertIn("8579", scp_parts)

    def test_parse_instance_id_handles_empty_create_output(self) -> None:
        self.assertEqual(_parse_vast_instance_id(""), "")
        self.assertEqual(_parse_vast_instance_id('{"new_contract": 12345}'), "12345")
        self.assertEqual(_parse_vast_instance_id("{'new_contract': 67890}"), "67890")

    def test_provider_catalog_lists_vast_asr_as_executable(self) -> None:
        catalog = build_provider_catalog(
            {
                "provider_limits": {"vast": {"asr": 1, "*": 1}},
                "provider_price_usd_per_second": {"vast": 0.0005},
            }
        )

        vast = catalog["providers"]["vast"]
        self.assertIn("asr", vast["adapter_supported_job_types"])
        self.assertIn("asr", vast["supported_job_types"])

    def test_provider_plans_share_execution_plan_shape(self) -> None:
        job = Job(
            job_id="asr-plan-shape",
            job_type="asr",
            input_uri="/tmp/input.mp4",
            output_uri="/tmp/out",
            worker_image="gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
            gpu_profile="asr_diarization",
            model="large-v3",
            metadata={"input": {"diarize": True, "language": "ja"}},
        )

        with patch.object(VastProvider, "offers", return_value={"query": "gpu_ram>=24", "offers": []}):
            plans = [ModalProvider().plan(job), RunPodProvider().plan(job), VastProvider().plan(job)]

        for plan in plans:
            execution_plan = plan["execution_plan"]
            self.assertEqual(execution_plan["execution_plan_version"], "gpu-job-execution-plan-v1")
            self.assertEqual(execution_plan["job_type"], "asr")
            self.assertEqual(execution_plan["gpu_profile"], "asr_diarization")
            self.assertEqual(execution_plan["required_backends"], ["faster_whisper", "pyannote"])
            self.assertEqual(execution_plan["image_contract"]["contract_id"], "asr-diarization-large-v3-pyannote3.3.2-cuda12.4")
            self.assertIn("image_distribution", execution_plan)

    def test_vast_diarization_requires_verified_image_before_gpu_allocation(self) -> None:
        with TemporaryDirectory() as tmp:
            media = Path(tmp) / "input.mp4"
            media.write_bytes(b"dummy media; should not be uploaded")
            store = JobStore(Path(tmp) / "store")
            job = Job(
                job_id="asr-vast-image-contract",
                job_type="asr",
                input_uri=str(media),
                output_uri=str(Path(tmp) / "out"),
                worker_image="gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                gpu_profile="asr_diarization",
                model="large-v3",
                metadata={"input": {"diarize": True, "language": "ja"}},
                limits={"max_runtime_minutes": 10, "max_cost_usd": 1},
            )
            provider = VastProvider()
            image_registry = {
                "registry_version": "gpu-job-image-contract-registry-v1",
                "image_contracts": {
                    "asr-diarization-large-v3-pyannote3.3.2-cuda12.4": {
                        "contract_id": "asr-diarization-large-v3-pyannote3.3.2-cuda12.4",
                        "status": "missing",
                        "image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                        "entrypoint": "gpu-job-asr-worker",
                        "provides_backends": ["faster_whisper", "pyannote"],
                        "provider_images": {"vast": {"status": "missing"}},
                    }
                },
            }

            with (
                patch("gpu_job.providers.vast.vast_bin", return_value="/usr/bin/true"),
                patch("gpu_job.image_contracts.load_image_contract_registry", return_value=image_registry),
                patch.object(provider, "_instances_by_label", return_value=[]),
                patch.object(provider, "offers", side_effect=AssertionError("offers must not be queried")),
            ):
                result = provider._submit_direct_instance_asr(job, store)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.provider_job_id, "")
            self.assertIn("verified prebuilt image contract", result.error)
            self.assertEqual(result.metadata.get("vast_startup_attempts"), None)

    def test_vast_runtime_install_flag_is_rejected_before_gpu_allocation(self) -> None:
        with TemporaryDirectory() as tmp:
            media = Path(tmp) / "input.mp4"
            media.write_bytes(b"dummy media; should not be uploaded")
            store = JobStore(Path(tmp) / "store")
            job = Job(
                job_id="asr-vast-debug-runtime-install",
                job_type="asr",
                input_uri=str(media),
                output_uri=str(Path(tmp) / "out"),
                worker_image="gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                gpu_profile="asr_diarization",
                model="large-v3",
                metadata={
                    "allow_runtime_dependency_install": True,
                    "input": {"diarize": True, "language": "ja"},
                },
                limits={"max_runtime_minutes": 10, "max_cost_usd": 1},
            )
            provider = VastProvider()

            with (
                patch("gpu_job.providers.vast.vast_bin", return_value="/usr/bin/true"),
                patch.object(provider, "_instances_by_label", return_value=[]),
                patch.object(provider, "offers", side_effect=AssertionError("offers must not be queried")),
            ):
                result = provider._submit_direct_instance_asr(job, store)

            self.assertEqual(result.status, "failed")
            self.assertIn("runtime dependency install is disabled", result.error)
            self.assertEqual(result.provider_job_id, "")

    def test_vast_onstart_uses_sleep_only_for_prebuilt_image(self) -> None:
        provider = VastProvider()

        self.assertEqual(provider._asr_onstart_command(), "sleep infinity")

    def test_vast_private_registry_login_is_deterministic_from_distribution(self) -> None:
        distribution = {
            "registry_auth": {
                "type": "vast_image_login",
                "registry": "ghcr.io",
                "username_env": "GHCR_USERNAME",
                "password_env": "GHCR_TOKEN",
            }
        }

        with patch.dict("os.environ", {"GHCR_USERNAME": "noiehoie", "GHCR_TOKEN": "token-value"}, clear=False):
            login = _vast_image_login_from_distribution(distribution)

        self.assertEqual(login, "-u noiehoie -p token-value ghcr.io")
        self.assertEqual(_vast_login_args(login), ["--login", "-u noiehoie -p token-value ghcr.io"])

    def test_vast_private_registry_login_requires_token_before_gpu_allocation(self) -> None:
        distribution = {
            "registry_auth": {
                "type": "vast_image_login",
                "registry": "ghcr.io",
                "username_env": "GHCR_USERNAME",
                "password_env": "GHCR_TOKEN",
            }
        }

        with patch.dict("os.environ", {"GHCR_USERNAME": "noiehoie"}, clear=True):
            with self.assertRaises(RuntimeError) as raised:
                _vast_image_login_from_distribution(distribution)

        self.assertIn("requires registry credentials before GPU allocation", str(raised.exception))

    def test_vast_verified_contract_without_provider_image_stops_before_offers(self) -> None:
        with TemporaryDirectory() as tmp:
            media = Path(tmp) / "input.mp4"
            media.write_bytes(b"dummy media; should not be uploaded")
            store = JobStore(Path(tmp) / "store")
            job = Job(
                job_id="asr-vast-missing-provider-image",
                job_type="asr",
                input_uri=str(media),
                output_uri=str(Path(tmp) / "out"),
                worker_image="gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                gpu_profile="asr_diarization",
                model="large-v3",
                metadata={"input": {"diarize": True, "language": "ja"}},
                limits={"max_runtime_minutes": 10, "max_cost_usd": 1},
            )
            provider = VastProvider()
            image_registry = {
                "registry_version": "gpu-job-image-contract-registry-v1",
                "image_contracts": {
                    "asr-diarization-large-v3-pyannote3.3.2-cuda12.4": {
                        "contract_id": "asr-diarization-large-v3-pyannote3.3.2-cuda12.4",
                        "status": "verified",
                        "image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                        "entrypoint": "gpu-job-asr-worker",
                        "provides_backends": ["faster_whisper", "pyannote"],
                        "provider_images": {"vast": {"status": "missing"}},
                    }
                },
            }

            with (
                patch("gpu_job.providers.vast.vast_bin", return_value="/usr/bin/true"),
                patch("gpu_job.image_contracts.load_image_contract_registry", return_value=image_registry),
                patch.object(provider, "_instances_by_label", return_value=[]),
                patch.object(provider, "offers", side_effect=AssertionError("offers must not be queried")),
            ):
                result = provider._submit_direct_instance_asr(job, store)

            self.assertEqual(result.status, "failed")
            self.assertIn("provider-distributed image", result.error)

    def test_vast_rejects_startup_budget_that_leaves_no_worker_time_before_gpu_allocation(self) -> None:
        with TemporaryDirectory() as tmp:
            media = Path(tmp) / "input.mp4"
            media.write_bytes(b"dummy media; should not be uploaded")
            store = JobStore(Path(tmp) / "store")
            job = Job(
                job_id="asr-vast-impossible-budget",
                job_type="asr",
                input_uri=str(media),
                output_uri=str(Path(tmp) / "out"),
                worker_image="gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                gpu_profile="asr_diarization",
                model="large-v3",
                metadata={
                    "input": {"diarize": True, "language": "ja"},
                    "max_startup_seconds": 600,
                    "min_worker_seconds": 120,
                },
                limits={"max_runtime_minutes": 10, "max_cost_usd": 1},
            )
            provider = VastProvider()

            with (
                patch("gpu_job.providers.vast.vast_bin", return_value="/usr/bin/true"),
                patch.dict("os.environ", {"HF_TOKEN": "hf", "GHCR_TOKEN": "ghcr", "GHCR_USERNAME": "noiehoie"}, clear=False),
                patch.object(provider, "_instances_by_label", return_value=[]),
                patch.object(provider, "offers", side_effect=AssertionError("offers must not be queried")),
            ):
                result = provider._submit_direct_instance_asr(job, store)

            self.assertEqual(result.status, "failed")
            self.assertIn("max_runtime_minutes must exceed max_startup_seconds plus min_worker_seconds", result.error)
            self.assertEqual(result.provider_job_id, "")

    def test_vast_smoke_direct_path_records_lifecycle_phases(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "store")
            job = Job(
                job_id="vast-smoke-lifecycle",
                job_type="smoke",
                input_uri="/tmp/input",
                output_uri=str(Path(tmp) / "out"),
                worker_image="nvidia/cuda:12.4.1-base-ubuntu22.04",
                gpu_profile="smoke",
                metadata={"allow_vast_direct_instance_smoke": True},
                limits={"max_startup_seconds": 30},
            )
            provider = VastProvider()

            def fake_run(args, **kwargs):
                if args[:3] == ["/usr/bin/vastai", "create", "instance"]:
                    return CompletedProcess(args, 0, stdout='{"new_contract": 12345}', stderr="")
                if args[:2] == ["/usr/bin/vastai", "logs"]:
                    return CompletedProcess(
                        args,
                        0,
                        stdout="GPU_JOB_SMOKE_START\nNVIDIA RTX 3090, 24576 MiB, 550.00\nGPU_JOB_SMOKE_DONE\n",
                        stderr="",
                    )
                if args[:3] == ["/usr/bin/vastai", "destroy", "instance"]:
                    return CompletedProcess(args, 0, stdout='{"success": true}', stderr="")
                raise AssertionError(f"unexpected command: {args!r}")

            with (
                patch("gpu_job.providers.vast.vast_bin", return_value="/usr/bin/vastai"),
                patch("gpu_job.providers.vast.run", side_effect=fake_run),
                patch.object(provider, "offers", return_value={"offers": [{"id": 99, "dph_total": 0.5, "gpu_name": "RTX 3090"}]}),
                patch.object(provider, "_instance_ids", return_value=set()),
                patch.object(provider, "_instances_by_label", return_value=[]),
            ):
                result = provider.submit(job, store, execute=True)

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.provider_job_id, "12345")
        events = result.metadata["timing_v2"]["events"]
        phase_events = [(item["phase"], item["event"], item.get("status", "")) for item in events]
        self.assertIn(("reserving_workspace", "enter", ""), phase_events)
        self.assertIn(("reserving_workspace", "exit", "ok"), phase_events)
        self.assertIn(("image_materialization", "enter", ""), phase_events)
        self.assertIn(("image_materialization", "exit", "ready"), phase_events)
        self.assertIn(("starting_worker", "instant", "ok"), phase_events)
        self.assertIn(("running_worker", "enter", ""), phase_events)
        self.assertIn(("running_worker", "exit", "ok"), phase_events)
        self.assertIn(("collecting_artifacts", "enter", ""), phase_events)
        self.assertIn(("collecting_artifacts", "exit", "ok"), phase_events)
        self.assertIn(("cleaning_up", "enter", ""), phase_events)
        self.assertIn(("cleaning_up", "exit", "ok"), phase_events)
        self.assertFalse([phase for phase in timing_summary(result)["phases"] if phase.get("open")])
        self.assertEqual(
            result.metadata["vast_lifecycle_evidence"],
            {
                "offer_id": "99",
                "instance_id": "12345",
                "label": "gpu-job:vast-smoke-lifecycle",
                "create_detection": "create_stdout",
                "last_known_vast_state": "",
            },
        )


if __name__ == "__main__":
    unittest.main()
