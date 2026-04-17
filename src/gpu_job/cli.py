from __future__ import annotations

from pathlib import Path
import argparse
import json
import os
import shutil

from .models import Job
from .api import serve as serve_api
from .audit import verify_audit_chain
from .authz import approval_ok, approval_record, authorize, list_approvals, save_approval
from .capabilities import evaluate_model_capability, load_capabilities
from .config import config_path, project_root
from .circuit import all_circuits
from .decision import load_decision, replay_all_decisions, replay_decision
from .destructive import destructive_preflight
from .cost import cost_estimate
from .drain import clear_drain, drain_status, start_drain
from .dlq import dlq_status
from .error_class import classify_error
from .guard import collect_cost_guard
from .image import image_build, image_check, image_mirror, image_plan
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


CONFIG_FILES = {
    "execution_policy": ("GPU_JOB_EXECUTION_POLICY", "execution-policy.json"),
    "gpu_profiles": ("GPU_JOB_PROFILES_CONFIG", "gpu-profiles.json"),
    "model_capabilities": ("GPU_JOB_CAPABILITIES_CONFIG", "model-capabilities.json"),
}


def user_config_dir() -> Path:
    xdg_config = os.getenv("XDG_CONFIG_HOME", "").strip()
    if xdg_config:
        return Path(xdg_config).expanduser() / "gpu-job-control"
    return Path.home() / ".config" / "gpu-job-control"


