from __future__ import annotations

from pathlib import Path
import argparse
import json
import os

from .models import Job
from .api import serve as serve_api
from .audit import verify_audit_chain
from .authz import approval_ok, approval_record, authorize, list_approvals, save_approval
from .capabilities import evaluate_model_capability, load_capabilities
from .circuit import all_circuits
from .decision import load_decision, replay_all_decisions, replay_decision
from .destructive import destructive_preflight
from .cost import cost_estimate
from .drain import clear_drain, drain_status, start_drain
from .dlq import dlq_status
from .error_class import classify_error
from .guard import collect_cost_guard
from .image import image_build, image_check, image_plan
from .invariants import evaluate_invariants
from .metrics_export import metrics_prometheus, metrics_snapshot
from .intake import intake_job, intake_status, plan_intake_groups
from .policy_engine import policy_activation_record
from .provider_stability import provider_stability_report
from .provenance import expected_attestation_hash
from .providers import PROVIDERS, get_provider
from .providers.runpod import RunPodProvider
from .queue import cancel_group, cancel_job, enqueue_job, queue_status, replan_queued_jobs, retry_job, work_loop, work_once
from .readiness import launch_readiness
from .remediation import remediation_decision
from .retention import retention_report
from .placement import placement_check
from .preemption import preemption_check
from .quota import quota_check
from .secrets_policy import secret_check
from .selftest import run_selftest
from .reconcile import reconcile_detect_only
from .router import load_routing_config, provider_signal, route_job
from .runner import submit_job
from .stats import collect_stats
from .store import JobStore
from .timeout import timeout_contract
from .verify import verify_artifacts
from .wal import wal_recovery_plan, wal_recovery_status, wal_status
from .workflow import load_workflow, save_workflow


def print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def cmd_doctor(_: argparse.Namespace) -> int:
    results = [provider.doctor() for provider in PROVIDERS.values()]
    print_json({"ok": all(item.get("ok") for item in results), "providers": results})
    return 0 if all(item.get("ok") for item in results) else 1


def cmd_validate(args: argparse.Namespace) -> int:
    job = Job.from_file(Path(args.job))
    print_json({"ok": True, "job": job.to_dict()})
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    job = Job.from_file(Path(args.job))
    provider_name = route_job(job)["selected_provider"] if args.provider == "auto" else args.provider
    provider = get_provider(provider_name)
    print_json(provider.plan(job))
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    job = Job.from_file(Path(args.job))
    print_json(route_job(job))
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    job = Job.from_file(Path(args.job))
    result = submit_job(job, provider_name=args.provider, execute=args.execute)
    print_json(result)
    if result.get("ok"):
        return 0
    if result.get("error") == "pre-submit cost guard failed":
        return 2
    if result.get("status_code") == 429:
        return 75
    return 1


def cmd_enqueue(args: argparse.Namespace) -> int:
    job = Job.from_file(Path(args.job))
    print_json(enqueue_job(job, provider_name=args.provider))
    return 0


def cmd_intake(args: argparse.Namespace) -> int:
    job = Job.from_file(Path(args.job))
    print_json(intake_job(job, provider_name=args.provider))
    return 0


def cmd_intake_status(args: argparse.Namespace) -> int:
    print_json(intake_status(limit=args.limit, compact=not args.full))
    return 0


def cmd_intake_plan(_: argparse.Namespace) -> int:
    print_json(plan_intake_groups())
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    print_json(queue_status(limit=args.limit))
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    if args.job_id:
        result = cancel_job(args.job_id)
    else:
        result = cancel_group(source_system=args.source_system, workflow_id=args.workflow_id, task_family=args.task_family)
    print_json(result)
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    print_json(retry_job(args.job_id))
    return 0


def cmd_replan(args: argparse.Namespace) -> int:
    result = replan_queued_jobs(limit=args.limit)
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_worker(args: argparse.Namespace) -> int:
    if args.once:
        print_json(work_once())
        return 0
    return work_loop(poll_interval=args.poll_interval, once=False)


def cmd_status(args: argparse.Namespace) -> int:
    store = JobStore()
    job = store.load(args.job_id)
    print_json(job.to_dict())
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    required = args.required or None
    result = verify_artifacts(Path(args.artifact_dir), required=required)
    print_json(result)
    return 0 if result["ok"] else 1


