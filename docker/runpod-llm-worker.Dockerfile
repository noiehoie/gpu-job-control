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
    && useradd --create-home --shell /usr/sbin/nologin worker \
    && chown -R worker:worker /app

ENV PATH="/opt/venv/bin:$PATH"

USER worker
CMD ["python", "-u", "/app/runpod_llm.py"]
