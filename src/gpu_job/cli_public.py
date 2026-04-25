"""Public CLI transport surface.

Lane B keeps this file read-oriented and delegates public orchestration to
``gpu_job.public_ops``.
"""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import os
import shutil

from .audit import verify_audit_chain
from .authz import approval_ok, list_approvals
from .circuit import all_circuits
from .config import config_path, project_root
from .contracts import plan_workload
from .destructive import destructive_preflight
from .dlq import dlq_status
from .error_class import classify_error
from .image import image_check, image_contract_check, image_contract_plan, image_plan
from .models import Job
from .placement import placement_check
from .policy_engine import policy_activation_record
from .preemption import preemption_check
from .provider_contract_probe import (
    list_contract_probes,
    parse_contract_probe_artifact,
    plan_contract_probe,
    provider_contract_probe_schema,
    recent_contract_probe_summary,
)
from .provenance import expected_attestation_hash
from .quota import quota_check
from .remediation import remediation_decision
from .retention import retention_report
from .secrets_policy import secret_check
from .stats import collect_stats
from .store import JobStore
from .timeout import timeout_contract
from .timing import public_timing
from .verify import verify_artifacts
from .wal import wal_recovery_plan, wal_recovery_status, wal_status
from .workflow import list_workflows, load_workflow, plan_workflow


CONFIG_FILES = {
    "execution_policy": ("GPU_JOB_EXECUTION_POLICY", "execution-policy.json"),
    "gpu_profiles": ("GPU_JOB_PROFILES_CONFIG", "gpu-profiles.json"),
    "model_capabilities": ("GPU_JOB_CAPABILITIES_CONFIG", "model-capabilities.json"),
}


def print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def user_config_dir() -> Path:
    xdg_config = os.getenv("XDG_CONFIG_HOME", "").strip()
    if xdg_config:
        return Path(xdg_config).expanduser() / "gpu-job-control"
    return Path.home() / ".config" / "gpu-job-control"


def _read_json(path: str) -> dict:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("expected JSON object")
    return data


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
    config_checks = {}
    ok = True
    for name, (env_name, filename) in CONFIG_FILES.items():
        path = config_path(env_name, filename)
        exists = path.is_file()
        parsed = False
        error = ""
        if exists:
            try:
                json.loads(path.read_text())
                parsed = True
            except Exception as exc:
                error = str(exc)
                ok = False
        else:
            ok = False
        config_checks[name] = {"path": str(path), "exists": exists, "json_ok": parsed, "error": error}
    print_json({"ok": ok, "mode": "local_only", "configs": config_checks})
    return 0 if ok else 1


def cmd_selftest(_: argparse.Namespace) -> int:
    from .selftest import run_selftest

    result = run_selftest()
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_validate(args: argparse.Namespace) -> int:
    from .public_ops import validate_public_job

    payload = _read_json(args.job)
    result = validate_public_job(payload)
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_workload_plan(args: argparse.Namespace) -> int:
    from .public_ops import plan_public_job

    payload = _read_json(args.workload)
    job_keys = {"job_type", "input_uri", "output_uri", "worker_image", "gpu_profile"}
    if job_keys.issubset(payload):
        result = plan_public_job(payload)
    else:
        result = plan_workload(payload)
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_caller(args: argparse.Namespace) -> int:
    from .caller_contract import caller_request_schema, operation_catalog_snapshot, prompt_asset_snapshot

    if args.caller_command == "schema":
        result = caller_request_schema()
    elif args.caller_command == "catalog":
        result = operation_catalog_snapshot()
    elif args.caller_command == "prompt":
        result = prompt_asset_snapshot()
    else:
        raise ValueError(f"unknown caller command: {args.caller_command}")
    print_json(result)
    return 0 if result.get("ok", True) else 2


def cmd_queue(args: argparse.Namespace) -> int:
    jobs = JobStore().list_jobs(limit=args.limit)
    queued = [job.to_dict() for job in jobs if job.status == "queued"]
    print_json({"ok": True, "mode": "local_only", "queued": queued, "count": len(queued), "limit": args.limit})
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    store = JobStore()
    job = store.load(args.job_id)
    if args.timing:
        print_json({"job_id": job.job_id, "status": job.status, "timing_v2": public_timing(job)})
    else:
        print_json(job.to_dict())
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    result = verify_artifacts(Path(args.artifact_dir), required=args.required or None)
    print_json(result)
    return 0 if result["ok"] else 1