def cmd_offers(args: argparse.Namespace) -> int:
    config = load_routing_config()
    profile = config.get("profiles", {}).get(args.profile)
    if not profile:
        raise ValueError(f"unknown profile: {args.profile}")
    provider = get_provider(args.provider)
    if not hasattr(provider, "offers"):
        raise ValueError(f"provider does not support offer search: {args.provider}")
    print_json(provider.offers(profile, limit=args.limit))
    return 0


def cmd_signals(args: argparse.Namespace) -> int:
    config = load_routing_config()
    profile = config.get("profiles", {}).get(args.profile)
    if not profile:
        raise ValueError(f"unknown profile: {args.profile}")
    providers = args.provider or sorted(PROVIDERS)
    print_json({"profile": args.profile, "signals": {name: provider_signal(name, profile) for name in providers}})
    return 0


def cmd_stability(_: argparse.Namespace) -> int:
    result = provider_stability_report()
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_guard(args: argparse.Namespace) -> int:
    result = collect_cost_guard(args.provider or sorted(PROVIDERS))
    print_json(result)
    return 0 if result["ok"] else 2


def cmd_stats(_: argparse.Namespace) -> int:
    print_json(collect_stats())
    return 0


def cmd_decision(args: argparse.Namespace) -> int:
    if args.replay_all:
        result = replay_all_decisions(limit=args.limit)
    else:
        if not args.job_id:
            raise ValueError("job_id is required unless --replay-all is used")
        result = replay_decision(args.job_id) if args.replay else load_decision(args.job_id)
    print_json(result)
    return 0 if result.get("ok") else 1


def cmd_reconcile(_: argparse.Namespace) -> int:
    result = reconcile_detect_only()
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_policy(_: argparse.Namespace) -> int:
    result = policy_activation_record()
    print_json(result)
    return 0 if result.get("ok") else 1


def cmd_audit(_: argparse.Namespace) -> int:
    result = verify_audit_chain()
    print_json(result)
    return 0 if result.get("ok") else 1


def cmd_wal(args: argparse.Namespace) -> int:
    if args.recovery_plan:
        result = wal_recovery_plan()
    elif args.recovery:
        result = wal_recovery_status()
    else:
        result = wal_status(limit=args.limit)
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_authz(args: argparse.Namespace) -> int:
    result = authorize(args.principal, args.action, scope=args.scope)
    print_json(result)
    return 0 if result.get("ok") else 3


def cmd_approval(args: argparse.Namespace) -> int:
    if args.approval_command == "create":
        record = approval_record(args.action, args.principal, approved=args.approved, expires_at=args.expires_at, reason=args.reason)
        result = save_approval(record)
    elif args.approval_command == "check":
        result = approval_ok(args.action, args.principal)
    elif args.approval_command == "list":
        result = list_approvals(limit=args.limit)
    else:
        raise ValueError(f"unknown approval command: {args.approval_command}")
    print_json(result)
    return 0 if result.get("ok") else 3


def cmd_timeout(args: argparse.Namespace) -> int:
    job = Job.from_file(Path(args.job))
    result = timeout_contract(job)
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_error_class(args: argparse.Namespace) -> int:
    result = classify_error(args.error, status_code=args.status_code, provider=args.provider)
    print_json(result)
    return 0


def cmd_remediation(args: argparse.Namespace) -> int:
    job = Job.from_file(Path(args.job))
    result = remediation_decision(job)
    print_json(result)
    return 0 if result.get("ok") else 1


def cmd_readiness(args: argparse.Namespace) -> int:
    result = launch_readiness(limit=args.limit)
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_invariants(args: argparse.Namespace) -> int:
    job = Job.from_file(Path(args.job))
    result = evaluate_invariants(job, provider_name=args.provider)
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_capabilities(args: argparse.Namespace) -> int:
    if args.capability_command == "list":
        result = {"ok": True, "registry": load_capabilities()}
    elif args.capability_command == "check":
        job = Job.from_file(Path(args.job))
        result = evaluate_model_capability(job, provider=args.provider)
    else:
        raise ValueError(f"unknown capability command: {args.capability_command}")
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_attestation(args: argparse.Namespace) -> int:
    job = Job.from_file(Path(args.job))
    print_json({"ok": True, "subject_sha256": expected_attestation_hash(job)})
    return 0


