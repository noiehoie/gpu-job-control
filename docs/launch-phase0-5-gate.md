# Launch Phase 0-5 Gate

Date: 2026-04-25 JST

This gate records the launch path from contract freeze through provider
canaries. It is intentionally conservative: provider canaries cannot start
while any billing guard is red.

## Command

```text
uv run gpu-job-admin readiness --phase-report --limit 20
```

The command is admin-only because it reads live provider guard state. The phase
gate checks paid launch providers only: Modal, RunPod, and Vast. Local Ollama
disk guard remains visible through the broader `readiness` command, but it does
not block external provider canaries.

## Current Boundary

- `provider_module_id` and `provider_contract_unit` are audit metadata only.
- Parent provider routing remains `modal`, `runpod`, `vast`, `local`, or
  `ollama`.
- `provider_module_routing.routing_by_module_enabled` must remain `false`.
- Provider adapters remain outside this launch-gate patch.
- Mac Studio must not build, probe, or push Docker images.

## Finished Provider Surface

The launch target is five execution systems:

| System | Module id | Current launch status |
| --- | --- | --- |
| Modal function execution | `modal_function` | production primary after repeat LLM and ASR canaries |
| Vast serverless pyworker execution | `vast_pyworker_serverless` | reserve/canary with endpoint/workergroup evidence fixed on netcup |
| Vast direct instance execution | `vast_instance` | reserve/canary with direct smoke lifecycle evidence |
| RunPod serverless handler execution | `runpod_serverless` | contracted canary path with approved endpoint evidence on netcup; RunPod Serverless vLLM/Hub-template deferred |
| RunPod bounded Pod execution | `runpod_pod` | conditional batch route with explicit create/verify/terminate lifecycle |

These module IDs are not routing keys yet. They are contract and audit metadata
until a separate routing feature flag is designed and approved.

## Phase Gates

| Phase | Gate | Launch meaning |
| --- | --- | --- |
| Phase 0 | policy valid, module routing false, true rejected, provider adapter diff empty, guard clean, contract-probe schema exposes module canary evidence | current diff can be frozen as the audit baseline |
| Phase 1 | `01_contract_core=locally_verified`, `02_runtime_binding=locally_verified_after_ci`, `03_lifecycle_reconciliation=locally_verified_conservative_only` | contract core launch candidate |
| Phase 2 | `05_runtime_configuration=needs_provider_slice_cross_check`, unverified provider-image routing remains blocked, module routing false | runtime config is planning-only until provider canaries pass |
| Phase 3 | guard clean, Modal LLM and ASR contract probe evidence present, Modal slice remains high-risk until repeat live canary | Modal production primary evidence |
| Phase 4 | no RunPod billable resources, bounded Pod canary evidence separated from serverless endpoint evidence, RunPod slice remains high-risk, Serverless vLLM deferred | RunPod remains bounded Pod/conditional route; approved serverless endpoint evidence is present but still conservative |
| Phase 5 | guard clean, Vast direct instance evidence separated from Vast pyworker endpoint/workergroup evidence, Vast slice remains high-risk, Vast primary forbidden | Vast remains reserve/canary only, with pyworker endpoint/workergroup evidence fixed |

## Stop Conditions

Stop immediately when any of these are true:

- paid-provider billing guard fails;
- provider adapter diff is present before canary evidence parity;
- `routing_by_module_enabled` is not `false`;
- policy no longer rejects `routing_by_module_enabled=true`;
- provider canary would require Docker on Mac Studio;
- serverless module evidence contains only Pod or instance identity instead of
  endpoint/workergroup identity;
- destructive cleanup lacks explicit approval, fresh provider read, and
  destructive preflight.

## Destructive Cleanup Questions

Before terminating any active provider resource, answer these three questions
for each target:

1. Is the resource still serving a current production or diagnostic role?
2. Are attached volumes, logs, or artifacts needed before termination?
3. Is the account expected to keep any `gpu-job-pod-canary` resource alive
   during launch preparation?

## Current Run Result

The netcup clean clone run on commit `492310e9949552bdb407c1666cc873cdfbca1e31`
produced two relevant checkpoints.