def cmd_contract_probe(args: argparse.Namespace) -> int:
    if args.contract_probe_command == "list":
        result = list_contract_probes()
    elif args.contract_probe_command == "schema":
        result = provider_contract_probe_schema()
    elif args.contract_probe_command == "summary":
        result = recent_contract_probe_summary(limit=args.limit)
    elif args.contract_probe_command == "plan":
        result = plan_contract_probe(args.provider, args.probe)
    elif args.contract_probe_command == "parse":
        result = parse_contract_probe_artifact(
            Path(args.artifact_dir),
            provider=args.provider,
            probe_name=args.probe,
            execution_mode="fixture" if args.fixture else "executed",
            append=False,
        )
    else:
        raise ValueError(f"unknown contract-probe command: {args.contract_probe_command}")
    print_json(result)
    return 0 if result.get("ok", True) else 2


def cmd_stats(_: argparse.Namespace) -> int:
    print_json(collect_stats())
    return 0


def cmd_decision(args: argparse.Namespace) -> int:
    if not args.job_id:
        raise ValueError("job_id is required")
    store = JobStore()
    store.ensure()
    path = store.root / "decisions" / f"{args.job_id}.json"
    if not path.is_file():
        result = {"ok": False, "error": "decision not found", "job_id": args.job_id, "path": str(path)}
    else:
        result = {"ok": True, "path": str(path), "decision": json.loads(path.read_text())}
    print_json(result)
    return 0 if result.get("ok") else 1


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
    result = approval_ok(args.action, args.principal) if args.approval else None
    if result is None:
        from .authz import authorize

        result = authorize(args.principal, args.action, scope=args.scope)
    print_json(result)
    return 0 if result.get("ok") else 3


def cmd_approval(args: argparse.Namespace) -> int:
    if args.approval_command == "check":
        result = approval_ok(args.action, args.principal)
    elif args.approval_command == "list":
        result = list_approvals(limit=args.limit)
    else:
        raise ValueError(f"unknown approval command: {args.approval_command}")
    print_json(result)
    return 0 if result.get("ok") else 3


def cmd_timeout(args: argparse.Namespace) -> int:
    result = timeout_contract(Job.from_file(Path(args.job)))
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_error_class(args: argparse.Namespace) -> int:
    print_json(classify_error(args.error, status_code=args.status_code, provider=args.provider))
    return 0


def cmd_remediation(args: argparse.Namespace) -> int:
    result = remediation_decision(Job.from_file(Path(args.job)))
    print_json(result)
    return 0 if result.get("ok") else 1


def cmd_capabilities(args: argparse.Namespace) -> int:
    if args.capability_command == "list":
        path = config_path("GPU_JOB_CAPABILITIES_CONFIG", "model-capabilities.json")
        result = {"ok": True, "path": str(path), "registry": json.loads(path.read_text())}
    else:
        raise ValueError(f"unknown capability command: {args.capability_command}")
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_attestation(args: argparse.Namespace) -> int:
    print_json({"ok": True, "subject_sha256": expected_attestation_hash(Job.from_file(Path(args.job)))})
    return 0


def cmd_destructive(args: argparse.Namespace) -> int:
    result = destructive_preflight(args.action, args.principal, target=args.target, scope=args.scope)
    print_json(result)
    return 0 if result.get("ok") else 3


def cmd_eval(args: argparse.Namespace) -> int:
    job = Job.from_file(Path(args.job))
    if args.eval_command == "quota":
        result = quota_check(job)
    elif args.eval_command == "secrets":
        result = secret_check(job, args.provider)
    elif args.eval_command == "placement":
        result = placement_check(job, args.provider)
    elif args.eval_command == "preemption":
        result = preemption_check(job)
    else:
        raise ValueError(f"unknown eval command: {args.eval_command}")
    print_json(result)
    return 0 if result.get("ok") else 2