def cmd_destructive(args: argparse.Namespace) -> int:
    result = destructive_preflight(args.action, args.principal, target=args.target, scope=args.scope)
    print_json(result)
    return 0 if result.get("ok") else 3


def cmd_eval(args: argparse.Namespace) -> int:
    job = Job.from_file(Path(args.job))
    provider = args.provider
    if args.eval_command == "quota":
        result = quota_check(job)
    elif args.eval_command == "cost":
        result = cost_estimate(job)
    elif args.eval_command == "secrets":
        result = secret_check(job, provider)
    elif args.eval_command == "placement":
        result = placement_check(job, provider)
    elif args.eval_command == "preemption":
        result = preemption_check(job)
    else:
        raise ValueError(f"unknown eval command: {args.eval_command}")
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_drain(args: argparse.Namespace) -> int:
    if args.drain_command == "start":
        result = start_drain(args.reason)
    elif args.drain_command == "clear":
        result = clear_drain()
    elif args.drain_command == "status":
        result = drain_status()
    else:
        raise ValueError(f"unknown drain command: {args.drain_command}")
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_metrics(args: argparse.Namespace) -> int:
    if args.prometheus:
        print(metrics_prometheus(), end="")
        return 0
    result = metrics_snapshot()
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_retention(_: argparse.Namespace) -> int:
    result = retention_report()
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_selftest(_: argparse.Namespace) -> int:
    result = run_selftest()
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_circuits(_: argparse.Namespace) -> int:
    result = all_circuits()
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_dlq(args: argparse.Namespace) -> int:
    print_json(dlq_status(limit=args.limit))
    return 0


def cmd_workflow(args: argparse.Namespace) -> int:
    if args.workflow_command == "validate":
        data = json.loads(Path(args.workflow).read_text())
        print_json(save_workflow(data))
        return 0
    if args.workflow_command == "status":
        result = load_workflow(args.workflow_id)
        print_json(result)
        return 0 if result.get("ok") else 1
    raise ValueError(f"unknown workflow command: {args.workflow_command}")


def cmd_image(args: argparse.Namespace) -> int:
    if args.image_command == "plan":
        result = image_plan(args.worker)
    elif args.image_command == "check":
        result = image_check(args.worker)
    elif args.image_command == "build":
        pre_guard = collect_cost_guard()
        if not pre_guard["ok"]:
            print_json({"ok": False, "error": "pre-build cost guard failed", "guard": pre_guard})
            return 2
        result = image_build(args.worker, execute=args.execute)
        post_guard = collect_cost_guard()
        result["pre_build_guard"] = pre_guard
        result["post_build_guard"] = post_guard
        if not post_guard["ok"]:
            result["ok"] = False
        print_json(result)
        return 0 if result.get("ok") else 1
    else:
        raise ValueError(f"unknown image command: {args.image_command}")
    print_json(result)
    return 0 if result.get("ok") else 1


