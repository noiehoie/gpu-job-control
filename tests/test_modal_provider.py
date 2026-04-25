from __future__ import annotations

import unittest
from pathlib import Path

from gpu_job.models import Job
from gpu_job.providers.modal import ModalProvider


class ModalProviderCommandTest(unittest.TestCase):
    def test_asr_command_names_modal_entrypoint(self) -> None:
        job = Job(
            job_id="asr-command-test",
            job_type="asr",
            input_uri="/tmp/audio.mp4",
            output_uri="local://out",
            worker_image="auto",
            gpu_profile="asr_diarization",
            model="whisper-large-v3",
            metadata={"input": {"language": "ja", "diarize": True}},
        )

        command = ModalProvider()._command("/usr/bin/modal", job, Path("/tmp/artifacts"))

        self.assertEqual(command[0:2], ["/usr/bin/modal", "run"])
        self.assertIn("modal_asr.py::main", command[2])
        self.assertIn("--diarize", command)

    def test_asr_diarization_contract_probe_uses_canary_entrypoint(self) -> None:
        job = Job(
            job_id="asr-contract-probe",
            job_type="asr",
            input_uri="text://GPU_JOB_CONTRACT_PROBE_OK",
            output_uri="local://out",
            worker_image="gpu-job-modal-asr",
            gpu_profile="asr_diarization",
            model="pyannote/speaker-diarization-3.1",
            metadata={"contract_probe": {"probe_name": "modal.asr_diarization.pyannote"}},
        )

        command = ModalProvider()._command("/usr/bin/modal", job, Path("/tmp/artifacts"))

        self.assertEqual(command[0:2], ["/usr/bin/modal", "run"])
        self.assertIn("modal_asr.py::canary", command[2])
        self.assertIn("--speaker-model", command)
        self.assertNotIn("--input-uri", command)

    def test_gpu_task_command_names_generic_entrypoint(self) -> None:
        job = Job(
            job_id="gpu-task-command-test",
            job_type="gpu_task",
            input_uri="none://gpu-task",
            output_uri="local://out",
            worker_image="auto",
            gpu_profile="generic_gpu",
            metadata={"input": {"workload": {"kind": "container", "entrypoint": ["true"]}}},
        )

        command = ModalProvider()._command("/usr/bin/modal", job, Path("/tmp/artifacts"))

        self.assertEqual(command[0:2], ["/usr/bin/modal", "run"])
        self.assertIn("modal_gpu_task.py::main", command[2])


if __name__ == "__main__":
    unittest.main()