def cmd_retention(_: argparse.Namespace) -> int:
    result = retention_report()
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
    if args.workflow_command == "list":
        print_json(list_workflows(limit=args.limit))
        return 0
    if args.workflow_command == "status":
        result = load_workflow(args.workflow_id)
        print_json(result)
        return 0 if result.get("ok") else 1
    if args.workflow_command == "plan":
        result = plan_workflow(_read_json(args.workflow))
        print_json(result)
        return 0 if result.get("ok") else 2
    raise ValueError(f"unknown workflow command: {args.workflow_command}")


def cmd_image(args: argparse.Namespace) -> int:
    if args.image_command == "plan":
        result = image_plan(args.worker)
    elif args.image_command == "check":
        result = image_check(args.worker)
    elif args.image_command == "contract-plan":
        result = image_contract_plan(args.contract_id)
    elif args.image_command == "contract-check":
        result = image_contract_check(args.contract_id)
    else:
        raise ValueError(f"unknown image command: {args.image_command}")
    print_json(result)
    return 0 if result.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gpu-job")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check local configuration without contacting providers")
    doctor.set_defaults(func=cmd_doctor)

    selftest = sub.add_parser("selftest", help="run deterministic local self-test")
    selftest.set_defaults(func=cmd_selftest)

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

    caller = sub.add_parser("caller", help="inspect caller-facing prompt, schema, and operation catalog")
    caller_sub = caller.add_subparsers(dest="caller_command", required=True)
    caller_sub.add_parser("schema", help="show caller request schema").set_defaults(func=cmd_caller)
    caller_sub.add_parser("catalog", help="show caller operation catalog").set_defaults(func=cmd_caller)
    caller_sub.add_parser("prompt", help="show current caller prompt asset metadata").set_defaults(func=cmd_caller)

    workload_plan = sub.add_parser("workload-plan", help="plan a workload contract without contacting providers")
    workload_plan.add_argument("workload", help="path to workload JSON file")
    workload_plan.set_defaults(func=cmd_workload_plan)

    queue = sub.add_parser("queue", help="show durable queue status")
    queue.add_argument("--limit", type=int, default=100, help="maximum queued jobs to show")
    queue.set_defaults(func=cmd_queue)

    status = sub.add_parser("status", help="show one stored job")
    status.add_argument("job_id", help="job id to load")
    status.add_argument("--timing", action="store_true", help="show Status/Timing v2 phase table and events")
    status.set_defaults(func=cmd_status)

    verify = sub.add_parser("verify", help="verify an artifact directory")
    verify.add_argument("artifact_dir", help="artifact directory to verify")
    verify.add_argument("--required", action="append", help="required artifact filename; may be repeated")
    verify.set_defaults(func=cmd_verify)

    contract_probe = sub.add_parser("contract-probe", help="inspect or parse provider contract probe records")
    contract_probe_sub = contract_probe.add_subparsers(dest="contract_probe_command", required=True)
    contract_probe_sub.add_parser("list", help="list built-in provider contract probes").set_defaults(func=cmd_contract_probe)
    contract_probe_sub.add_parser("schema", help="show provider contract probe record schema").set_defaults(func=cmd_contract_probe)
    contract_probe_summary = contract_probe_sub.add_parser("summary", help="show recent provider contract probe records")
    contract_probe_summary.add_argument("--limit", type=int, default=100, help="maximum records to inspect")
    contract_probe_summary.set_defaults(func=cmd_contract_probe)
    contract_probe_plan = contract_probe_sub.add_parser("plan", help="show expected contract for a provider probe")
    contract_probe_plan.add_argument("--provider", required=True, help="provider to plan")
    contract_probe_plan.add_argument("--probe", default="", help="explicit probe name")
    contract_probe_plan.set_defaults(func=cmd_contract_probe)
    contract_probe_parse = contract_probe_sub.add_parser("parse", help="parse an artifact directory against a provider contract")
    contract_probe_parse.add_argument("artifact_dir", help="artifact directory to parse")
    contract_probe_parse.add_argument("--provider", required=True, help="provider whose contract applies")
    contract_probe_parse.add_argument("--probe", default="", help="explicit probe name")
    contract_probe_parse.add_argument("--fixture", action="store_true", help="mark the record as fixture-sourced")
    contract_probe_parse.set_defaults(func=cmd_contract_probe)

    stats = sub.add_parser("stats", help="show observed job statistics")
    stats.set_defaults(func=cmd_stats)

    decision = sub.add_parser("decision", help="inspect a stored routing decision without replaying provider signals")
    decision.add_argument("job_id", help="job id whose decision should be read")
    decision.set_defaults(func=cmd_decision)

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
    authz.add_argument("--approval", action="store_true", help="check approval records instead of authz policy")
    authz.set_defaults(func=cmd_authz)

    approval = sub.add_parser("approval", help="inspect explicit operator approvals")
    approval_sub = approval.add_subparsers(dest="approval_command", required=True)
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

    capabilities = sub.add_parser("capabilities", help="inspect model capability registry")
    capabilities_sub = capabilities.add_subparsers(dest="capability_command", required=True)
    capabilities_sub.add_parser("list", help="list registered model capabilities").set_defaults(func=cmd_capabilities)

    attestation = sub.add_parser("attestation", help="compute expected job attestation hash")
    attestation.add_argument("job", help="path to a gpu-job JSON file")
    attestation.set_defaults(func=cmd_attestation)

    destructive = sub.add_parser("destructive-check", help="preflight a destructive provider action")
    destructive.add_argument("--principal", required=True, help="principal requesting the destructive action")
    destructive.add_argument("--action", required=True, help="destructive action name")
    destructive.add_argument("--target", default="", help="target resource")
    destructive.add_argument("--scope", default="", help="optional action scope")
    destructive.set_defaults(func=cmd_destructive)

    eval_cmd = sub.add_parser("eval", help="evaluate local policy dimensions for a job")
    eval_sub = eval_cmd.add_subparsers(dest="eval_command", required=True)
    for name in ["quota", "secrets", "placement", "preemption"]:
        item = eval_sub.add_parser(name, help=f"evaluate {name} policy")
        item.add_argument("job", help="path to a gpu-job JSON file")
        item.add_argument("--provider", default="", help="provider context")
        item.set_defaults(func=cmd_eval)

    retention = sub.add_parser("retention", help="show retention policy report")
    retention.set_defaults(func=cmd_retention)

    circuits = sub.add_parser("circuits", help="show provider circuit-breaker state")
    circuits.set_defaults(func=cmd_circuits)

    dlq = sub.add_parser("dlq", help="show dead-letter queue status")
    dlq.add_argument("--limit", type=int, default=100, help="maximum failed jobs to show")
    dlq.set_defaults(func=cmd_dlq)

    workflow = sub.add_parser("workflow", help="plan and inspect workflow records")
    workflow_sub = workflow.add_subparsers(dest="workflow_command", required=True)
    workflow_list = workflow_sub.add_parser("list", help="list stored workflows")
    workflow_list.add_argument("--limit", type=int, default=100, help="maximum workflows to show")
    workflow_list.set_defaults(func=cmd_workflow)
    workflow_status = workflow_sub.add_parser("status", help="show a stored workflow")
    workflow_status.add_argument("workflow_id", help="workflow id to load")
    workflow_status.set_defaults(func=cmd_workflow)
    workflow_plan = workflow_sub.add_parser("plan", help="plan and estimate a workflow JSON file")
    workflow_plan.add_argument("workflow", help="path to workflow JSON file")
    workflow_plan.set_defaults(func=cmd_workflow)

    image = sub.add_parser("image", help="plan or check worker images without building")
    image_sub = image.add_subparsers(dest="image_command", required=True)
    for name in ["plan", "check"]:
        item = image_sub.add_parser(name, help=f"{name} a worker image")
        item.add_argument("--worker", default="asr", help="worker image family")
        item.set_defaults(func=cmd_image)
    image_contract_plan_parser = image_sub.add_parser("contract-plan", help="plan one registered image contract")
    image_contract_plan_parser.add_argument("contract_id", help="image contract id")
    image_contract_plan_parser.set_defaults(func=cmd_image)
    image_contract_check_parser = image_sub.add_parser("contract-check", help="check one registered image contract")
    image_contract_check_parser.add_argument("contract_id", help="image contract id")
    image_contract_check_parser.set_defaults(func=cmd_image)

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
