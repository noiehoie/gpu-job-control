from __future__ import annotations

import os
import unittest
from tempfile import TemporaryDirectory

from gpu_job.workflow import (
    approve_workflow,
    cost_drift_decision,
    enforce_workflow_budget_drains,
    execute_workflow,
    list_workflows,
    load_workflow,
    merge_json_array_results,
    plan_workflow,
    submit_bulk_workflow,
    workflow_budget_monitor,
    workflow_strategies,
)


class WorkflowPlannerTest(unittest.TestCase):
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

    def test_bulk_workflow_enqueues_children_with_business_context(self) -> None:
        result = submit_bulk_workflow(
            {
                "workflow_type": "bulk_test",
                "business_context": {
                    "app_id": "news-system",
                    "budget_class": "daily_news_core",
                    "priority": "high",
                },
                "jobs": [_job_payload("a"), _job_payload("b")],
            }
        )

        self.assertTrue(result["ok"])
        workflow = result["workflow"]
        self.assertEqual(workflow["total_jobs"], 2)
        self.assertEqual(workflow["expected_budget_class"], "daily_news_core")
        self.assertEqual(workflow["summary"]["counts"], {"queued": 2})
        self.assertEqual(workflow["summary"]["children"][0]["stage"], "map")

    def test_json_array_plan_auto_executes_under_budget(self) -> None:
        result = plan_workflow(_scatter_payload(item_count=6, item_tokens=2000, budget_class="daily_news_core"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["approval"]["decision"], "auto_execute")
        self.assertGreaterEqual(result["plan"]["chunk_count"], 1)
        self.assertLessEqual(result["estimate"]["estimated_cost_p95_usd"], result["estimate"]["auto_approve_cap_usd"])

    def test_json_array_plan_rejects_above_hard_cap(self) -> None:
        payload = _scatter_payload(item_count=50, item_tokens=20000, budget_class="batch_low_cost")
        payload["strategy"]["estimated_map_seconds"] = 3600

        result = plan_workflow(payload)

        self.assertFalse(result["ok"])
        self.assertEqual(result["approval"]["decision"], "reject")

    def test_execute_pending_approval_then_approve(self) -> None:
        payload = _scatter_payload(item_count=10, item_tokens=4000, budget_class="batch_low_cost")
        payload["strategy"]["estimated_map_seconds"] = 300

        result = execute_workflow(payload)

        self.assertTrue(result["ok"])
        workflow = result["workflow"]
        self.assertEqual(workflow["status"], "pending_approval")
        approved = approve_workflow(workflow["workflow_id"], principal="tester", reason="within manual budget")
        self.assertTrue(approved["ok"])
        self.assertEqual(approved["workflow"]["status"], "approved")

    def test_execute_workflow_splits_and_queues_jobs(self) -> None:
        payload = _scatter_payload(item_count=9, item_tokens=6000, budget_class="daily_news_core")
        result = execute_workflow(payload)

        self.assertTrue(result["ok"])
        workflow = load_workflow(result["workflow_id"])["workflow"]
        self.assertEqual(workflow["status"], "queued")
        self.assertEqual(workflow["summary"]["counts"]["queued"], workflow["plan"]["map_job_count"])

    def test_cost_drift_requests_drain_when_projected_above_hard_cap(self) -> None:
        decision = cost_drift_decision(
            {
                "workflow_id": "wf-test",
                "plan": {"map_job_count": 4},
                "estimate": {"estimated_cost_p95_usd": 4.0},
                "approval": {"effective_hard_cap_usd": 2.5},
            },
            {"total_jobs": 4, "counts": {"succeeded": 1}, "actual_cost_usd": 1.0},
        )

        self.assertFalse(decision["ok"])
        self.assertEqual(decision["action"], "drain")

    def test_budget_drain_cancels_queued_children(self) -> None:
        result = submit_bulk_workflow(
            {
                "workflow_id": "wf-drain-test",
                "workflow_type": "bulk_test",
                "jobs": [_job_payload("a"), _job_payload("b")],
            }
        )
        workflow = load_workflow(result["workflow_id"])["workflow"]
        workflow["plan"] = {"map_job_count": 2}
        workflow["estimate"] = {"estimated_cost_p95_usd": 10.0}
        workflow["approval"] = {"effective_hard_cap_usd": 1.0}
        from gpu_job.workflow import _save_manifest

        _save_manifest(workflow)
        drained = enforce_workflow_budget_drains()

        self.assertEqual(drained["drained_count"], 1)
        status = load_workflow("wf-drain-test")["workflow"]
        self.assertEqual(status["status"], "draining")

    def test_non_text_strategies_are_registered_as_worker_plugins(self) -> None:
        strategies = workflow_strategies()
        self.assertFalse(strategies["ffmpeg_time_splitter"]["runs_in_api"])
        self.assertEqual(strategies["ffmpeg_time_splitter"]["worker_job_type"], "cpu_workflow_helper")
        self.assertFalse(strategies["pdf_page_splitter"]["runs_in_api"])

    def test_json_array_merger(self) -> None:
        result = merge_json_array_results([[{"a": 1}], {"items": [{"b": 2}]}, {"c": 3}])
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 3)

    def test_list_workflows(self) -> None:
        submit_bulk_workflow({"workflow_type": "bulk_test", "jobs": [_job_payload("a")]})
        listed = list_workflows()
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["count"], 1)

    def test_workflow_budget_monitor_reports_totals(self) -> None:
        submit_bulk_workflow({"workflow_type": "bulk_test", "jobs": [_job_payload("a")]})
        monitor = workflow_budget_monitor()

        self.assertTrue(monitor["ok"])
        self.assertEqual(monitor["totals"]["workflow_count"], 1)
        self.assertIn("events_path", monitor)


def _job_payload(suffix: str) -> dict:
    return {
        "job_id": f"workflow-child-{suffix}",
        "job_type": "llm_heavy",
        "input_uri": f"text://{suffix}",
        "output_uri": f"local://{suffix}",
        "worker_image": "auto",
        "gpu_profile": "llm_heavy",
        "model": "local-deterministic-llm",
        "metadata": {"input": {"prompt": suffix}},
    }


def _scatter_payload(*, item_count: int, item_tokens: int, budget_class: str) -> dict:
    return {
        "workflow_type": "topic_ranking",
        "strategy": {
            "splitter": "json_array_chunker",
            "reducer": "json_array_merger",
            "target_chunk_tokens": 24000,
            "estimated_map_seconds": 30,
        },
        "business_context": {
            "app_id": "news-system",
            "budget_class": budget_class,
            "priority": "high",
            "sla_target_minutes": 30,
            "fallback_allowed": True,
        },
        "provider": "modal",
        "input_payload": {
            "items": [{"article_id": index, "estimated_tokens": item_tokens} for index in range(item_count)],
        },
        "job_template": {
            "job_type": "llm_heavy",
            "input_uri": "workflow://topic-ranking",
            "output_uri": "workflow://topic-ranking/out",
            "worker_image": "modal:qwen2.5",
            "gpu_profile": "llm_heavy",
            "model": "claude-sonnet-4-6",
            "limits": {"max_runtime_minutes": 30, "max_cost_usd": 8},
            "metadata": {"routing": {"max_input_tokens": 32768}},
        },
    }


if __name__ == "__main__":
    unittest.main()
