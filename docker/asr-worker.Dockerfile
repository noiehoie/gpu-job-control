FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-pip ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/gpu-job-control
COPY pyproject.toml README.md ./
COPY src ./src

RUN python3 -m venv /opt/gpu-job-venv \
    && /opt/gpu-job-venv/bin/pip install --no-cache-dir . faster-whisper==1.2.1 \
    && useradd --create-home --shell /usr/sbin/nologin worker \
    && chown -R worker:worker /opt/gpu-job-control

ENV PATH="/opt/gpu-job-venv/bin:$PATH"
USER worker
ENTRYPOINT ["gpu-job-asr-worker"]
