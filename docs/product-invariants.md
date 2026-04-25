# Product Invariants

These invariants are release-blocking. They are not feature toggles to relax
during launch pressure.

## Launch Invariants

- `phase_0_current_diff_fixed=true`
- `phase_1_contract_core_launch_candidate=true`
- `phase_2_runtime_config_cross_check=true`
- `phase_3_modal_canary=true`
- `phase_4_runpod_bounded_canary=true`
- `phase_5_vast_reserve_canary=true`
- `provider_adapter_diff=[]`
- `routing_by_module_enabled=false`
- `stop_conditions=[]`

## Public Product Invariants

- Public caller requests use `gpu-job-caller-request-v1`.
- External GPUs are strictly for workloads unsuitable for local fixed resources.
- Production `llm.generate` judges require 70B+ class models and are strictly external.
- ASR is a validation workload, not a provider-lane product boundary.
- All five cloud GPU lanes remain generic candidates for closed public GPU operations.
- Public callers choose `operation`, not provider-specific `job_type`.
- Free-form public operations are rejected.
- Same valid caller request compiles to the same internal plan.
- Ambiguous caller requests fail closed before provider submission.
- Secrets are never accepted in caller payloads.
- Provider module metadata is audit evidence only, not production routing logic.
- Destructive provider cleanup is explicitly policy-gated.

## Runtime Role Invariants

- Modal is the production primary route after repeat LLM and ASR canary evidence.
- RunPod Pod is a bounded conditional batch route.
- RunPod Serverless is approved endpoint only.
- Vast direct instance and Vast pyworker serverless are generic reserve/canary routes.
- Vast must not be promoted to production primary without a new release gate.

## Verification

The release candidate must run:

```bash
uv run python -m gpu_job.cli readiness --phase-report
git diff -- src/gpu_job/providers
```

The first command must show the launch invariants above. The second command
must be empty unless a separate provider-adapter release review explicitly
authorizes the change.
