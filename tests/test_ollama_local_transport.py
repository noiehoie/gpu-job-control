from __future__ import annotations

import gpu_job.public_ops as public_ops
from gpu_job.lanes import resolve_lane_id
from gpu_job.public_ops import plan_public_job, submit_public_job


def _job_dict() -> dict:
    return {
        "job_type": "smoke",
        "input_uri": "text://transport-test",
        "output_uri": "local://transport-test",
        "worker_image": "local/canary:latest",
        "gpu_profile": "llm_heavy",
        "metadata": {},
    }


def test_resolve_lane_id_returns_empty_for_no_lane_providers() -> None:
    assert resolve_lane_id("ollama") == ""
    assert resolve_lane_id("local") == ""
    assert resolve_lane_id("modal") == "modal_function"


def test_plan_public_job_ollama_fallback_to_provider_plan() -> None:
    # Ollama and Local don't have lanes in DEFAULT_LANE_BY_PROVIDER
    result = plan_public_job(_job_dict(), provider="ollama")
    assert result["ok"] is True
    assert result["selected_provider"] == "ollama"
    assert result["selected_lane_id"] == ""
    assert "plan" in result
    # Verify it actually contains a plan from OllamaProvider
    assert result["plan"]["provider"] == "ollama"


def test_plan_public_job_local_fallback_to_provider_plan() -> None:
    result = plan_public_job(_job_dict(), provider="local")
    assert result["ok"] is True
    assert result["selected_provider"] == "local"
    assert result["selected_lane_id"] == ""
    assert "plan" in result
    assert result["plan"]["provider"] == "local"


def test_submit_public_job_ollama_transport_no_lane(monkeypatch) -> None:
    def _stub_submit_job(job, provider_name="auto", execute=False):
        return {"ok": True, "job_id": job.job_id, "provider": provider_name}

    monkeypatch.setattr(public_ops, "submit_job", _stub_submit_job)

    result = submit_public_job(_job_dict(), provider="ollama")
    assert result["ok"] is True
    assert result["selected_provider"] == "ollama"
    assert result["selected_lane_id"] == ""
    assert result["provider"] == "ollama"


def test_submit_public_job_local_transport_no_lane(monkeypatch) -> None:
    def _stub_submit_job(job, provider_name="auto", execute=False):
        return {"ok": True, "job_id": job.job_id, "provider": provider_name}

    monkeypatch.setattr(public_ops, "submit_job", _stub_submit_job)

    result = submit_public_job(_job_dict(), provider="local")
    assert result["ok"] is True
    assert result["selected_provider"] == "local"
    assert result["selected_lane_id"] == ""
    assert result["provider"] == "local"