`R0` (code-quality baseline) reported:

```text
phase_0_current_diff_fixed=true
phase_1_contract_core_launch_candidate=true
phase_2_runtime_config_cross_check=true
phase_3_modal_canary=true
phase_4_runpod_bounded_canary=false
phase_5_vast_reserve_canary=false
provider_adapter_diff=[]
routing_by_module_enabled=false
stop_conditions=[]
pytest=349 passed, 14 subtests passed
selftest.ok=true
validate.ok=true
ruff check=All checks passed!
ruff format --check=125 files already formatted
```

`R1` (serverless identity freeze) then reported:

```text
phase_0_current_diff_fixed=true
phase_1_contract_core_launch_candidate=true
phase_2_runtime_config_cross_check=true
phase_3_modal_canary=true
phase_4_runpod_bounded_canary=true
phase_5_vast_reserve_canary=true
provider_adapter_diff=[]
routing_by_module_enabled=false
stop_conditions=[]
```

The specific serverless artifacts accepted by `R1` were:

```text
runpod.asr.official_whisper_smoke
log=docs/launch-logs/20260425-R1-runpod-serverless.out
provider_module_canary_evidence.ok=true

vast.asr.serverless_template
log=docs/launch-logs/20260425-R1-vast-serverless.out
provider_module_canary_evidence.ok=true
```

The `R3` repeat cycle then ended with:

```text
phase_0_current_diff_fixed=true
phase_1_contract_core_launch_candidate=true
phase_2_runtime_config_cross_check=true
phase_3_modal_canary=true
phase_4_runpod_bounded_canary=true
phase_5_vast_reserve_canary=true
provider_adapter_diff=[]
routing_by_module_enabled=false
stop_conditions=[]
```

Repo-tracked repeat logs:

- `docs/launch-logs/20260425-R3-modal-llm.json`
- `docs/launch-logs/20260425-R3-modal-asr.json`
- `docs/launch-logs/20260425-R3-runpod-pod.json`
- `docs/launch-logs/20260425-R3-vast-instance.json`
- `docs/launch-logs/20260425-R3-readiness-fixed.json`

The historical first live `readiness --phase-report` after adding this gate
reported the following before stale RunPod Pods were terminated:

```text
ok=false
phase_1_contract_core_launch_candidate=true
phase_2_runtime_config_cross_check=true
phase_0_current_diff_fixed=false
phase_3_modal_canary=false
phase_4_runpod_bounded_canary=false
phase_5_vast_reserve_canary=false
provider_adapter_diff=[]
routing_by_module_enabled=false
routing_true_rejected=true
stop_conditions=[billing_guard_failed]
```

The blocking guard facts were:

```text
runpod.billable_count=2
runpod.estimated_hourly_usd=0.44
runpod.reason=RunPod active pods or warm serverless workers present
```

The RunPod orphan reaper dry run classified both active resources as zombies but
skipped destruction because cleanup evidence was missing. This is the intended
conservative launch behavior.

After explicit operator confirmation that neither Pod had any current
production, diagnostic, log, artifact, or launch-prep role, the two Pods were
stopped and then terminated through RunPod GraphQL `podTerminate`. Post-guard
reported:

```text
runpod.billable_count=0
runpod.estimated_hourly_usd=0
runpod.reason=no RunPod active pods or warm serverless workers; persistent storage within approved fixed-cost budget
runpod.orphan_inventory.candidate_count=0
```

## Modal Canary Attempts

Modal initially remained blocked for production-primary promotion on
2026-04-22 JST. The guard was clean, but live canary evidence did not satisfy
the provider contract.

Two `modal.llm_heavy.qwen2_5_32b` contract-probe runs submitted successfully,
completed the provider job, verified artifacts, and observed the expected GPU,
but both failed the cache contract:

```text
probe=modal.llm_heavy.qwen2_5_32b
jobs=contract-probe-llm_heavy-20260422-051536-43b15222, contract-probe-llm_heavy-20260422-051749-55d25451
failure.class=cache_contract_missing
failure.reason=cache contract missing or cold model download observed
checks.artifact_contract_ok=true
checks.gpu_contract_ok=true
checks.verify_ok=true
observed.cache.cache_hit=false
observed.cache.cold_start_observed=true
```

