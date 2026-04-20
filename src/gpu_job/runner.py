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
from .execution_record import write_execution_record
from .guard import collect_cost_guard
from .manifest import write_manifest
from .models import Job
from .policy_engine import validate_policy
from .placement import placement_check
from .preemption import preemption_check
from .provenance import evaluate_provenance
from .providers import get_provider
from .quota import quota_check
from .router import route_explanation, route_job
from .secrets_policy import secret_check
from .store import JobStore
from .telemetry import ensure_trace
from .timeout import runtime_within_timeout, timeout_contract
from .timing import ensure_received, enter_phase, exit_phase, instant_phase, terminal_phase_for_status
from .wal import append_wal
from .verify import verify_artifacts
from .workspace_registry import provider_workspace_plan, record_workspace_state


GPU_BOUND_JOB_TYPES = {"asr", "llm_heavy", "vlm_ocr", "pdf_ocr"}


def _requires_hardware_verification(job: Job, provider: str) -> bool:
    hardware = job.metadata.get("hardware_verification") if isinstance(job.metadata.get("hardware_verification"), dict) else {}
    if "require_gpu_utilization" in hardware:
        return bool(hardware.get("require_gpu_utilization"))
    if job.job_type not in GPU_BOUND_JOB_TYPES:
        return False
    if job.gpu_profile in {"cpu", "embedding"}:
        return False
    if provider in {"local", "ollama"}:
        return False
    return True


def _save_blocked_job(job: Job, store: JobStore, selected: str) -> None:
    instant_phase(job, terminal_phase_for_status(job.status), provider=selected, status=job.status)
    store.save(job)


def _plan_quote(job: Job) -> dict[str, Any]:
    workflow_quote = job.metadata.get("workflow_plan_quote") if isinstance(job.metadata.get("workflow_plan_quote"), dict) else {}
    child_quote = job.metadata.get("plan_quote") if isinstance(job.metadata.get("plan_quote"), dict) else {}
    if workflow_quote and job.job_type != "cpu_workflow_helper":
        return workflow_quote
    return child_quote or workflow_quote


def _quote_provider(job: Job) -> str:
    quote = _plan_quote(job)
    selected = quote.get("selected_option") if isinstance(quote.get("selected_option"), dict) else {}
    return str(selected.get("provider") or "")


def _workspace_plan_for_submit(job: Job, selected: str) -> tuple[dict[str, Any], dict[str, Any]]:
    current = provider_workspace_plan(job, selected)
    planned = job.metadata.get("workspace_plan") if isinstance(job.metadata.get("workspace_plan"), dict) else {}
    if planned and str(planned.get("provider") or "") == selected:
        return planned, current
    return current, current


