from __future__ import annotations

from typing import Any
import uuid


TELEMETRY_SCHEMA_VERSION = "gpu-job-telemetry-v1"


def ensure_trace(metadata: dict[str, Any]) -> dict[str, Any]:
    trace = metadata.get("trace")
    if not isinstance(trace, dict):
        trace = {}
    trace.setdefault("trace_id", uuid.uuid4().hex)
    trace.setdefault("span_id", uuid.uuid4().hex[:16])
    trace.setdefault("telemetry_schema_version", TELEMETRY_SCHEMA_VERSION)
    metadata["trace"] = trace
    return trace


def metric_labels(job_data: dict[str, Any]) -> dict[str, str]:
    metadata = job_data.get("metadata") if isinstance(job_data.get("metadata"), dict) else {}
    return {
        "source_system": str(metadata.get("source_system") or "unknown"),
        "job_type": str(job_data.get("job_type") or "unknown"),
        "gpu_profile": str(job_data.get("gpu_profile") or "unknown"),
        "provider": str(job_data.get("provider") or metadata.get("selected_provider") or "unknown"),
        "status": str(job_data.get("status") or "unknown"),
    }