One `modal.asr_diarization.pyannote` contract-probe run also completed cleanup
but failed model and artifact verification:

```text
probe=modal.asr_diarization.pyannote
job=contract-probe-asr-20260422-051916-4620caf0
failure.class=model_contract_mismatch
failure.reason=observed model does not satisfy provider contract
checks.artifact_contract_ok=false
checks.model_match=false
checks.verify_ok=false
provider_message=Could not download 'large-v3' pipeline. It might be because the pipeline is private or gated so make sure to authenticate.
```

Modal cannot move from `controlled_canary` to `production_primary` until repeat
LLM cache evidence and ASR model/artifact verification pass.

## Modal Canary Fix And Repeat Evidence

After reading the Modal official repositories
`modal-labs/modal-client`, `modal-labs/modal-examples`, and
`modal-labs/open-batch-transcription`, two implementation facts were applied:

- Modal model cache should use a Volume-mounted Hugging Face hub cache path
  consistently, matching the official Volume cache examples.
- The Modal ASR diarization probe must keep `large-v3` as the Whisper ASR model
  while passing `pyannote/speaker-diarization-3.1` as the speaker pipeline.

The Qwen 32B cache was warmed before repeating the contract probe:

```text
modal run src/gpu_job/modal_llm.py::warm_cache --model-name Qwen/Qwen2.5-32B-Instruct --artifact-dir /Users/tamotsu/.local/share/gpu-job-control/modal-warm-cache-20260422-053226
ok=true
cache_hit_before_download=false
cache_hit_after_download=true
runtime_seconds=183.062
```

Two Modal LLM contract probes then passed:

```text
probe=modal.llm_heavy.qwen2_5_32b
jobs=contract-probe-llm_heavy-20260422-053610-6feab33f, contract-probe-llm_heavy-20260422-053837-c43af5d8
checks.artifact_contract_ok=true
checks.cache_contract_ok=true
checks.gpu_contract_ok=true
checks.model_match=true
checks.verify_ok=true
observed.cache.cache_hit=true
observed.hardware.gpu_name=NVIDIA A100 80GB PCIe
```

Two Modal ASR diarization contract probes then passed:

```text
probe=modal.asr_diarization.pyannote
jobs=contract-probe-asr-20260422-053753-693e459d, contract-probe-asr-20260422-054001-c0148f82
checks.artifact_contract_ok=true
checks.model_match=true
checks.verify_ok=true
observed.model=pyannote/speaker-diarization-3.1
observed.workspace_contract.hf_token_present=true
observed.hardware.gpu_name=NVIDIA A10
```

Post-guard reported:

```text
modal.billable_count=0
modal.reason=no running Modal apps
```

## Vast Phase 5 Preflight

Vast direct instance remains reserve/canary. The live ASR preflight now reaches
the correct fixture path and stops before GPU allocation when required secrets
are absent.

```text
probe=vast.asr_diarization.pyannote
job=contract-probe-asr-20260422-055934-cafdb10b
failure.class=secret_block
failure.reason=Vast speaker diarization Hugging Face token is missing
submit_result.provider_job_id=
input_uri=/Users/tamotsu/Projects/runpodmaking/gpu-job-control/fixtures/audio/asr-ja.wav
```

The GHCR credential path was verified through the local GitHub CLI token, but
no local Hugging Face token was present:

```text
gh auth status -> token scopes include write:packages
HF cache token: absent
HF config token: absent
vastai show instances --raw -> []
vast.guard.reason=no Vast.ai billable resources
```

Phase 5 does not promote until `HF_TOKEN`, `HUGGINGFACE_TOKEN`, or
`HUGGING_FACE_HUB_TOKEN` is available and the guarded create -> SSH ready ->
tiny ASR/diarization -> artifact verify -> destroy -> post-guard lifecycle
passes twice. Vast production primary remains forbidden.