def submit_job(
    job: Job,
    provider_name: str = "auto",
    execute: bool = False,
    enforce_capacity: bool = True,
    guard_provider_names: list[str] | None = None,
) -> dict[str, Any]:
    ensure_trace(job.metadata)
    ensure_received(job)
    store = JobStore()
    quote = _plan_quote(job)
    quoted_provider = _quote_provider(job)
    if provider_name != "auto" and quoted_provider and provider_name != quoted_provider:
        selected = provider_name
        enter_phase(job, "validated", provider=selected)
        exit_phase(job, "validated", provider=selected, status="failed", error_class="quote_provider_mismatch")
        job.status = "failed"
        job.error = "explicit provider conflicts with plan_quote selected provider"
        job.metadata["selected_provider"] = selected
        job.metadata["route_result"] = {
            "selected_provider": selected,
            "source": "explicit_provider",
            "plan_quote_id": quote.get("quote_id"),
            "quoted_provider": quoted_provider,
        }
        job.metadata["error_class"] = classify_error(job.error, provider=selected, context={"gate": "plan_quote"})
        _save_blocked_job(job, store, selected)
        append_audit(
            "submit.blocked.plan_quote_mismatch",
            {"job_id": job.job_id, "provider_name": provider_name, "quoted_provider": quoted_provider},
            store=store,
        )
        return {"ok": False, "error": job.error, "job": job.to_dict(), "path": str(store.job_path(job.job_id))}
    if provider_name == "auto" and quoted_provider:
        route_result = {
            "selected_provider": quoted_provider,
            "source": "plan_quote",
            "plan_quote_id": quote.get("quote_id"),
            "gpu_profile": job.gpu_profile,
            "candidates": [quoted_provider],
            "decision": {
                "strategy": "plan_quote_bound_routing",
                "reason": "selected provider is bound to immutable plan_quote",
            },
        }
    else:
        route_result = route_job(job) if provider_name == "auto" else {"selected_provider": provider_name}
    selected = str(route_result["selected_provider"])
    provider = get_provider(selected)
    enter_phase(job, "validated", provider=selected)
    policy_validation = validate_policy()
    if not policy_validation["ok"]:
        exit_phase(job, "validated", provider=selected, status="failed", error_class="policy")
        job.status = "failed"
        job.error = "policy validation failed"
        job.metadata["error_class"] = classify_error(job.error, provider=selected, context={"gate": "policy"})
        job.metadata["policy_validation"] = policy_validation
        _save_blocked_job(job, store, selected)
        append_audit("submit.blocked.policy", {"job_id": job.job_id, "policy_validation": policy_validation}, store=store)
        return {"ok": False, "error": job.error, "job": job.to_dict(), "path": str(store.job_path(job.job_id))}
    circuit = provider_circuit_state(selected, store=store)
    if not circuit["ok"]:
        if circuit.get("half_open_probe_allowed"):
            job.metadata["circuit_probe"] = circuit
            append_audit("submit.circuit.half_open_probe", {"job_id": job.job_id, "circuit": circuit}, store=store)
        else:
            exit_phase(job, "validated", provider=selected, status="failed", error_class="circuit")
            job.status = "failed"
            job.error = f"provider circuit open: {selected}"
            job.metadata["error_class"] = classify_error(job.error, provider=selected, context={"gate": "circuit"})
            job.metadata["circuit"] = circuit
            _save_blocked_job(job, store, selected)
            append_audit("submit.blocked.circuit", {"job_id": job.job_id, "circuit": circuit}, store=store)
            return {"ok": False, "error": job.error, "job": job.to_dict(), "path": str(store.job_path(job.job_id))}
    if circuit["ok"]:
        job.metadata["circuit"] = circuit
    provenance = evaluate_provenance(job)
    job.metadata["selected_provider"] = selected
    job.metadata["route_result"] = route_result
    job.metadata["route_explanation"] = route_explanation(
        {
            **route_result,
            "selected_provider": selected,
            "gpu_profile": job.gpu_profile,
            "candidates": route_result.get("candidates") or [selected],
        }
    )
    compliance = evaluate_compliance(job)
    capability = evaluate_model_capability(job, selected)
    quota = quota_check(job, store=store)
    cost = cost_estimate(job)
    secrets = secret_check(job, selected)
    placement = placement_check(job, selected)
    preemption = preemption_check(job, store=store)
    timeout = timeout_contract(job)
    workspace_plan, current_workspace_plan = _workspace_plan_for_submit(job, selected)
    job.metadata["workspace_plan"] = workspace_plan
    if current_workspace_plan.get("workspace_plan_id") != workspace_plan.get("workspace_plan_id"):
        job.metadata["workspace_plan_current"] = current_workspace_plan
    job.metadata["workspace_record"] = record_workspace_state(job, workspace_plan, state="planned")
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
        exit_phase(job, "validated", provider=selected, status="failed", error_class="safety_gate")
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
        _save_blocked_job(job, store, selected)
        append_audit(
            "submit.blocked.safety_gate",
            {"job_id": job.job_id, "failed_gate": failed_gate, "gates": {name: item for name, item in gates}},
            store=store,
        )
        return {"ok": False, "error": job.error, "job": job.to_dict(), "path": str(store.job_path(job.job_id))}
    if not timeout["ok"]:
        exit_phase(job, "validated", provider=selected, status="failed", error_class="timeout")
        job.status = "failed"
        job.error = "timeout contract failed"
        job.metadata["error_class"] = classify_error(job.error, provider=selected, context={"gate": "timeout"})
        job.metadata["timeout_contract"] = timeout
        _save_blocked_job(job, store, selected)
        append_audit("submit.blocked.timeout", {"job_id": job.job_id, "timeout": timeout}, store=store)
        return {"ok": False, "error": job.error, "job": job.to_dict(), "path": str(store.job_path(job.job_id))}
    if execute and current_workspace_plan.get("workspace_plan_id") != workspace_plan.get("workspace_plan_id"):
        exit_phase(job, "validated", provider=selected, status="failed", error_class="workspace_plan_drift")
        job.status = "failed"
        job.error = "workspace plan drift detected before GPU allocation"
        job.metadata["error_class"] = classify_error(job.error, provider=selected, context={"gate": "workspace"})
        job.metadata["workspace_record"] = record_workspace_state(job, workspace_plan, state="drift", status="blocked")
        _save_blocked_job(job, store, selected)
        append_audit(
            "submit.blocked.workspace_drift",
            {"job_id": job.job_id, "workspace_plan": workspace_plan, "current_workspace_plan": current_workspace_plan},
            store=store,
        )
        return {"ok": False, "error": job.error, "job": job.to_dict(), "path": str(store.job_path(job.job_id))}
    if execute and workspace_plan.get("decision") == "requires_action":
        exit_phase(job, "validated", provider=selected, status="failed", error_class="workspace_requires_action")
        job.status = "failed"
        job.error = "workspace contract requires action before GPU allocation"
        job.metadata["error_class"] = classify_error(job.error, provider=selected, context={"gate": "workspace"})
        job.metadata["workspace_record"] = record_workspace_state(job, workspace_plan, state="requires_action", status="blocked")
        _save_blocked_job(job, store, selected)
        append_audit("submit.blocked.workspace", {"job_id": job.job_id, "workspace_plan": workspace_plan}, store=store)
        return {"ok": False, "error": job.error, "job": job.to_dict(), "path": str(store.job_path(job.job_id))}
    exit_phase(job, "validated", provider=selected, status="ok")
    instant_phase(job, "planned", provider=selected, status="ok")
    decision = make_decision(job, phase="submit", route_result=route_result, store=store)
    billing_guard_providers = guard_provider_names if guard_provider_names is not None else [selected]
    pre_guard = collect_cost_guard(provider_names=billing_guard_providers) if execute else None
    if pre_guard and not pre_guard["ok"]:
        job.status = "failed"
        job.error = "pre-submit cost guard failed"
        job.metadata["error_class"] = classify_error(job.error, provider=selected, context={"gate": "billing_guard"})
        job.metadata["pre_submit_guard"] = pre_guard
        _save_blocked_job(job, store, selected)
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
        enter_phase(job, "reserving_workspace", provider=selected)
        append_wal(
            job, transition=f"{job.status}->starting", intent="reserve_direct_execution_slot", extra={"provider": selected}, store=store
        )
        capacity = reserve_direct_execution_slot(job, selected)
        if not capacity.get("ok"):
            exit_phase(job, "reserving_workspace", provider=selected, status="failed", error_class="backpressure")
            job.provider = selected
            job.metadata["selected_provider"] = selected
            job.metadata["error_class"] = classify_error(
                capacity["error"], status_code=429, provider=selected, context={"gate": "capacity"}
            )
            job.status = "failed"
            job.error = str(capacity["error"])
            _save_blocked_job(job, store, selected)
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
        exit_phase(job, "reserving_workspace", provider=selected, status="ok")

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
            post_guard = collect_cost_guard(provider_names=billing_guard_providers)

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
    saved.metadata["workspace_plan"] = workspace_plan
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
            artifact_dir = store.artifact_dir(saved.job_id)
            enter_phase(saved, "verifying", provider=selected)
            write_manifest(artifact_dir)
            final_verify = verify_artifacts(
                artifact_dir,
                require_manifest=True,
                require_gpu_utilization=_requires_hardware_verification(saved, selected),
                execution_class="gpu",
            )
            saved.metadata["final_artifact_verify"] = final_verify
            saved.artifact_count = final_verify["artifact_count"]
            saved.artifact_bytes = final_verify["artifact_bytes"]
            if not final_verify["ok"] and saved.status == "succeeded":
                saved.status = "failed"
                saved.error = "final artifact verification failed"
                saved.exit_code = saved.exit_code if saved.exit_code not in {None, 0} else 1
                saved.metadata["error_class"] = classify_error(saved.error, provider=selected, context={"gate": "artifact_verify"})
            exit_phase(
                saved,
                "verifying",
                provider=selected,
                status="ok" if final_verify["ok"] else "failed",
                error_class="" if final_verify["ok"] else "artifact_verify",
            )
            store.save(saved)
        except Exception as exc:
            exit_phase(saved, "verifying", provider=selected, status="failed", error_class="artifact_verify")
            saved.metadata["manifest_error"] = str(exc)
            store.save(saved)
    if saved.status in {"succeeded", "failed", "cancelled"}:
        instant_phase(saved, terminal_phase_for_status(saved.status), provider=selected, status=saved.status)
        store.save(saved)
        if execute:
            try:
                saved.metadata["workspace_record"] = record_workspace_state(
                    saved,
                    saved.metadata.get("workspace_plan") if isinstance(saved.metadata.get("workspace_plan"), dict) else workspace_plan,
                    state="terminal",
                    status=saved.status,
                )
                write_execution_record(saved, store=store)
            except Exception as exc:
                saved.metadata["execution_record_error"] = str(exc)
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
