FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3.11-dev \
        build-essential ffmpeg libsndfile1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/gpu-job-control
COPY pyproject.toml README.md ./
COPY src ./src

RUN python3.11 -m venv /opt/gpu-job-venv \
    && /opt/gpu-job-venv/bin/pip install --no-cache-dir .[providers] \
        torch==2.5.1 torchaudio==2.5.1 \
        huggingface-hub==0.24.7 \
        faster-whisper==1.2.1 pyannote.audio==3.3.2 matplotlib==3.10.1 \
    && mkdir -p /opt/gpu-job-control/image-contracts \
    && printf '%s\n' '{"contract_id":"asr-diarization-large-v3-pyannote3.3.2-cuda12.4","prebuilt_dependencies":["faster_whisper==1.2.1","pyannote.audio==3.3.2","matplotlib==3.10.1"],"runtime_dependency_install_allowed":false}' > /opt/gpu-job-control/image-contracts/asr-diarization-large-v3-pyannote3.3.2-cuda12.4.json \
    && printf '%s\n' '{"contract_id":"asr-diarization-runpod-serverless-large-v3-pyannote3.3.2-cuda12.4","prebuilt_dependencies":["runpod==1.9.0","faster_whisper==1.2.1","pyannote.audio==3.3.2","matplotlib==3.10.1"],"runtime_dependency_install_allowed":false,"serverless_handler":"gpu_job.workers.runpod_asr:handler"}' > /opt/gpu-job-control/image-contracts/asr-diarization-runpod-serverless-large-v3-pyannote3.3.2-cuda12.4.json \
    && useradd --create-home --shell /usr/sbin/nologin worker \
    && chown -R worker:worker /opt/gpu-job-control

ENV PATH="/opt/gpu-job-venv/bin:$PATH"
USER worker
CMD ["gpu-job-runpod-asr-worker"]
