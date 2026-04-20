from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gpu_job.contracts import plan_workload, workload_to_workflow
from gpu_job.error_class import classify_error
from gpu_job.models import Job
from gpu_job.store import JobStore
from gpu_job.router import route_explanation
from gpu_job.secrets_policy import secret_check
from gpu_job.workers.asr import assign_speakers_to_segments, normalize_faster_whisper_model, probe_runtime, render_srt, write_artifacts
from gpu_job.workers.asr import build_parser, prepare_diarization_audio, run_asr
from gpu_job.workflow import _asr_reduce_item, _job_from_segment, execute_workflow, plan_workflow


class AsrDiarizationContractTest(unittest.TestCase):
    def test_assign_speakers_by_max_overlap(self) -> None:
        segments = [
            {"id": 0, "start": 0.0, "end": 2.0, "text": "こんにちは"},
            {"id": 1, "start": 2.0, "end": 5.0, "text": "質疑です"},
            {"id": 2, "start": 6.0, "end": 7.0, "text": "不明"},
        ]
        speaker_segments = [
            {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
            {"start": 1.0, "end": 5.0, "speaker": "SPEAKER_01"},
        ]

        assigned = assign_speakers_to_segments(segments, speaker_segments)

        self.assertEqual([item["speaker"] for item in assigned], ["SPEAKER_00", "SPEAKER_01", ""])

    def test_render_srt_includes_speaker_label(self) -> None:
        srt = render_srt([{"start": 1.25, "end": 2.5, "speaker": "SPEAKER_00", "text": "本文"}])

        self.assertIn("00:00:01,250 --> 00:00:02,500", srt)
        self.assertIn("SPEAKER_00: 本文", srt)

    def test_write_artifacts_fails_when_requested_diarization_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            result = {
                "text": "本文",
                "duration_seconds": 10,
                "segments": [{"start": 0, "end": 1, "text": "本文"}],
                "diarization_requested": True,
                "diarization_error": "speaker diarization requires HF_TOKEN",
                "speaker_segments": [],
            }

            verify = write_artifacts(Path(tmp), result, {"job_id": "asr-test"})

            self.assertFalse(verify["ok"])
            self.assertFalse(verify["checks"]["diarization_ok"])
            self.assertTrue((Path(tmp) / "transcript.srt").is_file())
            self.assertTrue((Path(tmp) / "speaker_timeline.json").is_file())
            probe_info = json.loads((Path(tmp) / "probe_info.json").read_text())
            self.assertEqual(probe_info["worker_image"], "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4")
            self.assertFalse(probe_info["cache_hit"])

    def test_probe_runtime_requires_image_contract_marker_for_diarization_cache_hit(self) -> None:
        with (
            patch("gpu_job.workers.asr.shutil.which", return_value="/usr/bin/tool"),
            patch("gpu_job.workers.asr.Path.is_file", return_value=False),
            patch.dict("sys.modules", {"faster_whisper": object(), "pyannote.audio": object(), "matplotlib": object()}),
        ):
            result = probe_runtime(diarize=True)

        self.assertFalse(result["checks"]["image_contract_marker_present"])
        self.assertFalse(result["cache_hit"])

    def test_prepare_diarization_audio_uses_ffmpeg_wav_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            output = Path(tmp) / "diarization.wav"
            source.write_bytes(b"fake mp4")

            def fake_run(cmd, **kwargs):
                output.write_bytes(b"RIFF" + b"\0" * 128)
                self.assertIn("-vn", cmd)
                self.assertIn("-ac", cmd)
                self.assertIn("1", cmd)
                self.assertIn("-ar", cmd)
                self.assertIn("16000", cmd)
                self.assertEqual(cmd[-1], str(output))
                return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with patch("gpu_job.workers.asr.subprocess.run", side_effect=fake_run):
                result = prepare_diarization_audio(source, output)

            self.assertEqual(result, output)

    def test_run_asr_preflights_missing_hf_token_before_transcription(self) -> None:
        with TemporaryDirectory() as tmp:
            old_values = {key: os.environ.pop(key, None) for key in ("HF_TOKEN", "HUGGINGFACE_TOKEN", "HUGGING_FACE_HUB_TOKEN")}
            try:
                media = Path(tmp) / "dummy.wav"
                media.write_bytes(b"not a real wav; should not reach faster-whisper")
                artifact = Path(tmp) / "artifacts"
                args = build_parser().parse_args(
                    [
                        "--job-id",
                        "asr-preflight",
                        "--artifact-dir",
                        str(artifact),
                        "--gpu-profile",
                        "asr_fast",
                        "--input-uri",
                        str(media),
                        "--diarize",
                    ]
                )

                exit_code = run_asr(args)

                result = json.loads((artifact / "result.json").read_text())
                verify = json.loads((artifact / "verify.json").read_text())
                self.assertEqual(exit_code, 1)
                self.assertIn("HF_TOKEN", result["error"])
                self.assertFalse(verify["ok"])
                self.assertFalse(verify["checks"]["diarization_ok"])
            finally:
                for key, value in old_values.items():
                    if value is not None:
                        os.environ[key] = value

    def test_workload_to_workflow_propagates_speaker_diarization(self) -> None:
        workflow = workload_to_workflow(
            {
                "workload_kind": "transcription.whisper",
                "request_id": "req-diarize",
                "inputs": [{"uri": "/tmp/video.mp4", "duration_seconds": 3600}],
                "requirements": {"max_cost_usd": 20, "speaker_diarization": True},
                "hints": {"language": "ja", "speaker_model": "pyannote/speaker-diarization-3.1"},
                "business_context": {"app_id": "media-system", "budget_class": "standard"},
            }
        )

        input_payload = workflow["job_template"]["metadata"]["input"]
        model_requirements = workflow["job_template"]["metadata"]["model_requirements"]
        self.assertTrue(input_payload["diarize"])
        self.assertTrue(input_payload["speaker_diarization"])
        self.assertEqual(input_payload["speaker_model"], "pyannote/speaker-diarization-3.1")
        self.assertTrue(model_requirements["speaker_diarization"])
        self.assertEqual(workflow["job_template"]["gpu_profile"], "asr_diarization")
        self.assertEqual(workflow["job_template"]["model"], "large-v3")
        self.assertEqual(workflow["job_template"]["metadata"]["secret_refs"], ["hf_token"])

    def test_whisper_public_model_alias_maps_to_faster_whisper_model_id(self) -> None:
        self.assertEqual(normalize_faster_whisper_model("whisper-large-v3"), "large-v3")
        workflow = workload_to_workflow(
            {
                "workload_kind": "transcription.whisper",
                "request_id": "req-model-alias",
                "inputs": [{"uri": "/tmp/video.mp4", "duration_seconds": 60}],
                "requirements": {"model": "openai/whisper-large-v3"},
                "hints": {"language": "ja"},
                "business_context": {"app_id": "media-system"},
            }
        )

        self.assertEqual(workflow["job_template"]["model"], "large-v3")

    def test_plan_workload_returns_requires_action_for_diarization_prerequisites(self) -> None:
        with TemporaryDirectory() as tmp:
            old_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = tmp
            try:
                result = plan_workload(
                    {
                        "workload_kind": "transcription.whisper",
                        "request_id": "req-plan-action",
                        "inputs": [{"uri": "/tmp/video.mp4", "duration_seconds": 5250}],
                        "requirements": {"speaker_diarization": True, "max_cost_usd": 25},
                        "hints": {"language": "ja"},
                        "business_context": {"app_id": "media-system", "budget_class": "critical"},
                    }
                )

                plan = result["plan"]
                action = plan["action_requirements"]
                self.assertTrue(result["ok"])
                self.assertFalse(plan["can_run_now"])
                self.assertEqual(plan["gpu_profile"], "asr_diarization")
                self.assertEqual(plan["approval"]["decision"], "requires_action")
                self.assertEqual(action["decision"], "requires_action")
                self.assertIn("authorize_secret", [item["action"] for item in action["required_actions"]])
                self.assertIn("run_contract_probe", [item["action"] for item in action["required_actions"]])
                worker_dependencies = {item.get("id") for item in action["requirements"] if item.get("type") == "worker_dependency"}
                self.assertIn("pyannote.audio", worker_dependencies)
                self.assertIn("matplotlib", worker_dependencies)
            finally:
                if old_home is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_home

    def test_torchcodec_missing_is_classified_as_image_dependency(self) -> None:
        result = classify_error(
            "TorchCodec is required for load_with_torchcodec. Please install torchcodec.",
            provider="modal",
        )

        self.assertEqual(result["class"], "image_missing_dependency")
        self.assertFalse(result["retryable"])

    def test_torchcodec_ffmpeg_lib_missing_is_classified_as_image_dependency(self) -> None:
        result = classify_error("OSError: libavutil.so.58: cannot open shared object file", provider="modal")

        self.assertEqual(result["class"], "image_missing_dependency")
        self.assertFalse(result["retryable"])

    def test_missing_torchaudio_info_is_classified_as_image_dependency(self) -> None:
        result = classify_error("module 'torchaudio' has no attribute 'info'", provider="modal")

        self.assertEqual(result["class"], "image_missing_dependency")
        self.assertFalse(result["retryable"])

    def test_missing_matplotlib_is_classified_as_image_dependency(self) -> None:
        result = classify_error("No module named 'matplotlib'", provider="vast")

        self.assertEqual(result["class"], "image_missing_dependency")
        self.assertFalse(result["retryable"])

    def test_plan_and_execute_workflow_stop_at_requires_action(self) -> None:
        with TemporaryDirectory() as tmp:
            old_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = tmp
            try:
                workflow = workload_to_workflow(
                    {
                        "workload_kind": "transcription.whisper",
                        "request_id": "req-workflow-action",
                        "inputs": [{"uri": "/tmp/video.mp4", "duration_seconds": 5250}],
                        "requirements": {"speaker_diarization": True, "max_cost_usd": 25},
                        "hints": {"language": "ja"},
                        "business_context": {"app_id": "media-system", "budget_class": "critical"},
                    }
                )

                planned = plan_workflow(workflow)
                executed = execute_workflow(workflow)

                self.assertTrue(planned["ok"])
                self.assertEqual(planned["approval"]["decision"], "requires_action")
                self.assertFalse(planned["plan"]["can_run_now"])
                self.assertTrue(executed["ok"])
                self.assertEqual(executed["workflow"]["status"], "requires_action")
                self.assertEqual(executed["workflow"]["summary"]["counts"], {})
            finally:
                if old_home is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_home

    def test_diarization_secret_ref_is_blocked_until_policy_allows_it(self) -> None:
        workflow = workload_to_workflow(
            {
                "workload_kind": "transcription.whisper",
                "request_id": "req-diarize-secret",
                "inputs": [{"uri": "/tmp/video.mp4", "duration_seconds": 60}],
                "requirements": {"speaker_diarization": True},
                "hints": {"language": "ja"},
                "business_context": {"app_id": "media-system"},
            }
        )
        job = Job.from_dict(workflow["job_template"])

        denied = secret_check(job, "modal", policy={"secret_policy": {"allowed_refs": {"*:*:*": []}}})
        allowed = secret_check(job, "modal", policy={"secret_policy": {"allowed_refs": {"modal:*:asr": ["hf_token"]}}})

        self.assertFalse(denied["ok"])
        self.assertEqual(denied["denied_secret_refs"], ["hf_token"])
        self.assertTrue(allowed["ok"])

    def test_contract_probe_secret_scope_allows_runpod_hf_token_without_default_wildcard(self) -> None:
        job = Job.from_dict(
            {
                "job_id": "contract-probe-secret",
                "job_type": "asr",
                "input_uri": "text://probe",
                "output_uri": "local://probe",
                "worker_image": "gpu-job/asr-diarization-worker:large-v3-pyannote3.3.2-cuda12.4",
                "gpu_profile": "asr_diarization",
                "provider": "runpod",
                "metadata": {"source_system": "contract-probe", "secret_refs": ["hf_token"]},
            }
        )

        allowed = secret_check(job, "runpod")
        denied = secret_check(
            Job.from_dict({**job.to_dict(), "metadata": {"source_system": "default", "secret_refs": ["hf_token"]}}),
            "runpod",
        )

        self.assertTrue(allowed["ok"])
        self.assertEqual(allowed["scope"], "runpod:contract-probe:asr")
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["scope"], "runpod:default:asr")

    def test_workflow_child_jobs_inherit_app_id_for_secret_scope(self) -> None:
        workflow = workload_to_workflow(
            {
                "workload_kind": "transcription.whisper",
                "request_id": "req-diarize-child-secret",
                "inputs": [{"uri": "/tmp/video.mp4", "duration_seconds": 600}],
                "requirements": {"speaker_diarization": True},
                "hints": {"language": "ja"},
                "business_context": {"app_id": "media-system", "budget_class": "critical"},
            }
        )

        job = _job_from_segment(
            workflow["job_template"],
            {"path": "/tmp/segment-00000.mp4", "start_seconds": 0, "end_seconds": 600},
            workflow_id="wf-asr-secret",
            index=0,
            business_context=workflow["business_context"],
        )
        allowed = secret_check(job, "modal", policy={"secret_policy": {"allowed_refs": {"modal:media-system:asr": ["hf_token"]}}})

        self.assertEqual(job.metadata["source_system"], "media-system")
        self.assertEqual(allowed["scope"], "modal:media-system:asr")
        self.assertTrue(allowed["ok"])

    def test_route_explanation_is_rendered_from_deterministic_route_json(self) -> None:
        explanation = route_explanation(
            {
                "gpu_profile": "asr_diarization",
                "selected_provider": "modal",
                "candidates": ["modal", "runpod"],
                "eligible_ranked": [{"provider": "modal", "score": 120.5}],
                "decision": {"reason": "workload estimate accepted"},
            }
        )

        self.assertIn("selected provider 'modal'", explanation)
        self.assertIn("gpu_profile 'asr_diarization'", explanation)
        self.assertIn("score=120.5", explanation)
        self.assertIn("workload estimate accepted", explanation)

    def test_asr_reduce_item_offsets_chunk_segments_to_absolute_timeline(self) -> None:
        with TemporaryDirectory() as tmp:
            old_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = tmp
            try:
                store = JobStore()
                job = Job.from_dict(
                    {
                        "job_id": "asr-wf-map-1",
                        "job_type": "asr",
                        "input_uri": "/tmp/seg.mp4",
                        "output_uri": "workflow://out",
                        "worker_image": "auto",
                        "gpu_profile": "asr_fast",
                        "metadata": {
                            "workflow_chunk_index": 1,
                            "input": {"segment": {"start_seconds": 600, "end_seconds": 1200}},
                        },
                    }
                )
                artifact = store.artifact_dir(job.job_id)
                artifact.mkdir(parents=True, exist_ok=True)
                (artifact / "result.json").write_text(
                    json.dumps(
                        {
                            "text": "chunk",
                            "segments": [{"start": 1.0, "end": 2.0, "text": "chunk", "speaker": "SPEAKER_00"}],
                            "speaker_segments": [{"start": 0.5, "end": 2.5, "speaker": "SPEAKER_00"}],
                        }
                    )
                    + "\n"
                )

                reduced = _asr_reduce_item(store, job)

                self.assertEqual(reduced["start_seconds"], 600.0)
                self.assertEqual(reduced["result"]["segments"][0]["start"], 601.0)
                self.assertEqual(reduced["result"]["speaker_segments"][0]["end"], 602.5)
            finally:
                if old_home is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
