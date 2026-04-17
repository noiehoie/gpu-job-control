from __future__ import annotations

from typing import Any

from .canonical import canonical_hash
from .models import Job


PROVENANCE_VERSION = "gpu-job-provenance-v1"


def evaluate_provenance(job: Job) -> dict[str, Any]:
    provenance = job.metadata.get("provenance", {})
    if not isinstance(provenance, dict):
        provenance = {}
    image_ref = str(job.worker_image or "")
    image_digest = str(provenance.get("image_digest") or "")
    model_hash = str(provenance.get("model_weights_hash") or "")
    requires_strict = bool(provenance.get("require_strict") or job.metadata.get("require_provenance"))
    requires_attestation = bool(provenance.get("require_attestation"))
    attestation = provenance.get("attestation")
    attestation = attestation if isinstance(attestation, dict) else {}
    image_pinned = "@sha256:" in image_ref or bool(image_digest)
    model_pinned = bool(model_hash) or not job.model
    attestation_result = (
        verify_attestation(job, attestation)
        if (requires_attestation or attestation)
        else {
            "ok": not requires_attestation,
            "required": requires_attestation,
            "reason": "attestation not required",
        }
    )
    ok = ((image_pinned and model_pinned) or not requires_strict) and bool(attestation_result.get("ok"))
    return {
        "ok": ok,
        "provenance_version": PROVENANCE_VERSION,
        "strict": requires_strict,
        "attestation_required": requires_attestation,
        "attestation": attestation_result,
        "image_pinned": image_pinned,
        "model_pinned": model_pinned,
        "image_digest": image_digest or None,
        "model_weights_hash": model_hash or None,
    }


def attestation_subject(job: Job) -> dict[str, Any]:
    return {
        "job_type": job.job_type,
        "worker_image": job.worker_image,
        "model": job.model,
        "gpu_profile": job.gpu_profile,
    }


def expected_attestation_hash(job: Job) -> str:
    return canonical_hash(attestation_subject(job))["sha256"]


def verify_attestation(job: Job, attestation: dict[str, Any]) -> dict[str, Any]:
    expected = expected_attestation_hash(job)
    actual = str(attestation.get("subject_sha256") or "")
    issuer = str(attestation.get("issuer") or "")
    ok = bool(actual) and actual == expected and bool(issuer)
    return {
        "ok": ok,
        "required": True,
        "issuer": issuer,
        "expected_subject_sha256": expected,
        "actual_subject_sha256": actual,
        "reason": "attestation accepted" if ok else "attestation subject hash or issuer missing/invalid",
    }