def cmd_runpod(args: argparse.Namespace) -> int:
    provider = get_provider("runpod")
    if not isinstance(provider, RunPodProvider):
        raise ValueError("runpod provider unavailable")
    if args.runpod_command == "quarantine-ollama":
        result = provider.quarantine_public_ollama_endpoint(
            model=args.model,
            gpu_ids=args.gpu_ids,
            network_volume_id=args.network_volume_id,
            locations=args.locations,
            template_id=args.template_id,
            flashboot=args.flashboot,
        )
    else:
        raise ValueError(f"unknown runpod command: {args.runpod_command}")
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_serve(args: argparse.Namespace) -> int:
    if args.require_token:
        if not os.getenv("GPU_JOB_API_TOKEN", "").strip():
            raise ValueError("GPU_JOB_API_TOKEN is required")
    serve_api(host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gpu-job")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.set_defaults(func=cmd_doctor)

    validate = sub.add_parser("validate")
    validate.add_argument("job", help="path to a gpu-job JSON file")
    validate.set_defaults(func=cmd_validate)

    plan = sub.add_parser("plan")
    plan.add_argument("job", help="path to a gpu-job JSON file")
    plan.add_argument("--provider", choices=["auto", *sorted(PROVIDERS)], required=True, help="provider to plan for")
    plan.set_defaults(func=cmd_plan)

    route = sub.add_parser("route")
    route.add_argument("job", help="path to a gpu-job JSON file")
    route.set_defaults(func=cmd_route)

    submit = sub.add_parser("submit")
    submit.add_argument("job", help="path to a gpu-job JSON file")
    submit.add_argument("--provider", choices=["auto", *sorted(PROVIDERS)], required=True, help="provider to submit to")
    submit.add_argument("--execute", action="store_true", help="execute when the provider supports it")
    submit.set_defaults(func=cmd_submit)

    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument("job", help="path to a gpu-job JSON file")
    enqueue.add_argument("--provider", choices=["auto", *sorted(PROVIDERS)], required=True, help="provider to queue for")
    enqueue.set_defaults(func=cmd_enqueue)

    intake = sub.add_parser("intake")
    intake.add_argument("job", help="path to a gpu-job JSON file")
    intake.add_argument("--provider", choices=["auto", *sorted(PROVIDERS)], default="auto", help="requested provider or auto")
    intake.set_defaults(func=cmd_intake)

    intake_status_parser = sub.add_parser("intake-status")
    intake_status_parser.add_argument("--limit", type=int, default=100, help="maximum buffered jobs to show")
    intake_status_parser.add_argument("--full", action="store_true", help="show full job payloads")
    intake_status_parser.set_defaults(func=cmd_intake_status)

    intake_plan = sub.add_parser("intake-plan")
    intake_plan.set_defaults(func=cmd_intake_plan)

    queue = sub.add_parser("queue")
    queue.add_argument("--limit", type=int, default=100, help="maximum queued jobs to show")
    queue.set_defaults(func=cmd_queue)

    cancel = sub.add_parser("cancel")
    cancel.add_argument("job_id", nargs="?", help="job id to cancel")
    cancel.add_argument("--source-system", default="", help="cancel jobs from this source system")
    cancel.add_argument("--workflow-id", default="", help="cancel jobs in this workflow")
    cancel.add_argument("--task-family", default="", help="cancel jobs in this task family")
    cancel.set_defaults(func=cmd_cancel)

    replan = sub.add_parser("replan")
    replan.add_argument("--limit", type=int, default=1000, help="maximum queued jobs to replan")
    replan.set_defaults(func=cmd_replan)

    retry = sub.add_parser("retry")
    retry.add_argument("job_id", help="failed job id to retry")
    retry.set_defaults(func=cmd_retry)

    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true", help="run one worker iteration")
    worker.add_argument("--poll-interval", type=float, default=5.0, help="seconds between worker polls")
    worker.set_defaults(func=cmd_worker)

    status = sub.add_parser("status")
    status.add_argument("job_id", help="job id to load")
    status.set_defaults(func=cmd_status)

    verify = sub.add_parser("verify")
    verify.add_argument("artifact_dir", help="artifact directory to verify")
    verify.add_argument("--required", action="append", help="required artifact filename; may be repeated")
    verify.set_defaults(func=cmd_verify)

    offers = sub.add_parser("offers")
    offers.add_argument("--provider", choices=["vast"], required=True, help="provider to query")
    offers.add_argument("--profile", required=True, help="GPU profile name")
    offers.add_argument("--limit", type=int, default=5, help="maximum offers to return")
    offers.set_defaults(func=cmd_offers)

    signals = sub.add_parser("signals")
    signals.add_argument("--profile", required=True, help="GPU profile name")
    signals.add_argument("--provider", action="append", choices=sorted(PROVIDERS), help="provider to inspect; repeatable")
    signals.set_defaults(func=cmd_signals)

    stability = sub.add_parser("stability")
    stability.set_defaults(func=cmd_stability)

    guard = sub.add_parser("guard")
    guard.add_argument("--provider", action="append", choices=sorted(PROVIDERS), help="provider to guard; repeatable")
    guard.set_defaults(func=cmd_guard)

    stats = sub.add_parser("stats")
    stats.set_defaults(func=cmd_stats)

    decision = sub.add_parser("decision")
    decision.add_argument("job_id", nargs="?", help="job id whose decision should be read")
    decision.add_argument("--replay", action="store_true", help="replay one stored decision")
    decision.add_argument("--replay-all", action="store_true", help="replay recent stored decisions")
    decision.add_argument("--limit", type=int, default=1000, help="maximum decisions for replay-all")
    decision.set_defaults(func=cmd_decision)

    reconcile = sub.add_parser("reconcile")
    reconcile.set_defaults(func=cmd_reconcile)

    policy_cmd = sub.add_parser("policy")
    policy_cmd.set_defaults(func=cmd_policy)

    audit = sub.add_parser("audit")
    audit.set_defaults(func=cmd_audit)

    wal = sub.add_parser("wal")
    wal.add_argument("--limit", type=int, default=100, help="maximum WAL records to inspect")
    wal.add_argument("--recovery", action="store_true", help="show recovery status")
    wal.add_argument("--recovery-plan", action="store_true", help="show recovery plan")
    wal.set_defaults(func=cmd_wal)

    authz = sub.add_parser("authz")
    authz.add_argument("--principal", required=True, help="principal to authorize")
    authz.add_argument("--action", required=True, help="action to check")
    authz.add_argument("--scope", default="", help="optional authorization scope")
    authz.set_defaults(func=cmd_authz)

    approval = sub.add_parser("approval")
    approval_sub = approval.add_subparsers(dest="approval_command", required=True)
    approval_create = approval_sub.add_parser("create")
    approval_create.add_argument("--principal", required=True, help="principal receiving the approval decision")
    approval_create.add_argument("--action", required=True, help="action being approved or denied")
    approval_create.add_argument("--approved", action="store_true", help="record approval instead of denial")
    approval_create.add_argument("--expires-at", type=int, help="Unix timestamp when approval expires")
    approval_create.add_argument("--reason", default="", help="human-readable approval reason")
    approval_create.set_defaults(func=cmd_approval)
    approval_check = approval_sub.add_parser("check")
    approval_check.add_argument("--principal", required=True, help="principal to check")
    approval_check.add_argument("--action", required=True, help="action to check")
    approval_check.set_defaults(func=cmd_approval)
    approval_list = approval_sub.add_parser("list")
    approval_list.add_argument("--limit", type=int, default=100, help="maximum approvals to list")
    approval_list.set_defaults(func=cmd_approval)

    timeout = sub.add_parser("timeout")
    timeout.add_argument("job", help="path to a gpu-job JSON file")
    timeout.set_defaults(func=cmd_timeout)

    error_class = sub.add_parser("error-class")
    error_class.add_argument("--error", default="", help="provider error text")
    error_class.add_argument("--status-code", type=int, help="HTTP or provider status code")
    error_class.add_argument("--provider", default="", help="provider name")
    error_class.set_defaults(func=cmd_error_class)

    remediation = sub.add_parser("remediation")
    remediation.add_argument("job", help="path to a gpu-job JSON file")
    remediation.set_defaults(func=cmd_remediation)

    readiness = sub.add_parser("readiness")
    readiness.add_argument("--limit", type=int, default=100, help="maximum records to inspect")
    readiness.set_defaults(func=cmd_readiness)

    invariants = sub.add_parser("invariants")
    invariants.add_argument("job", help="path to a gpu-job JSON file")
    invariants.add_argument("--provider", choices=["auto", *sorted(PROVIDERS)], default="auto", help="provider to evaluate")
    invariants.set_defaults(func=cmd_invariants)

    capabilities = sub.add_parser("capabilities")
    capabilities_sub = capabilities.add_subparsers(dest="capability_command", required=True)
    capabilities_list = capabilities_sub.add_parser("list")
    capabilities_list.set_defaults(func=cmd_capabilities)
    capabilities_check = capabilities_sub.add_parser("check")
    capabilities_check.add_argument("job", help="path to a gpu-job JSON file")
    capabilities_check.add_argument("--provider", required=True, choices=sorted(PROVIDERS), help="provider to check")
    capabilities_check.set_defaults(func=cmd_capabilities)

    attestation = sub.add_parser("attestation")
    attestation.add_argument("job", help="path to a gpu-job JSON file")
    attestation.set_defaults(func=cmd_attestation)

    destructive = sub.add_parser("destructive-check")
    destructive.add_argument("--principal", required=True, help="principal requesting the destructive action")
    destructive.add_argument("--action", required=True, help="destructive action name")
    destructive.add_argument("--target", default="", help="target resource")
    destructive.add_argument("--scope", default="", help="optional action scope")
    destructive.set_defaults(func=cmd_destructive)

    eval_cmd = sub.add_parser("eval")
    eval_sub = eval_cmd.add_subparsers(dest="eval_command", required=True)
    for name in ["quota", "cost", "secrets", "placement", "preemption"]:
        item = eval_sub.add_parser(name)
        item.add_argument("job", help="path to a gpu-job JSON file")
        item.add_argument("--provider", default="", help="provider context")
        item.set_defaults(func=cmd_eval)

    drain = sub.add_parser("drain")
    drain_sub = drain.add_subparsers(dest="drain_command", required=True)
    drain_start = drain_sub.add_parser("start")
    drain_start.add_argument("--reason", default="", help="reason for draining")
    drain_start.set_defaults(func=cmd_drain)
    drain_clear = drain_sub.add_parser("clear")
    drain_clear.set_defaults(func=cmd_drain)
    drain_status_parser = drain_sub.add_parser("status")
    drain_status_parser.set_defaults(func=cmd_drain)

    metrics = sub.add_parser("metrics")
    metrics.add_argument("--prometheus", action="store_true", help="emit Prometheus text format")
    metrics.set_defaults(func=cmd_metrics)

    retention = sub.add_parser("retention")
    retention.set_defaults(func=cmd_retention)

    selftest = sub.add_parser("selftest")
    selftest.set_defaults(func=cmd_selftest)

    circuits = sub.add_parser("circuits")
    circuits.set_defaults(func=cmd_circuits)

    dlq = sub.add_parser("dlq")
    dlq.add_argument("--limit", type=int, default=100, help="maximum failed jobs to show")
    dlq.set_defaults(func=cmd_dlq)

    workflow = sub.add_parser("workflow")
    workflow_sub = workflow.add_subparsers(dest="workflow_command", required=True)
    workflow_validate = workflow_sub.add_parser("validate")
    workflow_validate.add_argument("workflow", help="path to workflow JSON file")
    workflow_validate.set_defaults(func=cmd_workflow)
    workflow_status = workflow_sub.add_parser("status")
    workflow_status.add_argument("workflow_id", help="workflow id to load")
    workflow_status.set_defaults(func=cmd_workflow)

    image = sub.add_parser("image")
    image_sub = image.add_subparsers(dest="image_command", required=True)
    for name in ["plan", "check", "build"]:
        item = image_sub.add_parser(name)
        item.add_argument("--worker", default="asr", help="worker image family")
        if name == "build":
            item.add_argument("--execute", action="store_true", help="perform the remote/CI build action")
        item.set_defaults(func=cmd_image)

    runpod = sub.add_parser("runpod")
    runpod_sub = runpod.add_subparsers(dest="runpod_command", required=True)
    quarantine = runpod_sub.add_parser("quarantine-ollama")
    quarantine.add_argument("--model", default="llama3.2:1b", help="public Ollama model to test")
    quarantine.add_argument("--gpu-ids", default="AMPERE_24,ADA_24", help="RunPod GPU id list")
    quarantine.add_argument(
        "--network-volume-id",
        default=os.getenv("RUNPOD_NETWORK_VOLUME_ID", ""),
        help="optional RunPod network volume id",
    )
    quarantine.add_argument("--locations", default="US", help="RunPod location filter")
    quarantine.add_argument("--template-id", default="", help="existing RunPod template id")
    quarantine.add_argument("--flashboot", action="store_true", help="request RunPod FlashBoot")
    quarantine.set_defaults(func=cmd_runpod)

    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1", help="API bind host")
    serve.add_argument("--port", type=int, default=8765, help="API bind port")
    serve.add_argument("--require-token", action="store_true", help="fail startup when GPU_JOB_API_TOKEN is empty")
    serve.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print_json({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