def cmd_config(args: argparse.Namespace) -> int:
    if args.config_command == "paths":
        print_json(
            {
                "ok": True,
                "user_config_dir": str(user_config_dir()),
                "resolved": {
                    name: {
                        "env": env_name,
                        "filename": filename,
                        "path": str(config_path(env_name, filename)),
                    }
                    for name, (env_name, filename) in CONFIG_FILES.items()
                },
            }
        )
        return 0
    if args.config_command == "init":
        target = Path(args.dir).expanduser() if args.dir else user_config_dir()
        target.mkdir(parents=True, exist_ok=True)
        created: list[str] = []
        skipped: list[str] = []
        for _, filename in CONFIG_FILES.values():
            src = project_root() / "config" / filename
            dst = target / filename
            if dst.exists() and not args.force:
                skipped.append(str(dst))
                continue
            shutil.copyfile(src, dst)
            created.append(str(dst))
        print_json({"ok": True, "target_dir": str(target), "created": created, "skipped": skipped})
        return 0
    raise ValueError(f"unknown config command: {args.config_command}")


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
    elif args.image_command == "mirror":
        result = image_mirror(args.source, args.target, builder=args.builder, execute=args.execute)
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
    elif args.runpod_command == "plan-pod-worker":
        result = provider.plan_pod_worker(
            gpu_type_id=args.gpu_type_id,
            image=args.image,
            cloud_type=args.cloud_type,
            gpu_count=args.gpu_count,
            volume_in_gb=args.volume_in_gb,
            container_disk_in_gb=args.container_disk_in_gb,
            min_vcpu_count=args.min_vcpu_count,
            min_memory_in_gb=args.min_memory_in_gb,
            max_uptime_seconds=args.max_uptime_seconds,
            max_estimated_cost_usd=args.max_estimated_cost_usd,
            docker_args=args.docker_args,
        )
    elif args.runpod_command == "canary-pod-lifecycle":
        pre_guard = collect_cost_guard(["runpod"])
        if args.execute and not pre_guard["ok"]:
            print_json({"ok": False, "error": "pre-pod-canary cost guard failed", "guard": pre_guard})
            return 2
        result = provider.canary_pod_lifecycle(
            gpu_type_id=args.gpu_type_id,
            image=args.image,
            cloud_type=args.cloud_type,
            gpu_count=args.gpu_count,
            volume_in_gb=args.volume_in_gb,
            container_disk_in_gb=args.container_disk_in_gb,
            min_vcpu_count=args.min_vcpu_count,
            min_memory_in_gb=args.min_memory_in_gb,
            max_uptime_seconds=args.max_uptime_seconds,
            max_estimated_cost_usd=args.max_estimated_cost_usd,
            docker_args=args.docker_args,
            execute=args.execute,
        )
        result["pre_pod_canary_guard"] = pre_guard
        post_guard = collect_cost_guard(["runpod"])
        result["post_pod_canary_guard"] = post_guard
        if not post_guard["ok"]:
            result["ok"] = False
    elif args.runpod_command == "canary-pod-http-worker":
        pre_guard = collect_cost_guard(["runpod"])
        if args.execute and not pre_guard["ok"]:
            print_json({"ok": False, "error": "pre-pod-http-canary cost guard failed", "guard": pre_guard})
            return 2
        result = provider.canary_pod_http_worker(
            gpu_type_id=args.gpu_type_id,
            image=args.image,
            cloud_type=args.cloud_type,
            gpu_count=args.gpu_count,
            volume_in_gb=args.volume_in_gb,
            container_disk_in_gb=args.container_disk_in_gb,
            min_vcpu_count=args.min_vcpu_count,
            min_memory_in_gb=args.min_memory_in_gb,
            max_uptime_seconds=args.max_uptime_seconds,
            max_estimated_cost_usd=args.max_estimated_cost_usd,
            network_volume_id=args.network_volume_id,
            data_center_id=args.data_center_id,
            worker_mode=args.worker_mode,
            prompt=args.prompt,
            execute=args.execute,
        )
        result["pre_pod_http_canary_guard"] = pre_guard
        post_guard = collect_cost_guard(["runpod"])
        result["post_pod_http_canary_guard"] = post_guard
        if not post_guard["ok"]:
            result["ok"] = False
    elif args.runpod_command == "plan-vllm-endpoint":
        result = provider.plan_vllm_endpoint(
            model=args.model,
            image=args.image,
            gpu_ids=args.gpu_ids,
            network_volume_id=args.network_volume_id,
            locations=args.locations,
            hf_secret_name=args.hf_secret_name,
            max_model_len=args.max_model_len,
            gpu_memory_utilization=args.gpu_memory_utilization,
            max_concurrency=args.max_concurrency,
            idle_timeout=args.idle_timeout,
            workers_max=args.workers_max,
            scaler_value=args.scaler_value,
            quantization=args.quantization,
            served_model_name=args.served_model_name,
            flashboot=args.flashboot,
        )
    elif args.runpod_command == "promote-vllm-endpoint":
        pre_guard = collect_cost_guard(["runpod"])
        if args.execute and not pre_guard["ok"]:
            print_json({"ok": False, "error": "pre-promotion cost guard failed", "guard": pre_guard})
            return 2
        result = provider.promote_vllm_endpoint(
            model=args.model,
            image=args.image,
            gpu_ids=args.gpu_ids,
            network_volume_id=args.network_volume_id,
            locations=args.locations,
            hf_secret_name=args.hf_secret_name,
            max_model_len=args.max_model_len,
            gpu_memory_utilization=args.gpu_memory_utilization,
            max_concurrency=args.max_concurrency,
            idle_timeout=args.idle_timeout,
            workers_max=args.workers_max,
            scaler_value=args.scaler_value,
            quantization=args.quantization,
            served_model_name=args.served_model_name,
            flashboot=args.flashboot,
            canary_prompt=args.canary_prompt,
            canary_timeout_seconds=args.canary_timeout_seconds,
            execute=args.execute,
        )
        result["pre_promotion_guard"] = pre_guard
        post_guard = collect_cost_guard(["runpod"])
        result["post_promotion_guard"] = post_guard
        if not post_guard["ok"]:
            result["ok"] = False
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

    doctor = sub.add_parser("doctor", help="check provider readiness without submitting work")
    doctor.set_defaults(func=cmd_doctor)

    config = sub.add_parser("config", help="inspect or initialize user configuration")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_paths = config_sub.add_parser("paths", help="show resolved configuration paths")
    config_paths.set_defaults(func=cmd_config)
    config_init = config_sub.add_parser("init", help="copy safe default config files into the user config directory")
    config_init.add_argument("--dir", help="target config directory; defaults to $XDG_CONFIG_HOME/gpu-job-control")
    config_init.add_argument("--force", action="store_true", help="overwrite existing config files")
    config_init.set_defaults(func=cmd_config)

    validate = sub.add_parser("validate", help="validate a gpu-job JSON file")
    validate.add_argument("job", help="path to a gpu-job JSON file")
    validate.set_defaults(func=cmd_validate)

    plan = sub.add_parser("plan", help="show provider execution plan for a job")
    plan.add_argument("job", help="path to a gpu-job JSON file")
    plan.add_argument("--provider", choices=["auto", *sorted(PROVIDERS)], required=True, help="provider to plan for")
    plan.set_defaults(func=cmd_plan)

    route = sub.add_parser("route", help="compute deterministic provider routing")
    route.add_argument("job", help="path to a gpu-job JSON file")
    route.set_defaults(func=cmd_route)

    submit = sub.add_parser("submit", help="submit a job directly to a provider")
    submit.add_argument("job", help="path to a gpu-job JSON file")
    submit.add_argument("--provider", choices=["auto", *sorted(PROVIDERS)], required=True, help="provider to submit to")
    submit.add_argument("--execute", action="store_true", help="execute when the provider supports it")
    submit.set_defaults(func=cmd_submit)

    enqueue = sub.add_parser("enqueue", help="put a job into the durable queue")
    enqueue.add_argument("job", help="path to a gpu-job JSON file")
    enqueue.add_argument("--provider", choices=["auto", *sorted(PROVIDERS)], required=True, help="provider to queue for")
    enqueue.set_defaults(func=cmd_enqueue)

    intake = sub.add_parser("intake", help="buffer a job for burst-aware planning")
    intake.add_argument("job", help="path to a gpu-job JSON file")
    intake.add_argument("--provider", choices=["auto", *sorted(PROVIDERS)], default="auto", help="requested provider or auto")
    intake.set_defaults(func=cmd_intake)

    intake_status_parser = sub.add_parser("intake-status", help="show buffered intake jobs")
    intake_status_parser.add_argument("--limit", type=int, default=100, help="maximum buffered jobs to show")
    intake_status_parser.add_argument("--full", action="store_true", help="show full job payloads")
    intake_status_parser.set_defaults(func=cmd_intake_status)

    intake_plan = sub.add_parser("intake-plan", help="plan currently buffered intake groups")
    intake_plan.set_defaults(func=cmd_intake_plan)

    queue = sub.add_parser("queue", help="show durable queue status")
    queue.add_argument("--limit", type=int, default=100, help="maximum queued jobs to show")
    queue.set_defaults(func=cmd_queue)

    cancel = sub.add_parser("cancel", help="cancel one job or a queued job group")
    cancel.add_argument("job_id", nargs="?", help="job id to cancel")
    cancel.add_argument("--source-system", default="", help="cancel jobs from this source system")
    cancel.add_argument("--workflow-id", default="", help="cancel jobs in this workflow")
    cancel.add_argument("--task-family", default="", help="cancel jobs in this task family")
    cancel.set_defaults(func=cmd_cancel)

    replan = sub.add_parser("replan", help="recompute provider placement for queued jobs")
    replan.add_argument("--limit", type=int, default=1000, help="maximum queued jobs to replan")
    replan.set_defaults(func=cmd_replan)

    retry = sub.add_parser("retry", help="retry a failed job")
    retry.add_argument("job_id", help="failed job id to retry")
    retry.set_defaults(func=cmd_retry)

    worker = sub.add_parser("worker", help="run the queue worker")
    worker.add_argument("--once", action="store_true", help="run one worker iteration")
    worker.add_argument("--poll-interval", type=float, default=5.0, help="seconds between worker polls")
    worker.set_defaults(func=cmd_worker)

    status = sub.add_parser("status", help="show one stored job")
    status.add_argument("job_id", help="job id to load")
    status.set_defaults(func=cmd_status)

    verify = sub.add_parser("verify", help="verify an artifact directory")
    verify.add_argument("artifact_dir", help="artifact directory to verify")
    verify.add_argument("--required", action="append", help="required artifact filename; may be repeated")
    verify.set_defaults(func=cmd_verify)

    offers = sub.add_parser("offers", help="query provider offers for a profile")
    offers.add_argument("--provider", choices=["vast"], required=True, help="provider to query")
    offers.add_argument("--profile", required=True, help="GPU profile name")
    offers.add_argument("--limit", type=int, default=5, help="maximum offers to return")
    offers.set_defaults(func=cmd_offers)

    signals = sub.add_parser("signals", help="show live provider routing signals")
    signals.add_argument("--profile", required=True, help="GPU profile name")
    signals.add_argument("--provider", action="append", choices=sorted(PROVIDERS), help="provider to inspect; repeatable")
    signals.set_defaults(func=cmd_signals)

    stability = sub.add_parser("stability", help="summarize provider stability")
    stability.set_defaults(func=cmd_stability)

    guard = sub.add_parser("guard", help="run spend, queue, and local resource guards")
    guard.add_argument("--provider", action="append", choices=sorted(PROVIDERS), help="provider to guard; repeatable")
    guard.set_defaults(func=cmd_guard)

    stats = sub.add_parser("stats", help="show observed job statistics")
    stats.set_defaults(func=cmd_stats)

    decision = sub.add_parser("decision", help="inspect or replay routing decisions")
    decision.add_argument("job_id", nargs="?", help="job id whose decision should be read")
    decision.add_argument("--replay", action="store_true", help="replay one stored decision")
    decision.add_argument("--replay-all", action="store_true", help="replay recent stored decisions")
    decision.add_argument("--limit", type=int, default=1000, help="maximum decisions for replay-all")
    decision.set_defaults(func=cmd_decision)

    reconcile = sub.add_parser("reconcile", help="detect provider/control-plane drift")
    reconcile.set_defaults(func=cmd_reconcile)

    policy_cmd = sub.add_parser("policy", help="show active execution policy")
    policy_cmd.set_defaults(func=cmd_policy)

    audit = sub.add_parser("audit", help="verify the audit chain")
    audit.set_defaults(func=cmd_audit)

    wal = sub.add_parser("wal", help="inspect write-ahead log state")
    wal.add_argument("--limit", type=int, default=100, help="maximum WAL records to inspect")
    wal.add_argument("--recovery", action="store_true", help="show recovery status")
    wal.add_argument("--recovery-plan", action="store_true", help="show recovery plan")
    wal.set_defaults(func=cmd_wal)

    authz = sub.add_parser("authz", help="evaluate an authorization decision")
    authz.add_argument("--principal", required=True, help="principal to authorize")
    authz.add_argument("--action", required=True, help="action to check")
    authz.add_argument("--scope", default="", help="optional authorization scope")
    authz.set_defaults(func=cmd_authz)

    approval = sub.add_parser("approval", help="manage explicit operator approvals")
    approval_sub = approval.add_subparsers(dest="approval_command", required=True)
    approval_create = approval_sub.add_parser("create", help="create an approval record")
    approval_create.add_argument("--principal", required=True, help="principal receiving the approval decision")
    approval_create.add_argument("--action", required=True, help="action being approved or denied")
    approval_create.add_argument("--approved", action="store_true", help="record approval instead of denial")
    approval_create.add_argument("--expires-at", type=int, help="Unix timestamp when approval expires")
    approval_create.add_argument("--reason", default="", help="human-readable approval reason")
    approval_create.set_defaults(func=cmd_approval)
    approval_check = approval_sub.add_parser("check", help="check whether an approval is active")
    approval_check.add_argument("--principal", required=True, help="principal to check")
    approval_check.add_argument("--action", required=True, help="action to check")
    approval_check.set_defaults(func=cmd_approval)
    approval_list = approval_sub.add_parser("list", help="list recent approvals")
    approval_list.add_argument("--limit", type=int, default=100, help="maximum approvals to list")
    approval_list.set_defaults(func=cmd_approval)

    timeout = sub.add_parser("timeout", help="evaluate timeout policy for a job")
    timeout.add_argument("job", help="path to a gpu-job JSON file")
    timeout.set_defaults(func=cmd_timeout)

    error_class = sub.add_parser("error-class", help="classify a provider error")
    error_class.add_argument("--error", default="", help="provider error text")
    error_class.add_argument("--status-code", type=int, help="HTTP or provider status code")
    error_class.add_argument("--provider", default="", help="provider name")
    error_class.set_defaults(func=cmd_error_class)

    remediation = sub.add_parser("remediation", help="choose deterministic remediation for a job")
    remediation.add_argument("job", help="path to a gpu-job JSON file")
    remediation.set_defaults(func=cmd_remediation)

    readiness = sub.add_parser("readiness", help="show launch readiness checks")
    readiness.add_argument("--limit", type=int, default=100, help="maximum records to inspect")
    readiness.set_defaults(func=cmd_readiness)

    invariants = sub.add_parser("invariants", help="evaluate job and provider invariants")
    invariants.add_argument("job", help="path to a gpu-job JSON file")
    invariants.add_argument("--provider", choices=["auto", *sorted(PROVIDERS)], default="auto", help="provider to evaluate")
    invariants.set_defaults(func=cmd_invariants)

    capabilities = sub.add_parser("capabilities", help="inspect model capability registry")
    capabilities_sub = capabilities.add_subparsers(dest="capability_command", required=True)
    capabilities_list = capabilities_sub.add_parser("list", help="list registered model capabilities")
    capabilities_list.set_defaults(func=cmd_capabilities)
    capabilities_check = capabilities_sub.add_parser("check", help="check a job against model capabilities")
    capabilities_check.add_argument("job", help="path to a gpu-job JSON file")
    capabilities_check.add_argument("--provider", required=True, choices=sorted(PROVIDERS), help="provider to check")
    capabilities_check.set_defaults(func=cmd_capabilities)

    attestation = sub.add_parser("attestation", help="compute expected job attestation hash")
    attestation.add_argument("job", help="path to a gpu-job JSON file")
    attestation.set_defaults(func=cmd_attestation)

    destructive = sub.add_parser("destructive-check", help="preflight a destructive provider action")
    destructive.add_argument("--principal", required=True, help="principal requesting the destructive action")
    destructive.add_argument("--action", required=True, help="destructive action name")
    destructive.add_argument("--target", default="", help="target resource")
    destructive.add_argument("--scope", default="", help="optional action scope")
    destructive.set_defaults(func=cmd_destructive)

    eval_cmd = sub.add_parser("eval", help="evaluate policy dimensions for a job")
    eval_sub = eval_cmd.add_subparsers(dest="eval_command", required=True)
    for name in ["quota", "cost", "secrets", "placement", "preemption"]:
        item = eval_sub.add_parser(name, help=f"evaluate {name} policy")
        item.add_argument("job", help="path to a gpu-job JSON file")
        item.add_argument("--provider", default="", help="provider context")
        item.set_defaults(func=cmd_eval)

    drain = sub.add_parser("drain", help="manage queue drain mode")
    drain_sub = drain.add_subparsers(dest="drain_command", required=True)
    drain_start = drain_sub.add_parser("start", help="start drain mode")
    drain_start.add_argument("--reason", default="", help="reason for draining")
    drain_start.set_defaults(func=cmd_drain)
    drain_clear = drain_sub.add_parser("clear", help="clear drain mode")
    drain_clear.set_defaults(func=cmd_drain)
    drain_status_parser = drain_sub.add_parser("status", help="show drain mode status")
    drain_status_parser.set_defaults(func=cmd_drain)

    metrics = sub.add_parser("metrics", help="show metrics snapshot")
    metrics.add_argument("--prometheus", action="store_true", help="emit Prometheus text format")
    metrics.set_defaults(func=cmd_metrics)

    retention = sub.add_parser("retention", help="show retention policy report")
    retention.set_defaults(func=cmd_retention)

    selftest = sub.add_parser("selftest", help="run deterministic local self-test")
    selftest.set_defaults(func=cmd_selftest)

    circuits = sub.add_parser("circuits", help="show provider circuit-breaker state")
    circuits.set_defaults(func=cmd_circuits)

    dlq = sub.add_parser("dlq", help="show dead-letter queue status")
    dlq.add_argument("--limit", type=int, default=100, help="maximum failed jobs to show")
    dlq.set_defaults(func=cmd_dlq)

    workflow = sub.add_parser("workflow", help="validate and inspect workflow records")
    workflow_sub = workflow.add_subparsers(dest="workflow_command", required=True)
    workflow_validate = workflow_sub.add_parser("validate", help="validate and store a workflow JSON file")
    workflow_validate.add_argument("workflow", help="path to workflow JSON file")
    workflow_validate.set_defaults(func=cmd_workflow)
    workflow_status = workflow_sub.add_parser("status", help="show a stored workflow")
    workflow_status.add_argument("workflow_id", help="workflow id to load")
    workflow_status.set_defaults(func=cmd_workflow)

    image = sub.add_parser("image", help="plan, check, or build worker images")
    image_sub = image.add_subparsers(dest="image_command", required=True)
    for name in ["plan", "check", "build"]:
        item = image_sub.add_parser(name, help=f"{name} a worker image")
        item.add_argument("--worker", default="asr", help="worker image family")
        if name == "build":
            item.add_argument("--execute", action="store_true", help="perform the remote/CI build action")
        item.set_defaults(func=cmd_image)
    image_mirror_parser = image_sub.add_parser("mirror", help="mirror an image into an operator-controlled registry")
    image_mirror_parser.add_argument("--source", required=True, help="source image reference, preferably digest-pinned")
    image_mirror_parser.add_argument("--target", required=True, help="target image reference in the operator registry")
    image_mirror_parser.add_argument("--builder", default="", help="SSH host that has Docker/buildx and registry credentials")
    image_mirror_parser.add_argument("--execute", action="store_true", help="perform the mirror operation")
    image_mirror_parser.set_defaults(func=cmd_image)

    runpod = sub.add_parser("runpod", help="RunPod-specific promotion helpers")
    runpod_sub = runpod.add_subparsers(dest="runpod_command", required=True)
    quarantine = runpod_sub.add_parser("quarantine-ollama", help="create a quarantined public-Ollama endpoint canary")
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

    pod_defaults = {
        "gpu_type_id": "NVIDIA GeForce RTX 3090",
        "image": "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
        "cloud_type": "ALL",
        "gpu_count": 1,
        "volume_in_gb": 0,
        "container_disk_in_gb": 20,
        "min_vcpu_count": 2,
        "min_memory_in_gb": 8,
        "max_uptime_seconds": 90,
        "max_estimated_cost_usd": 0.02,
        "docker_args": "bash -lc 'nvidia-smi; sleep 300'",
    }

    def add_pod_worker_args(item: argparse.ArgumentParser) -> None:
        item.add_argument("--gpu-type-id", default=pod_defaults["gpu_type_id"], help="RunPod concrete GPU type id")
        item.add_argument("--image", default=pod_defaults["image"], help="Pod image name")
        item.add_argument(
            "--cloud-type", default=pod_defaults["cloud_type"], choices=["ALL", "SECURE", "COMMUNITY"], help="RunPod cloud type"
        )
        item.add_argument("--gpu-count", type=int, default=pod_defaults["gpu_count"], help="GPU count")
        item.add_argument("--volume-in-gb", type=int, default=pod_defaults["volume_in_gb"], help="ephemeral pod volume size")
        item.add_argument("--container-disk-in-gb", type=int, default=pod_defaults["container_disk_in_gb"], help="container disk size")
        item.add_argument("--min-vcpu-count", type=int, default=pod_defaults["min_vcpu_count"], help="minimum vCPU count")
        item.add_argument("--min-memory-in-gb", type=int, default=pod_defaults["min_memory_in_gb"], help="minimum memory in GiB")
        item.add_argument("--max-uptime-seconds", type=int, default=pod_defaults["max_uptime_seconds"], help="hard lifecycle canary wait")
        item.add_argument(
            "--max-estimated-cost-usd",
            type=float,
            default=pod_defaults["max_estimated_cost_usd"],
            help="maximum allowed estimated canary cost",
        )
        item.add_argument("--docker-args", default=pod_defaults["docker_args"], help="pod start command")

    plan_pod = runpod_sub.add_parser("plan-pod-worker", help="plan a bounded RunPod Pod lifecycle canary")
    add_pod_worker_args(plan_pod)
    plan_pod.set_defaults(func=cmd_runpod)

    canary_pod = runpod_sub.add_parser("canary-pod-lifecycle", help="create, observe, and terminate a bounded RunPod Pod canary")
    add_pod_worker_args(canary_pod)
    canary_pod.add_argument("--execute", action="store_true", help="actually create and terminate the pod")
    canary_pod.set_defaults(func=cmd_runpod)

    canary_pod_http = runpod_sub.add_parser(
        "canary-pod-http-worker", help="create, health-check, and terminate a bounded RunPod Pod HTTP worker canary"
    )
    add_pod_worker_args(canary_pod_http)
    canary_pod_http.add_argument(
        "--network-volume-id",
        default=os.getenv("RUNPOD_NETWORK_VOLUME_ID", ""),
        help="optional approved RunPod network volume id",
    )
    canary_pod_http.add_argument(
        "--data-center-id",
        default=os.getenv("RUNPOD_DATA_CENTER_ID", ""),
        help="optional RunPod data center id required for network volume placement",
    )
    canary_pod_http.add_argument(
        "--worker-mode",
        choices=["smoke", "llm"],
        default="smoke",
        help="HTTP worker mode to verify",
    )
    canary_pod_http.add_argument("--prompt", default="", help="prompt for --worker-mode llm")
    canary_pod_http.add_argument("--execute", action="store_true", help="actually create and terminate the pod")
    canary_pod_http.set_defaults(func=cmd_runpod)

    vllm_defaults = {
        "model": "Qwen/Qwen2.5-0.5B-Instruct",
        "image": "runpod/worker-v1-vllm:v2.14.0",
        "gpu_ids": "ADA_24",
        "locations": "",
        "hf_secret_name": "gpu_job_hf_read",
        "max_model_len": 2048,
        "gpu_memory_utilization": 0.9,
        "max_concurrency": 1,
        "idle_timeout": 90,
        "workers_max": 1,
        "scaler_value": 15,
    }

    def add_vllm_endpoint_args(item: argparse.ArgumentParser) -> None:
        item.add_argument("--model", default=vllm_defaults["model"], help="Hugging Face model id or vLLM model path")
        item.add_argument("--image", default=vllm_defaults["image"], help="RunPod vLLM worker image")
        item.add_argument("--gpu-ids", default=vllm_defaults["gpu_ids"], help="RunPod GPU id list")
        item.add_argument("--locations", default=vllm_defaults["locations"], help="RunPod location filter")
        item.add_argument(
            "--network-volume-id",
            default=os.getenv("RUNPOD_NETWORK_VOLUME_ID", ""),
            help="optional approved RunPod network volume id",
        )
        item.add_argument(
            "--hf-secret-name",
            default=vllm_defaults["hf_secret_name"],
            help="RunPod secret name used as HF_TOKEN; empty disables HF_TOKEN env",
        )
        item.add_argument("--max-model-len", type=int, default=vllm_defaults["max_model_len"], help="vLLM MAX_MODEL_LEN")
        item.add_argument(
            "--gpu-memory-utilization",
            type=float,
            default=vllm_defaults["gpu_memory_utilization"],
            help="vLLM GPU_MEMORY_UTILIZATION",
        )
        item.add_argument("--max-concurrency", type=int, default=vllm_defaults["max_concurrency"], help="worker MAX_CONCURRENCY")
        item.add_argument("--idle-timeout", type=int, default=vllm_defaults["idle_timeout"], help="endpoint idle timeout seconds")
        item.add_argument("--workers-max", type=int, default=vllm_defaults["workers_max"], help="maximum serverless workers")
        item.add_argument("--scaler-value", type=int, default=vllm_defaults["scaler_value"], help="QUEUE_DELAY scaler value")
        item.add_argument("--quantization", default="", help="optional vLLM QUANTIZATION value")
        item.add_argument("--served-model-name", default="", help="optional OpenAI served model alias")
        item.add_argument("--flashboot", action="store_true", help="request RunPod FlashBoot")

    plan_vllm = runpod_sub.add_parser("plan-vllm-endpoint", help="plan a safe RunPod vLLM serverless endpoint")
    add_vllm_endpoint_args(plan_vllm)
    plan_vllm.set_defaults(func=cmd_runpod)

    promote_vllm = runpod_sub.add_parser("promote-vllm-endpoint", help="create and canary a RunPod vLLM endpoint")
    add_vllm_endpoint_args(promote_vllm)
    promote_vllm.add_argument("--canary-prompt", default="Return exactly: GPU SELF HOSTED OK", help="short canary prompt")
    promote_vllm.add_argument("--canary-timeout-seconds", type=int, default=300, help="bounded canary wait")
    promote_vllm.add_argument("--execute", action="store_true", help="actually create and canary the endpoint")
    promote_vllm.set_defaults(func=cmd_runpod)

    serve = sub.add_parser("serve", help="start the local/private HTTP API")
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