The direct prebuilt-image lifecycle path was separately proven twice with
bounded CUDA smoke probes:

```text
probe=vast.instance_smoke.cuda
job=contract-probe-smoke-20260422-060957-fea5a74d
provider_job_id=35390103
result.ok=true
runtime_seconds=93
observed.hardware.gpu_name=NVIDIA GeForce RTX 5060 Ti, 16311 MiB, 580.95.05
checks.artifact_contract_ok=true
checks.gpu_contract_ok=true
checks.image_match=true
post_submit_guard.vast.reason=no Vast.ai billable resources
```

Provider logs for instance `35390103` included:

```text
GPU_JOB_SMOKE_START
NVIDIA GeForce RTX 5060 Ti, 16311 MiB, 580.95.05
GPU_JOB_SMOKE_DONE
```

Second repeat:

```text
probe=vast.instance_smoke.cuda
job=contract-probe-smoke-20260422-093918-ef9cf1c1
provider_job_id=35397598
result.ok=true
runtime_seconds=103
observed.hardware.gpu_name=NVIDIA GeForce RTX 5060 Ti, 16311 MiB, 570.195.03
checks.artifact_contract_ok=true
checks.gpu_contract_ok=true
checks.image_match=true
post_submit_guard.vast.reason=no Vast.ai billable resources
vastai show instances --raw -> []
```

Provider logs for instance `35397598` included:

```text
GPU_JOB_SMOKE_START
NVIDIA GeForce RTX 5060 Ti, 16311 MiB, 570.195.03
GPU_JOB_SMOKE_DONE
```

After the submit guard evidence was added to the contract-probe record, the
direct smoke path was re-run and the module-level canary evidence became
audit-complete for the `vast_instance` required categories:

```text
probe=vast.instance_smoke.cuda
job=contract-probe-smoke-20260422-104304-876e5c6b
provider_job_id=35399672
result.ok=true
runtime_seconds=100
provider_module_canary_evidence.ok=true
provider_module_canary_evidence.missing_categories=[]
provider_module_canary_evidence.failed_categories=[]
observed_categories=provider_resource_identity,image_contract,startup_phases,queue_or_reservation,model_load,gpu_execution,artifact_contract,cost_guard,cleanup_result,provider_residue
post_submit_guard.vast.reason=no Vast.ai billable resources
vastai show instances --raw -> []
```

Provider logs for instance `35399672` included:

```text
GPU_JOB_SMOKE_START
NVIDIA GeForce RTX 5060 Ti, 16311 MiB, 570.181
GPU_JOB_SMOKE_DONE
```

## RunPod Serverless Handler Canary

The first `runpod.asr_diarization.serverless_handler` live attempt created a
bounded Pod path rather than proving a serverless endpoint. It timed out before
HTTP worker readiness and artifact generation:

```text
probe=runpod.asr_diarization.serverless_handler
job=contract-probe-asr-20260422-131310-b9a739e1
provider_job_id=tlcnopcuaqw3s5
failure.class=provider_timeout
observed_http_worker=false
generate_ok=false
provider_module_canary_evidence.ok=false
```

After explicit operator approval, `podStop` was not sufficient because the Pod
remained `EXITED` and billable in the guard. The Pod was then removed through
RunPod GraphQL `podTerminate(input:{podId:"tlcnopcuaqw3s5"})`. Post-guard
reported no RunPod active Pods or warm serverless workers, and orphan inventory
reported zero candidates.

## Vast Pyworker Serverless Canary

The first `vast.asr.serverless_template` live attempt did not prove the
pyworker/serverless module. It created direct Vast instance smoke evidence:

```text
probe=vast.asr.serverless_template
job=contract-probe-asr-20260422-140436-24c1a50e
provider_job_id=35406597
observed.instance_id=35406597
observed.endpoint_id=
observed.workergroup_id=
```

That evidence remains useful only for direct lifecycle cleanup observation. It
must not promote `vast_pyworker_serverless`; the module-level audit gate now
requires endpoint ID and workergroup ID evidence before the pyworker/serverless
requirements can pass.
