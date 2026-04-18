from __future__ import annotations

from typing import Any

from .capacity import reserve_direct_execution_slot
from .audit import append_audit
from .capabilities import evaluate_model_capability
from .circuit import provider_circuit_state
from .compliance import evaluate_compliance
from .cost import cost_estimate
from .decision import make_decision
from .error_class import classify_error
from .guard import collect_cost_guard
from .manifest import write_manifest
from .models import Job
from .policy_engine import validate_policy
from .placement import placement_check
from .preemption import preemption_check
from .provenance import evaluate_provenance
from .providers import get_provider
from .quota import quota_check
from .router import route_job
from .secrets_policy import secret_check
from .store import JobStore
from .telemetry import ensure_trace
from .timeout import runtime_within_timeout, timeout_contract
from .wal import append_wal


def submit_job(
    job: Job,
    provider_name: str = "auto",
    execute: bool = False,
    enforce_capacity: bool = True,
    guard_provider_names: list[str] | None = None,
) -> dict[str, Any]:
    ensure_trace(job.metadata)
    route_result = route_job(job) if provider_name == "auto" else {"selected_provider": provider_name}
    selected = str(route_result["selected_provider"])
    provider = get_provider(selected)
    store = JobStore()
    policy_validation = validate_policy()
    if not policy_validation["ok"]:
        job.status = "failed"
        job.error = "policy validation failed"
        job.metadata["error_class"] = classify_error(job.error, provider=selected, context={"gate": "policy"})
        job.metadata["policy_validation"] = policy_validation
        store.save(job)
        append_audit("submit.blocked.policy", {"job_id": job.job_id, "policy_validation": policy_validation}, store=store)
        return {"ok": False, "error": job.error, "job": job.to_dict(), "path": str(store.job_path(job.job_id))}
    circuit = provider_circuit_state(selected, store=store)
    if not circuit["ok"]:
        if circuit.get("half_open_probe_allowed"):
            job.metadata["circuit_probe"] = circuit
            append_audit("submit.circuit.half_open_probe", {"job_id": job.job_id, "circuit": circuit}, store=store)
        else:
            job.status = "failed"
            job.error = f"provider circuit open: {selected}"
            job.metadata["error_class"] = classify_error(job.error, provider=selected, context={"gate": "circuit"})
            job.metadata["circuit"] = circuit
            store.save(job)
            append_audit("submit.blocked.circuit", {"job_id": job.job_id, "circuit": circuit}, store=store)
            return {"ok": False, "error": job.error, "job": job.to_dict(), "path": str(store.job_path(job.job_id))}
    if circuit["ok"]:
        job.metadata["circuit"] = circuit
    provenance = evaluate_provenance(job)
    job.metadata["selected_provider"] = selected
    compliance = evaluate_compliance(job)
    capability = evaluate_model_capability(job, selected)
    quota = quota_check(job, store=store)
    cost = cost_estimate(job)
    secrets = secret_check(job, selected)
    placement = placement_check(job, selected)
    preemption = preemption_check(job, store=store)
    timeout = timeout_contract(job)
    gates = [
        ("provenance", provenance),
        ("compliance", compliance),
        ("capability", capability),
        ("quota", quota),
        ("cost", cost),
        ("secret", secrets),
        ("placement", placement),
        ("preemption", preemption),
    ]
    if not all(item["ok"] for _, item in gates):
        job.status = "failed"
        failed_gate = next(name for name, item in gates if not item["ok"])
        job.error = f"{failed_gate} gate failed"
        job.metadata["error_class"] = classify_error(job.error, provider=selected, context={"gate": failed_gate})
        job.metadata["provenance_result"] = provenance
        job.metadata["compliance_result"] = compliance
        job.metadata["capability_result"] = capability
        job.metadata["quota_result"] = quota
        job.metadata["cost_result"] = cost
        job.metadata["secret_result"] = secrets
        job.metadata["placement_result"] = placement
        job.metadata["preemption_result"] = preemption
        store.save(job)
        append_audit(
            "submit.blocked.safety_gate",
            {"job_id": job.job_id, "failed_gate": failed_gate, "gates": {name: item for name, item in gates}},
            store=store,
        )
        return {"ok": False, "error": job.error, "job": job.to_dict(), "path": str(store.job_path(job.job_id))}
    if not timeout["ok"]:
        job.status = "failed"
        job.error = "timeout contract failed"
        job.metadata["error_class"] = classify_error(job.error, provider=selected, context={"gate": "timeout"})
        job.metadata["timeout_contract"] = timeout
        store.save(job)
        append_audit("submit.blocked.timeout", {"job_id": job.job_id, "timeout": timeout}, store=store)
        return {"ok": False, "error": job.error, "job": job.to_dict(), "path": str(store.job_path(job.job_id))}
    decision = make_decision(job, phase="submit", route_result=route_result, store=store)
    pre_guard = collect_cost_guard(provider_names=guard_provider_names) if execute else None
    if pre_guard and not pre_guard["ok"]:
        job.status = "failed"
        job.error = "pre-submit cost guard failed"
        job.metadata["error_class"] = classify_error(job.error, provider=selected, context={"gate": "billing_guard"})
        job.metadata["pre_submit_guard"] = pre_guard
        store.save(job)
        append_audit("submit.blocked.guard", {"job_id": job.job_id, "pre_submit_guard": pre_guard}, store=store)
        return {
            "ok": False,
            "error": job.error,
            "job": job.to_dict(),
            "path": str(store.job_path(job.job_id)),
            "pre_submit_guard": pre_guard,
            "post_submit_guard": None,
        }
    capacity = None
    if execute and enforce_capacity:
        append_wal(
            job, transition=f"{job.status}->starting", intent="reserve_direct_execution_slot", extra={"provider": selected}, store=store
        )
        capacity = reserve_direct_execution_slot(job, selected)
        if not capacity.get("ok"):
            job.provider = selected
            job.metadata["selected_provider"] = selected
            job.metadata["error_class"] = classify_error(
                capacity["error"], status_code=429, provider=selected, context={"gate": "capacity"}
            )
            return {
                "ok": False,
                "error": capacity["error"],
                "status_code": 429,
                "retry_after_seconds": capacity.get("retry_after_seconds", 30),
                "capacity": capacity,
                "job": job.to_dict(),
                "path": str(store.job_path(job.job_id)),
                "pre_submit_guard": pre_guard,
                "post_submit_guard": None,
            }

    saved = job
    submit_error = ""
    post_guard = None
    try:
        append_wal(
            job,
            transition=f"{job.status}->provider_submit",
            intent="provider_submit",
            extra={"provider": selected, "execute": execute},
            store=store,
        )
        saved = provider.submit(job, store=store, execute=execute)
    except Exception as exc:
        submit_error = str(exc)
        saved.status = "failed"
        saved.error = submit_error
        saved.metadata["error_class"] = classify_error(submit_error, provider=selected, context={"gate": "provider_submit"})
        saved.exit_code = 1
        store.save(saved)
    finally:
        if execute:
            post_guard = collect_cost_guard(provider_names=guard_provider_names)

    saved.metadata["pre_submit_guard"] = pre_guard
    saved.metadata["post_submit_guard"] = post_guard
    saved.metadata["decision_hash"] = decision.get("decision_hash")
    saved.metadata["policy_validation"] = policy_validation
    saved.metadata["provenance_result"] = provenance
    saved.metadata["compliance_result"] = compliance
    saved.metadata["capability_result"] = capability
    saved.metadata["quota_result"] = quota
    saved.metadata["cost_result"] = cost
    saved.metadata["secret_result"] = secrets
    saved.metadata["placement_result"] = placement
    saved.metadata["preemption_result"] = preemption
    saved.metadata["timeout_contract"] = timeout
    if capacity:
        saved.metadata["capacity_reservation"] = capacity
    if execute and saved.status == "planned":
        saved.status = "failed"
        saved.exit_code = saved.exit_code if saved.exit_code is not None else 2
        if not saved.error:
            saved.error = f"provider {selected} returned plan without executing"
        saved.metadata["error_class"] = classify_error(saved.error, provider=selected, context={"gate": "provider_submit"})
    if post_guard and not post_guard["ok"] and not saved.error:
        saved.error = "post-submit cost guard failed"
        saved.status = "failed"
        saved.metadata["error_class"] = classify_error(saved.error, provider=selected, context={"gate": "billing_guard"})
    timeout_result = runtime_within_timeout(saved, timeout)
    saved.metadata["timeout_result"] = timeout_result
    if not timeout_result["ok"]:
        saved.error = timeout_result["error"]
        saved.status = "failed"
        saved.exit_code = 124
        saved.metadata["error_class"] = classify_error(saved.error, provider=selected, context={"gate": "timeout"})
    store.save(saved)
    if execute and store.artifact_dir(saved.job_id).is_dir():
        try:
            write_manifest(store.artifact_dir(saved.job_id))
        except Exception as exc:
            saved.metadata["manifest_error"] = str(exc)
            store.save(saved)
    append_wal(
        saved,
        transition="provider_submit->final",
        intent="provider_submit_final",
        extra={"status": saved.status, "provider": selected},
        store=store,
    )
    append_audit(
        "submit.result",
        {"job_id": saved.job_id, "status": saved.status, "provider": selected, "decision_hash": decision.get("decision_hash")},
        store=store,
    )
    return {
        "ok": (saved.status == "succeeded" if execute else saved.status in {"planned", "succeeded"})
        and (not post_guard or post_guard["ok"]),
        "error": submit_error,
        "job": saved.to_dict(),
        "path": str(store.job_path(saved.job_id)),
        "pre_submit_guard": pre_guard,
        "post_submit_guard": post_guard,
    }
