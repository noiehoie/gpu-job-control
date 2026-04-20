from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gpu_job.contracts import plan_workload
from gpu_job.execution_record import build_execution_record, execution_record_hash, write_execution_record
from gpu_job.models import Job
from gpu_job.provider_catalog import build_provider_catalog
from gpu_job.provider_contract_probe import list_contract_probes
from gpu_job.runner import submit_job
from gpu_job.store import JobStore
from gpu_job.timing import enter_phase, exit_phase, instant_phase
from gpu_job.workspace_registry import provider_workspace_plan, workspace_registry_schema


class ProductionContractTest(unittest.TestCase):
    def test_plan_workload_returns_formal_plan_quote(self) -> None:
        catalog = build_provider_catalog(
            {
                "provider_limits": {"modal": {"asr_fast": 1}},
                "provider_price_usd_per_second": {"modal": 0.001},
                "workflow_budget_policy": {
                    "default": {
                        "auto_approve_cap_usd": 3.0,
                        "hard_cap_usd": 10.0,
                        "allowed_providers": ["modal"],
                        "retry_multiplier": 1.0,
                        "safety_margin": 1.0,
                    }
                },
            }
        )
        result = plan_workload(
            {
                "workload_kind": "transcription.whisper",
                "inputs": [{"uri": "file:///tmp/a.mp4", "duration_seconds": 120}],
                "requirements": {"max_cost_usd": 5},
                "business_context": {"budget_class": "default"},
            },
            catalog=catalog,
        )

        quote = result["plan_quote"]
        self.assertTrue(result["ok"])
        self.assertEqual(quote["quote_version"], "gpu-job-plan-quote-v1")
        self.assertTrue(quote["quote_id"].startswith("quote-"))
        self.assertEqual(quote["quote_hash"], result["plan"]["plan_quote"]["quote_hash"])
        self.assertEqual(quote["explanation"]["selected_provider"], quote["selected_option"]["provider"])
        support_contract = quote["selected_option"]["catalog_capability"]["support_contract"]
        self.assertIn(support_contract["highest_support_level"], {"catalog_routable", "canary_executable", "production_route"})
        self.assertTrue(support_contract["levels"]["registered"])
        self.assertTrue(support_contract["levels"]["catalog_routable"])

    def test_workspace_plan_blocks_missing_runpod_diarization_image(self) -> None:
        job = _asr_diarization_job("workspace-runpod")
        image_registry = {
            "registry_version": "gpu-job-image-contract-registry-v1",
            "image_contracts": {},
        }

        with (
            patch("gpu_job.image_contracts.load_image_contract_registry", return_value=image_registry),
            patch("gpu_job.workspace_registry.recent_contract_probe_summary", return_value={"latest": {}}),
        ):
            plan = provider_workspace_plan(job, "runpod")

        self.assertEqual(plan["workspace_registry_version"], "gpu-job-provider-workspace-registry-v1")
        self.assertEqual(plan["decision"], "requires_action")
        self.assertEqual(plan["required_actions"][0]["type"], "build_image")
        self.assertEqual(plan["required_actions"][1]["type"], "run_contract_probe")
        self.assertEqual(plan["image_contract"]["contract_id"], "asr-diarization-large-v3-pyannote3.3.2-cuda12.4")
        self.assertEqual(plan["image_contract"]["status"], "missing_image_contract")
        self.assertIn("required_fields", workspace_registry_schema())

    def test_workspace_registry_exposes_provider_documented_workspace_modes(self) -> None:
        runpod = provider_workspace_plan(_asr_diarization_job("workspace-runpod-modes"), "runpod")
        vast = provider_workspace_plan(_asr_diarization_job("workspace-vast-modes"), "vast")
        modal = provider_workspace_plan(_asr_diarization_job("workspace-modal-modes"), "modal")

        self.assertEqual(runpod["workspace"]["workspace_modes"]["serverless"]["network_volume_mount"], "/runpod-volume")
        self.assertEqual(runpod["workspace"]["workspace_modes"]["pod"]["network_volume_mount"], "/workspace")
        self.assertIn("job_ttl_seconds", runpod["workspace"]["workspace_modes"]["serverless"]["timing_fields"])
        self.assertIn("entrypoint", vast["workspace"]["workspace_modes"]["direct_instance"]["launch_modes"])
        self.assertIn("entrypoint", vast["workspace"]["workspace_modes"]["direct_instance"]["entrypoint_warning"])
        self.assertEqual(modal["workspace"]["workspace_modes"]["function"]["volume_mount_default"], "/mnt")
        self.assertIn("commit/reload", modal["workspace"]["workspace_modes"]["function"]["consistency"])

    def test_workspace_plan_blocks_unproven_runpod_diarization_runtime_even_with_verified_image(self) -> None:
        job = _asr_diarization_job("workspace-runpod-probe")
        image_registry = {
            "registry_version": "gpu-job-image-contract-registry-v1",
            "image_contracts": {
                "asr-diarization-large-v3-pyannote3.3.2-cuda12.4": {
                    "contract_id": "asr-diarization-large-v3-pyannote3.3.2-cuda12.4",
                    "status": "verified",
                    "image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                    "provides_backends": ["faster_whisper", "pyannote"],
                    "provider_images": {
                        "runpod": {
                            "status": "verified",
                            "image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                        }
                    },
                }
            },
        }

        with (
            patch("gpu_job.image_contracts.load_image_contract_registry", return_value=image_registry),
            patch("gpu_job.workspace_registry.recent_contract_probe_summary", return_value={"latest": {}}),
        ):
            plan = provider_workspace_plan(job, "runpod")

        self.assertEqual(plan["decision"], "requires_action")
        self.assertEqual(
            plan["required_actions"],
            [
                {
                    "type": "run_contract_probe",
                    "contract_probe": "runpod.asr_diarization.pyannote",
                    "status": "unverified",
                    "reason": "provider runtime contract probe has not passed for this workspace",
                }
            ],
        )

    def test_workspace_plan_allows_verified_runpod_diarization_runtime_after_probe(self) -> None:
        job = _asr_diarization_job("workspace-runpod-ready")
        image_registry = {
            "registry_version": "gpu-job-image-contract-registry-v1",
            "image_contracts": {
                "asr-diarization-large-v3-pyannote3.3.2-cuda12.4": {
                    "contract_id": "asr-diarization-large-v3-pyannote3.3.2-cuda12.4",
                    "status": "verified",
                    "image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                    "provides_backends": ["faster_whisper", "pyannote"],
                    "provider_images": {
                        "runpod": {
                            "status": "verified",
                            "image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                        }
                    },
                }
            },
        }
        probe_summary = {"latest": {"runpod.asr_diarization.pyannote": {"ok": True, "verdict": "pass"}}}

        with (
            patch("gpu_job.image_contracts.load_image_contract_registry", return_value=image_registry),
            patch("gpu_job.workspace_registry.recent_contract_probe_summary", return_value=probe_summary),
        ):
            plan = provider_workspace_plan(job, "runpod")

        self.assertEqual(plan["decision"], "ready")
        self.assertEqual(plan["required_actions"], [])

    def test_workspace_plan_blocks_unproven_vast_diarization_runtime_even_with_verified_image(self) -> None:
        job = _asr_diarization_job("workspace-vast-probe")
        image_registry = {
            "registry_version": "gpu-job-image-contract-registry-v1",
            "image_contracts": {
                "asr-diarization-large-v3-pyannote3.3.2-cuda12.4": {
                    "contract_id": "asr-diarization-large-v3-pyannote3.3.2-cuda12.4",
                    "status": "verified",
                    "image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                    "provides_backends": ["faster_whisper", "pyannote"],
                    "provider_images": {
                        "vast": {
                            "status": "verified",
                            "image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                        }
                    },
                }
            },
        }

        with (
            patch("gpu_job.image_contracts.load_image_contract_registry", return_value=image_registry),
            patch("gpu_job.workspace_registry.recent_contract_probe_summary", return_value={"latest": {}}),
        ):
            plan = provider_workspace_plan(job, "vast")

        self.assertEqual(plan["decision"], "requires_action")
        self.assertEqual(
            plan["required_actions"],
            [
                {
                    "type": "run_contract_probe",
                    "contract_probe": "vast.asr_diarization.pyannote",
                    "status": "unverified",
                    "reason": "provider runtime contract probe has not passed for this workspace",
                }
            ],
        )

    def test_workspace_plan_allows_matching_contract_probe_job_to_create_evidence(self) -> None:
        job = _asr_diarization_job("workspace-vast-self-probe")
        job.metadata["contract_probe"] = {"probe_name": "vast.asr_diarization.pyannote"}
        image_registry = {
            "registry_version": "gpu-job-image-contract-registry-v1",
            "image_contracts": {
                "asr-diarization-large-v3-pyannote3.3.2-cuda12.4": {
                    "contract_id": "asr-diarization-large-v3-pyannote3.3.2-cuda12.4",
                    "status": "verified",
                    "image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                    "provides_backends": ["faster_whisper", "pyannote"],
                    "provider_images": {
                        "vast": {
                            "status": "verified",
                            "image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                        }
                    },
                }
            },
        }

        with (
            patch("gpu_job.image_contracts.load_image_contract_registry", return_value=image_registry),
            patch("gpu_job.workspace_registry.recent_contract_probe_summary", return_value={"latest": {}}),
        ):
            plan = provider_workspace_plan(job, "vast")

        self.assertEqual(plan["decision"], "ready")
        self.assertEqual(plan["required_actions"], [])
        self.assertTrue(plan["runtime_contract_probe"]["self_probe"])

    def test_execution_record_is_written_without_raw_error_text(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "store")
            job = _asr_diarization_job("exec-record")
            job.provider = "runpod"
            job.status = "failed"
            job.error = "secret raw provider error should not be copied"
            job.exit_code = 1
            job.metadata["selected_provider"] = "runpod"
            job.metadata["workspace_plan"] = provider_workspace_plan(job, "runpod")
            enter_phase(job, "validated", at=1.0, provider="runpod")
            exit_phase(job, "validated", at=2.0, provider="runpod", status="failed", error_class="workspace_requires_action")
            instant_phase(job, "failed", at=3.0, provider="runpod", status="failed")
            artifact = store.artifact_dir(job.job_id)
            artifact.mkdir(parents=True)
            (artifact / "manifest.json").write_text(json.dumps({"files": []}) + "\n")
            store.save(job)

            record = write_execution_record(job, store=store)
            path = artifact / "execution_record.json"

            self.assertTrue(path.is_file())
            self.assertEqual(record["execution_record_version"], "gpu-job-execution-record-v1")
            self.assertEqual(record["record_hash"], execution_record_hash(record))
            self.assertTrue(record["terminal"]["has_error"])
            self.assertNotIn("secret raw provider error", json.dumps(record, ensure_ascii=False))

    def test_execution_record_uses_bound_workflow_plan_quote(self) -> None:
        job = Job(
            job_id="exec-record-workflow-quote",
            job_type="asr",
            input_uri="file:///tmp/input.mp4",
            output_uri="local://out",
            worker_image="auto",
            gpu_profile="asr_fast",
            metadata={
                "plan_quote": {
                    "quote_id": "stale-template",
                    "selected_option": {"provider": "vast", "gpu_profile": "asr_fast"},
                },
                "workflow_plan_quote": {
                    "quote_id": "workflow-bound",
                    "selected_option": {"provider": "runpod", "gpu_profile": "asr_fast"},
                },
            },
        )

        record = build_execution_record(job)

        self.assertEqual(record["plan_quote"]["quote_id"], "workflow-bound")

    def test_execution_record_ignores_empty_workflow_plan_quote(self) -> None:
        job = Job(
            job_id="exec-record-empty-workflow-quote",
            job_type="asr",
            input_uri="file:///tmp/input.mp4",
            output_uri="local://out",
            worker_image="auto",
            gpu_profile="asr_fast",
            metadata={
                "plan_quote": {
                    "quote_id": "child-bound",
                    "selected_option": {"provider": "vast", "gpu_profile": "asr_fast"},
                },
                "workflow_plan_quote": {},
            },
        )

        record = build_execution_record(job)

        self.assertEqual(record["plan_quote"]["quote_id"], "child-bound")

    def test_execution_record_uses_child_plan_quote_for_cpu_workflow_helper(self) -> None:
        job = Job(
            job_id="exec-record-helper-quote",
            job_type="cpu_workflow_helper",
            input_uri="workflow://helper",
            output_uri="local://out",
            worker_image="auto",
            gpu_profile="cpu",
            metadata={
                "plan_quote": {
                    "quote_id": "helper-child",
                    "selected_option": {"provider": "local", "gpu_profile": "cpu"},
                },
                "workflow_plan_quote": {
                    "quote_id": "workflow-parent",
                    "selected_option": {"provider": "runpod", "gpu_profile": "asr_fast"},
                },
            },
        )

        record = build_execution_record(job)

        self.assertEqual(record["plan_quote"]["quote_id"], "helper-child")

    def test_runpod_asr_diarization_contract_probe_is_registered(self) -> None:
        probes = list_contract_probes()["probes"]

        self.assertIn("runpod.asr_diarization.pyannote", probes)
        self.assertTrue(probes["runpod.asr_diarization.pyannote"]["workspace_contract_required"])
        self.assertIn("vast.asr_diarization.pyannote", probes)
        self.assertTrue(probes["vast.asr_diarization.pyannote"]["workspace_contract_required"])

    def test_submit_binds_auto_provider_to_plan_quote(self) -> None:
        class FakeProvider:
            def submit(self, job: Job, store: JobStore, execute: bool = False) -> Job:
                job.status = "planned"
                store.save(job)
                return job

        with TemporaryDirectory() as tmp:
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = tmp
            try:
                job = Job(
                    job_id="quote-bound-submit",
                    job_type="smoke",
                    input_uri="none://probe",
                    output_uri="local://out",
                    worker_image="auto",
                    gpu_profile="embedding",
                    metadata={
                        "plan_quote": {
                            "quote_id": "quote-test",
                            "selected_option": {"provider": "runpod", "gpu_profile": "embedding"},
                        }
                    },
                )
                with (
                    patch("gpu_job.runner.route_job", side_effect=AssertionError("live router must not run")),
                    patch("gpu_job.runner.get_provider", return_value=FakeProvider()),
                    patch("gpu_job.runner.provider_circuit_state", return_value={"ok": True}),
                    patch("gpu_job.runner.validate_policy", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_provenance", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_compliance", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_model_capability", return_value={"ok": True}),
                    patch("gpu_job.runner.quota_check", return_value={"ok": True}),
                    patch("gpu_job.runner.cost_estimate", return_value={"ok": True}),
                    patch("gpu_job.runner.secret_check", return_value={"ok": True}),
                    patch("gpu_job.runner.placement_check", return_value={"ok": True}),
                    patch("gpu_job.runner.preemption_check", return_value={"ok": True}),
                    patch("gpu_job.runner.timeout_contract", return_value={"ok": True, "timeout_seconds": 60}),
                    patch("gpu_job.runner.make_decision", return_value={"decision_hash": "test"}),
                ):
                    result = submit_job(job, provider_name="auto", execute=False)

                self.assertTrue(result["ok"])
                self.assertEqual(result["job"]["metadata"]["selected_provider"], "runpod")
                self.assertEqual(result["job"]["metadata"]["route_result"]["source"], "plan_quote")
                self.assertEqual(result["job"]["metadata"]["route_result"]["gpu_profile"], "embedding")
                self.assertIn("plan_quote", result["job"]["metadata"]["route_explanation"])
            finally:
                if old_data_home is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_data_home

    def test_submit_binds_auto_provider_to_workflow_plan_quote(self) -> None:
        class FakeProvider:
            def submit(self, job: Job, store: JobStore, execute: bool = False) -> Job:
                job.status = "planned"
                store.save(job)
                return job

        with TemporaryDirectory() as tmp:
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = tmp
            try:
                job = Job(
                    job_id="workflow-quote-bound-submit",
                    job_type="smoke",
                    input_uri="none://probe",
                    output_uri="local://out",
                    worker_image="auto",
                    gpu_profile="embedding",
                    metadata={
                        "workflow_plan_quote": {
                            "quote_id": "workflow-quote-test",
                            "selected_option": {"provider": "vast", "gpu_profile": "embedding"},
                        }
                    },
                )
                with (
                    patch("gpu_job.runner.route_job", side_effect=AssertionError("live router must not run")),
                    patch("gpu_job.runner.get_provider", return_value=FakeProvider()),
                    patch("gpu_job.runner.provider_circuit_state", return_value={"ok": True}),
                    patch("gpu_job.runner.validate_policy", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_provenance", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_compliance", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_model_capability", return_value={"ok": True}),
                    patch("gpu_job.runner.quota_check", return_value={"ok": True}),
                    patch("gpu_job.runner.cost_estimate", return_value={"ok": True}),
                    patch("gpu_job.runner.secret_check", return_value={"ok": True}),
                    patch("gpu_job.runner.placement_check", return_value={"ok": True}),
                    patch("gpu_job.runner.preemption_check", return_value={"ok": True}),
                    patch("gpu_job.runner.timeout_contract", return_value={"ok": True, "timeout_seconds": 60}),
                    patch("gpu_job.runner.make_decision", return_value={"decision_hash": "test"}),
                ):
                    result = submit_job(job, provider_name="auto", execute=False)

                self.assertTrue(result["ok"])
                self.assertEqual(result["job"]["metadata"]["selected_provider"], "vast")
                self.assertEqual(result["job"]["metadata"]["route_result"]["plan_quote_id"], "workflow-quote-test")
            finally:
                if old_data_home is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_data_home

    def test_submit_prefers_workflow_plan_quote_over_template_plan_quote_for_non_helper(self) -> None:
        class FakeProvider:
            def submit(self, job: Job, store: JobStore, execute: bool = False) -> Job:
                job.status = "planned"
                store.save(job)
                return job

        with TemporaryDirectory() as tmp:
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = tmp
            try:
                job = Job(
                    job_id="workflow-quote-over-template",
                    job_type="smoke",
                    input_uri="none://probe",
                    output_uri="local://out",
                    worker_image="auto",
                    gpu_profile="embedding",
                    metadata={
                        "plan_quote": {
                            "quote_id": "stale-template",
                            "selected_option": {"provider": "vast", "gpu_profile": "embedding"},
                        },
                        "workflow_plan_quote": {
                            "quote_id": "workflow-quote",
                            "selected_option": {"provider": "runpod", "gpu_profile": "embedding"},
                        },
                    },
                )
                with (
                    patch("gpu_job.runner.route_job", side_effect=AssertionError("live router must not run")),
                    patch("gpu_job.runner.get_provider", return_value=FakeProvider()),
                    patch("gpu_job.runner.provider_circuit_state", return_value={"ok": True}),
                    patch("gpu_job.runner.validate_policy", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_provenance", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_compliance", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_model_capability", return_value={"ok": True}),
                    patch("gpu_job.runner.quota_check", return_value={"ok": True}),
                    patch("gpu_job.runner.cost_estimate", return_value={"ok": True}),
                    patch("gpu_job.runner.secret_check", return_value={"ok": True}),
                    patch("gpu_job.runner.placement_check", return_value={"ok": True}),
                    patch("gpu_job.runner.preemption_check", return_value={"ok": True}),
                    patch("gpu_job.runner.timeout_contract", return_value={"ok": True, "timeout_seconds": 60}),
                    patch("gpu_job.runner.make_decision", return_value={"decision_hash": "test"}),
                ):
                    result = submit_job(job, provider_name="auto", execute=False)

                self.assertTrue(result["ok"])
                self.assertEqual(result["job"]["metadata"]["selected_provider"], "runpod")
                self.assertEqual(result["job"]["metadata"]["route_result"]["plan_quote_id"], "workflow-quote")
            finally:
                if old_data_home is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_data_home

    def test_submit_rejects_explicit_provider_that_conflicts_with_plan_quote(self) -> None:
        with TemporaryDirectory() as tmp:
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = tmp
            try:
                job = Job(
                    job_id="quote-mismatch-submit",
                    job_type="smoke",
                    input_uri="none://probe",
                    output_uri="local://out",
                    worker_image="auto",
                    gpu_profile="embedding",
                    metadata={
                        "plan_quote": {
                            "quote_id": "quote-test",
                            "selected_option": {"provider": "runpod", "gpu_profile": "embedding"},
                        }
                    },
                )
                with patch("gpu_job.runner.get_provider", side_effect=AssertionError("provider must not be allocated")):
                    result = submit_job(job, provider_name="vast", execute=False)

                self.assertFalse(result["ok"])
                self.assertEqual(result["job"]["status"], "failed")
                self.assertEqual(result["job"]["metadata"]["route_result"]["quoted_provider"], "runpod")
                self.assertEqual(result["job"]["metadata"]["selected_provider"], "vast")
                self.assertIn("conflicts with plan_quote", result["error"])
            finally:
                if old_data_home is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_data_home

    def test_submit_rejects_workspace_plan_drift_before_gpu_allocation(self) -> None:
        class FakeProvider:
            def submit(self, job: Job, store: JobStore, execute: bool = False) -> Job:
                raise AssertionError("provider submit must not run on workspace drift")

        with TemporaryDirectory() as tmp:
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = tmp
            try:
                job = _asr_diarization_job("workspace-drift-submit")
                job.metadata["plan_quote"] = {
                    "quote_id": "quote-runpod",
                    "selected_option": {"provider": "runpod", "gpu_profile": "asr_diarization"},
                }
                stale = provider_workspace_plan(job, "runpod")
                stale["workspace_plan_id"] = "workspace-stale"
                job.metadata["workspace_plan"] = stale

                with (
                    patch("gpu_job.runner.get_provider", return_value=FakeProvider()),
                    patch("gpu_job.runner.provider_circuit_state", return_value={"ok": True}),
                    patch("gpu_job.runner.validate_policy", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_provenance", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_compliance", return_value={"ok": True}),
                    patch("gpu_job.runner.evaluate_model_capability", return_value={"ok": True}),
                    patch("gpu_job.runner.quota_check", return_value={"ok": True}),
                    patch("gpu_job.runner.cost_estimate", return_value={"ok": True}),
                    patch("gpu_job.runner.secret_check", return_value={"ok": True}),
                    patch("gpu_job.runner.placement_check", return_value={"ok": True}),
                    patch("gpu_job.runner.preemption_check", return_value={"ok": True}),
                    patch("gpu_job.runner.timeout_contract", return_value={"ok": True, "timeout_seconds": 60}),
                ):
                    result = submit_job(job, provider_name="auto", execute=True, enforce_capacity=False)

                self.assertFalse(result["ok"])
                self.assertEqual(result["job"]["status"], "failed")
                self.assertEqual(result["job"]["metadata"]["workspace_plan"]["workspace_plan_id"], "workspace-stale")
                self.assertIn("workspace_plan_current", result["job"]["metadata"])
                self.assertIn("drift", result["error"])
            finally:
                if old_data_home is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_data_home


def _asr_diarization_job(job_id: str) -> Job:
    return Job(
        job_id=job_id,
        job_type="asr",
        input_uri="file:///tmp/input.mp4",
        output_uri="local://out",
        worker_image="gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
        gpu_profile="asr_diarization",
        model="large-v3",
        metadata={"input": {"diarize": True, "language": "ja"}, "secret_refs": ["hf_token"]},
        limits={"max_runtime_minutes": 30, "max_cost_usd": 5},
    )


if __name__ == "__main__":
    unittest.main()
