FROM ghcr.io/astral-sh/uv:0.8.22 AS uv
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY --from=uv /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY src/gpu_job/workers/runpod_llm.py /app/runpod_llm.py

RUN uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python runpod==1.9.0 \
    && mkdir -p /opt/gpu-job-control/image-contracts \
    && printf '%s\n' '{"contract_id":"runpod-serverless-heartbeat-python3.12","prebuilt_dependencies":["runpod==1.9.0"],"runtime_dependency_install_allowed":false,"serverless_handler":"runpod_llm:handler"}' > /opt/gpu-job-control/image-contracts/runpod-serverless-heartbeat-python3.12.json \
    && useradd --create-home --shell /usr/sbin/nologin worker \
    && chown -R worker:worker /app /opt/gpu-job-control

ENV PATH="/opt/venv/bin:$PATH"

USER worker
CMD ["python", "-u", "/app/runpod_llm.py"]
