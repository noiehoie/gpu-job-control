from __future__ import annotations

import unittest
import os
import json
from tempfile import TemporaryDirectory
from pathlib import Path

from gpu_job.contracts import artifact_manifest_schema, failure_taxonomy, plan_workload, workload_to_workflow
from gpu_job.error_class import classify_error
from gpu_job.models import Job
from gpu_job.provider_catalog import build_provider_catalog, provider_profile_catalog_limit, provider_supports_job_type
from gpu_job.provider_probe import probe_provider, recent_probe_summary
from gpu_job.store import JobStore
from gpu_job.workflow import advance_workflows, execute_workflow, load_workflow


class ProviderCatalogContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_data_home = os.environ.get("XDG_DATA_HOME")
        self.tmp = TemporaryDirectory()
        os.environ["XDG_DATA_HOME"] = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()
        if self.old_data_home is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self.old_data_home

    def test_catalog_exposes_provider_capabilities_and_profile_limits(self) -> None:
        catalog = build_provider_catalog(
            {
                "provider_limits": {"modal": {"llm_heavy": 1, "asr": 2, "*": 1}},
                "provider_regions": {"modal": "external"},
                "provider_price_usd_per_second": {"modal": 0.001},
            }
        )

        modal = catalog["providers"]["modal"]

        self.assertTrue(modal["is_cloud_gpu_provider"])
        self.assertEqual(modal["support_contract"]["highest_support_level"], "production_route")
        self.assertTrue(modal["support_contract"]["levels"]["registered"])
        self.assertTrue(modal["support_contract"]["levels"]["catalog_routable"])
        self.assertIn("asr", modal["supported_job_types"])
        self.assertIn({"gpu_profile": "asr", "max_concurrent": 2}, modal["gpu_profiles"])
        self.assertEqual(provider_profile_catalog_limit("modal", "asr", {"provider_limits": {"modal": {"asr": 2}}}), 2)
        self.assertTrue(provider_supports_job_type("modal", "llm_heavy", catalog))

    def test_catalog_distinguishes_cloud_canary_from_local_production_routes(self) -> None:
        catalog = build_provider_catalog(
            {
                "provider_limits": {"local": {"*": 1}, "runpod": {"asr": 1}, "vast": {"asr": 1}},
                "provider_regions": {"local": "local", "runpod": "external", "vast": "external"},
                "provider_price_usd_per_second": {"local": 0.0, "runpod": 0.0008, "vast": 0.0005},
            }
        )

        self.assertFalse(catalog["providers"]["local"]["is_cloud_gpu_provider"])
        self.assertEqual(catalog["providers"]["local"]["support_contract"]["highest_support_level"], "production_route")
        self.assertTrue(catalog["providers"]["runpod"]["is_cloud_gpu_provider"])
        self.assertTrue(catalog["providers"]["vast"]["is_cloud_gpu_provider"])
        self.assertEqual(catalog["providers"]["runpod"]["support_contract"]["highest_support_level"], "canary_executable")
        self.assertEqual(catalog["providers"]["vast"]["support_contract"]["highest_support_level"], "canary_executable")
        self.assertFalse(catalog["providers"]["runpod"]["support_contract"]["levels"]["production_route"])
        self.assertFalse(catalog["providers"]["vast"]["support_contract"]["levels"]["production_route"])

    def test_workload_plan_is_deterministic_for_same_input(self) -> None:
        request = _whisper_request()

        first = plan_workload(request)
        second = plan_workload(request)

        self.assertTrue(first["ok"])
        self.assertEqual(first["plan"]["plan_id"], second["plan"]["plan_id"])
        self.assertTrue(first["plan"]["catalog_snapshot_id"].startswith("cat-"))
        self.assertTrue((Path(self.tmp.name) / "gpu-job-control" / "catalog" / f"{first['plan']['catalog_snapshot_id']}.json").is_file())
        self.assertEqual(first["plan"]["request"]["workload_kind"], "transcription.whisper")
        self.assertEqual(first["plan"]["selected_option"]["job_type"], "asr")

    def test_workload_to_workflow_creates_whisper_scatter_gather(self) -> None:
        workflow = workload_to_workflow(_whisper_request())

        self.assertEqual(workflow["workflow_type"], "transcription_whisper")
        self.assertEqual(workflow["strategy"]["splitter"], "ffmpeg_time_splitter")
        self.assertEqual(workflow["strategy"]["reducer"], "timeline_reducer")
        self.assertEqual(workflow["job_template"]["job_type"], "asr")
        self.assertEqual(workflow["job_template"]["gpu_profile"], "asr_fast")

    def test_public_contract_schemas_expose_required_artifacts_and_failures(self) -> None:
        artifact = artifact_manifest_schema()
        taxonomy = failure_taxonomy()

        self.assertIn("manifest.json", artifact["required_files"])
        self.assertIn("context_overflow", taxonomy["classes"])

    def test_provider_native_failure_mapping(self) -> None:
        result = classify_error("ImportError: Loading an AWQ quantized model requires gptqmodel", provider="modal")

        self.assertEqual(result["class"], "image_missing_dependency")
        self.assertFalse(result["retryable"])

    def test_provider_probe_records_signal(self) -> None:
        result = probe_provider("local")
        summary = recent_probe_summary()
        catalog = build_provider_catalog({"provider_limits": {"local": {"*": 1}}, "provider_price_usd_per_second": {"local": 0}})

        self.assertTrue(result["ok"])
        self.assertIn("local", summary["latest"])
        self.assertGreaterEqual(summary["stats"]["local"]["probe_runtime_seconds"]["count"], 1)
        self.assertGreaterEqual(catalog["providers"]["local"]["observed"]["probe_runtime_seconds"]["count"], 1)

    def test_advance_workflow_expands_split_segments_to_asr_map_jobs(self) -> None:
        workflow = workload_to_workflow(_whisper_request())
        workflow["workflow_id"] = "wf-advance-test"
        executed = execute_workflow(workflow)
        self.assertTrue(executed["ok"])

        store = JobStore()
        split_jobs = [
            job
            for job in store.list_jobs(limit=100)
            if job.metadata.get("workflow_id") == "wf-advance-test" and job.metadata.get("workflow_stage") == "split"
        ]
        self.assertEqual(len(split_jobs), 1)
        split = split_jobs[0]
        split.status = "succeeded"
        artifact_dir = store.artifact_dir(split.job_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "result.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "action": "ffmpeg_time_splitter",
                    "segments": [
                        {"index": 0, "path": "/tmp/seg-0.wav"},
                        {"index": 1, "path": "/tmp/seg-1.wav"},
                    ],
                }
            )
            + "\n"
        )
        store.save(split)

        advanced = advance_workflows()
        status = load_workflow("wf-advance-test")["workflow"]

        self.assertEqual(advanced["advanced_count"], 1)
        stages = [child["stage"] for child in status["summary"]["children"]]
        self.assertEqual(stages.count("map"), 2)
        map_jobs = [
            job
            for job in store.list_jobs(limit=100)
            if job.metadata.get("workflow_id") == "wf-advance-test" and job.metadata.get("workflow_stage") == "map"
        ]
        self.assertEqual(len(map_jobs), 2)
        self.assertEqual(map_jobs[0].job_type, "asr")

    def test_advance_workflow_queues_reducer_after_map_artifacts_succeed(self) -> None:
        workflow = workload_to_workflow(_whisper_request())
        workflow["workflow_id"] = "wf-reduce-test"
        self.assertTrue(execute_workflow(workflow)["ok"])
        store = JobStore()

        split = _workflow_jobs(store, "wf-reduce-test", "split")[0]
        split.status = "succeeded"
        split_artifact = store.artifact_dir(split.job_id)
        split_artifact.mkdir(parents=True, exist_ok=True)
        (split_artifact / "result.json").write_text(
            json.dumps({"segments": [{"index": 0, "path": "/tmp/a.wav"}, {"index": 1, "path": "/tmp/b.wav"}]}) + "\n"
        )
        store.save(split)
        self.assertEqual(advance_workflows()["advanced_count"], 1)

        for job in _workflow_jobs(store, "wf-reduce-test", "map"):
            index = int(job.metadata.get("workflow_chunk_index") or 0)
            job.status = "succeeded"
            artifact = store.artifact_dir(job.job_id)
            artifact.mkdir(parents=True, exist_ok=True)
            (artifact / "result.json").write_text(json.dumps({"text": f"transcript {index}"}) + "\n")
            store.save(job)

        advanced = advance_workflows()
        reducers = _workflow_jobs(store, "wf-reduce-test", "reduce")

        self.assertEqual(advanced["advanced_count"], 1)
        self.assertEqual(len(reducers), 1)
        items = reducers[0].metadata["input"]["items"]
        self.assertEqual([item["text"] for item in items], ["transcript 0", "transcript 1"])


def _workflow_jobs(store: JobStore, workflow_id: str, stage: str) -> list[Job]:
    return [
        job
        for job in store.list_jobs(limit=100)
        if job.metadata.get("workflow_id") == workflow_id and job.metadata.get("workflow_stage") == stage
    ]


def _whisper_request() -> dict:
    return {
        "workload_kind": "transcription.whisper",
        "request_id": "req-whisper-test",
        "inputs": [{"uri": "/tmp/audio.wav", "duration_seconds": 1200}],
        "requirements": {"max_cost_usd": 5.0},
        "hints": {"language": "ja", "gpu_profile": "asr_fast"},
        "business_context": {"budget_class": "standard", "app_id": "media-system"},
    }


if __name__ == "__main__":
    unittest.main()
