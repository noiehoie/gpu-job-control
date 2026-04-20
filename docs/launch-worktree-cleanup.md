# Launch Worktree Cleanup

Date: 2026-04-20 JST

This document classifies the current dirty worktree for launch preparation. The
goal is to keep gpu-job-control as a deterministic, generic GPU broker module,
not a caller-specific patch surface.

## Rule

Do not prune files piecemeal. The contract stack, provider capability stack, and
workflow execution stack are now linked. They must be reviewed, tested, and
landed as coherent groups.

No production source in this repository may encode news-system-specific behavior.
gpu-job-control is a deterministic generic cloud-GPU broker; caller-specific
policy belongs in caller payloads, manifests, or external configuration.

Current status warning:

- The physical worktree is not cleanly sliced yet. It still contains cumulative
  in-progress changes from Slice 1 through Slice 5.
- A slice being listed below does not mean those files are already ready to
  land together.
- Verification of one slice means only that the named contract for that slice
  has been checked; it does not approve unrelated modified files that happen to
  exist in the same worktree.
- Before commit or release, each slice must be isolated into its own reviewable
  diff or explicitly documented as an intentional combined release unit.

## Ignore / Isolate

The following are local state or experiment artifacts. They are intentionally
ignored by `.gitignore` and must not be treated as production source:

- `.aider.chat.history.md`
- `.aider.input.history`
- `.opencode/`
- `ai-audit/`
- `tasks/`
- `scripts/continue_vast_asr_instance.sh`
- `scripts/vast_asr_30s_canary.sh`

The `ai-audit/` directory remains useful as local provenance for design review,
but production source should cite distilled docs under `docs/` instead.

## Council Decision

The 2026-04-20 council review used Gemini, Composer 2, Kimi K2.5, and
Grok. The engines converged on the same operating rule: land this work as
contract-first slices, not as one broad provider patch.

The 2026-04-21 council refresh used Composer 2, Kimi K2.5, Gemini, and web
source discovery for official provider documentation. The refreshed decision is
stricter: Slice 1 through Slice 3 may continue only as contract, quote, record,
workflow, and evidence work. Slice 4 and later provider/workspace implementation
is blocked until the provider documentation research gate below is complete.

Roles for the remaining launch cleanup:

- PM / scope guard: Grok-style review. Reject broad diffs and keep this document
  current.
- Implementer: main Codex. Apply only one agreed slice at a time.
- Auditor: Composer-style review. Check determinism, evidence paths, no runtime
  dependency installation, and no caller-specific leakage.
- Provider reviewers: provider-specific engine review for Vast, RunPod, and
  Modal before any live canary or provider behavior change.

Council stop conditions:

- Any live canary leaves billable provider residue or reports `cleanup_ok=false`.
- Any production provider path performs runtime `pip install`, `apt`, or
  equivalent dependency installation instead of using a verified image contract.
- Any core file encodes news-system-specific behavior.
- Any slice mixes contract-core changes with provider execution changes.
- Any plan cannot explain provider, GPU, image, cost, time estimate, and cleanup
  evidence using deterministic records.

Provider documentation research gate:

- This gate blocks `ProviderWorkspaceRegistry` expansion beyond pure local
  contract loading, provider-specific execution changes, provider-specific
  canary broadening, lifecycle reaper apply modes, and production route
  broadening for Modal, RunPod, or Vast.
- The research role must read the current official documentation and operational
  community discussions for each cloud GPU provider before Slice 4 work starts.
- The output must be distilled into committed docs under `docs/`, not left only
  in local `ai-audit/` transcripts.
- The current gate artifact is `docs/cloud-gpu-provider-research.md`.
- Each provider brief must separate documented fact, community operational
  reports, local observed evidence, and assumptions that still require canary
  proof.
- Any assumption that affects resource allocation, billing, cleanup, image
  preparation, volume/cache behavior, warm/cold state, secrets, artifacts, or
  timeout handling must become either a deterministic contract field, a
  `requires_action`, or an explicit blocked state.

Minimum source set for the provider documentation research gate:

- Modal official docs: images, GPU resources, volumes, secrets, function
  lifecycle, cold start and snapshot behavior, timeout/retry behavior, and
  deployment/image rebuild semantics.
- RunPod official docs: serverless overview, endpoint settings, active workers,
  FlashBoot, execution timeout, job status APIs, network volumes, S3-compatible
  volume access, pod lifecycle, secrets, and endpoint/pod cleanup semantics.
- Vast official docs: Docker execution environment, templates/base images,
  instance lifecycle, SSH/onstart behavior, label/metadata support, CLI/API
  state transitions, pricing/billing state, and termination semantics.
- Community operational discussions: cold start behavior, network volume
  availability, model cache persistence, image pull/setup delays, endpoint
  worker readiness, provider residue/orphan cases, and practical cleanup
  failures. Community reports may inform risk and tests, but must not override
  official API contracts without local canary evidence.

Required provider brief sections:

- Resource model: what object is allocated, how it is identified, and how it is
  terminated.
- Workspace model: image, filesystem, volume/cache, secrets, and artifact paths.
- Timing model: queue time, image pull/build time, cold start, model load, GPU
  execution, upload/download, cleanup, and idle retention.
- Cost model: billable states, minimum charges or idle charges where documented,
  storage charges, and cost fields available from API or local estimates.
- Status model: states that can be polled, terminal states, retry states, and
  ambiguous states.
- Failure model: timeout, preemption/eviction, insufficient capacity, auth,
  missing image, missing secret, artifact failure, cleanup failure, and orphan
  detection.
- Canary requirements: minimum live test that proves the workspace contract and
  the cleanup contract without relying on caller-specific behavior.

Step 4+ reopen condition:

- PM confirms the three provider briefs exist.
- Auditor confirms every provider-specific implementation task references a
  documented provider fact or an explicit canary assumption.
- Coder confirms the next diff is still one isolated launch slice.

RunPod serverless ASR decision:

- Add only contract / planner / probe skeletons until serverless observation
  reaches the same categories as Vast r8 and RunPod pod:
  image contract, startup phases, queue/warm state, cache, secret availability,
  worker startup, artifact contract, cost guard, cleanup, and provider residue.
- Do not enable production RunPod serverless ASR dispatch from the generic
  router before that evidence exists.

RunPod / Vast workspace observation coverage decision:

- Step 8 added a read-side `gpu-job-workspace-observation-coverage-v1`
  normalization surface under provider contract probe records.
- Coverage is observational only. Existing contract checks remain the pass/fail
  gate, and no provider allocation path was broadened.
- RunPod pod ASR and Vast ASR fixtures can now be compared against the same
  `WORKSPACE_OBSERVATION_CATEGORIES` set without live GPU calls in tests.

Latest RunPod pod evidence baseline:

- `job_id`: `contract-probe-asr-20260420-143245-6cfd3710`
- `provider_job_id`: `m7rioa08c0bi3y`
- `runtime_seconds`: `122`
- `workspace_contract_ok`: `true`
- `cleanup_ok`: `true`
- `cache_hit`: `true`
- `hf_token_present`: `true`
- `post_submit_guard.active_total`: `0`

## Slice 0: Cleanup Metadata

These files are part of the cleanup itself and should be reviewed with this
inventory:

- `.gitignore`
- `docs/cloud-gpu-provider-research.md`
- `docs/launch-slice-manifest.json`
- `docs/launch-worktree-cleanup.md`

Acceptance:

- This slice does not modify production behavior.
- Ignore rules cover local lab/audit state only.
- This document names slice boundaries, dependencies, role ownership, and stop
  conditions.
- `docs/launch-slice-manifest.json` assigns every current dirty path to exactly
  one primary launch review unit. The current validation result is
  `dirty_count=68`, `assigned_count=68`, `missing=[]`, `extra=[]`, and
  `duplicates=[]`.

## Slice 1: Contract And Broker Core

These files define the new public contract and deterministic broker model:

- `config/image-contracts.json`
- `config/requirement-registry.json`
- `src/gpu_job/concurrency.py`
- `src/gpu_job/contracts.py`
- `src/gpu_job/execution_plan.py`
- `src/gpu_job/execution_record.py`
- `src/gpu_job/image_contracts.py`
- `src/gpu_job/plan_quote.py`
- `src/gpu_job/provider_catalog.py`
- `src/gpu_job/provider_contract_probe.py`
- `src/gpu_job/provider_probe.py`
- `src/gpu_job/requirements.py`
- `src/gpu_job/timing.py`
- `src/gpu_job/workspace_registry.py`

Do not include provider execution behavior in this slice. `src/gpu_job/image.py`
must be split carefully: pure contract helpers belong here, while image build,
push, pull, and provider image operations belong in provider/worker slices.

Required tests for this stack:

- `tests/test_asr_diarization_contract.py`
- `tests/test_image_contracts.py`
- `tests/test_production_contracts.py`
- `tests/test_provider_catalog_contracts.py`
- `tests/test_provider_contract_probe.py`
- `tests/test_requirement_registry.py`
- `tests/test_timing_v2.py`

Acceptance:

- `PlanQuote`, `ExecutionRecord`, `ProviderWorkspaceRegistry`,
  requirement registry, image contract registry, provider catalog, and contract
  probe records are deterministic and testable without cloud allocation.
- Provider catalog entries expose a deterministic
  `gpu-job-provider-support-contract-v1` support contract with the agreed
  support levels: `registered`, `catalog_routable`, `canary_executable`, and
  `production_route`.
- Runtime requirements that are not registered produce `requires_action` or
  `requires_backend_registration`, not implicit execution.
- Provider adapters do not receive permission to allocate GPU when
  `workspace_plan.decision=requires_action`.
- `ProviderWorkspaceRegistry` may include provider-documented workspace modes,
  mount paths, timing fields, and cleanup semantics, but must remain a pure
  local registry with no provider API calls.
- Public schemas expose `requires_action` decisions and allowed required-action
  types so callers can stop deterministically before GPU allocation.
- Provider contract probe schemas expose a shared workspace observation category
  list so Modal, RunPod, and Vast canaries are compared at the same granularity.

## Slice 2: Workflow And Policy Binding

These existing files now participate in the public contract path and should be
reviewed with the core stack:

- `src/gpu_job/api.py`
- `src/gpu_job/capacity.py`
- `src/gpu_job/cli.py`
- `src/gpu_job/queue.py`
- `src/gpu_job/router.py`
- `src/gpu_job/runner.py`
- `src/gpu_job/workflow.py`
- `src/gpu_job/workflow_helpers.py`
- `src/gpu_job/verify.py`
- `src/gpu_job/policy.py`
- `src/gpu_job/policy_engine.py`
- `src/gpu_job/error_class.py`

Required tests for this stack:

- `tests/test_api_response_schema.py`
- `tests/test_policy_router.py`
- `tests/test_workflow_planner.py`

Why:

- `api.py` exposes contract schemas and workflow endpoints.
- `runner.py` binds execution to `plan_quote` / `workflow_plan_quote`.
- `workflow.py` propagates plan quotes and workspace snapshots to child jobs.
- `capacity.py`, `router.py`, and policy files now understand per-provider,
  per-profile concurrency.
- `verify.py` and `error_class.py` are part of the audit boundary.

Council sub-slices:

### Slice 2.1: Policy, Concurrency, Queue, Router

Files:

- `src/gpu_job/policy.py`
- `src/gpu_job/policy_engine.py`
- `src/gpu_job/capacity.py`
- `src/gpu_job/queue.py`
- `src/gpu_job/router.py`
- `src/gpu_job/error_class.py`
- `src/gpu_job/concurrency.py` (shared with Slice 1, reviewed here as runtime
  binding)
- `config/execution-policy.json`
- `tests/test_policy_router.py`

Acceptance:

- Provider limits are provider/profile aware.
- Queue and capacity use the same provider/profile key.
- Router decisions are deterministic and return reason codes, not LLM-derived
  judgments.
- Malformed policy is rejected before runtime use.

Diagnostic status:

- `tests/test_policy_router.py`: 13 passed.
- `py_compile` and `git diff --check` passed for Slice 2.1 files.
- Core provider/profile concurrency implementation is accepted as locally
  healthy by council diagnostic review.

Exact-diff blocker:

- The provider/profile concurrency runtime is exact-diff approved.
- The provider operations split is exact-diff approved as Slice 2.1-A:
  generic `execution-policy.json` no longer carries live RunPod persistent
  storage allowances or probe `hf_token` scopes.
- `provider-operations.example.json` is the safe committed template. Local live
  provider IDs and secret scopes belong in private provider-operations files such
  as `config/provider-operations.local.json`; this local file is excluded via
  `.git/info/exclude`, not as a production source file.
- Remaining operational risk: deployments that used `persistent_storage` or
  `secret_policy` inside `execution-policy.json` must move those keys to a
  provider operations file or set `GPU_JOB_PROVIDER_OPERATIONS_POLICY`.

### Slice 2.2: Public Contract API And Verification Surface

Files:

- `src/gpu_job/api.py`
- `src/gpu_job/cli.py` (schema, planning, policy, and diagnostic command
  surface only)
- `src/gpu_job/verify.py` (schema / artifact verification surface only)
- `tests/test_api_response_schema.py`

Acceptance:

- Public schema endpoints expose stable version markers:
  `gpu-job-plan-quote-v1`, `gpu-job-execution-record-v1`,
  `gpu-job-provider-workspace-registry-v1`, and `gpu-job-contract-v1`.
- CLI commands expose the same contract/planning diagnostics without invoking
  provider allocation.
- Tests use only localhost and local temporary stores.
- No provider API, cloud allocation, or live serverless endpoint is contacted.

Diagnostic status:

- `tests/test_api_response_schema.py`: 3 passed, 4 subtests passed for the
  initial schema surface diagnostic.
- Slice 2.2-A targeted verification:
  `tests/test_api_response_schema.py tests/test_policy_router.py`: 19 passed,
  7 subtests passed.
- `py_compile` and `git diff --check` passed for Slice 2.2-A files.
- Public schema endpoints and stable response fields are accepted as locally
  healthy by council diagnostic review.

Exact-diff status:

- Slice 2.2-A public API exact diff is council-approved. The public HTTP API no
  longer exposes active catalog probe execution, contract-probe execution, or
  manual workflow advancement:
  `POST /catalog/probe`, `POST /catalog/contract-probe`, and
  `POST /workflows/advance` now return a generic unknown-endpoint 404 before
  job deserialization.
- Read-only catalog/schema endpoints and planning endpoints remain public.
- Slice 2.2-B public CLI exact diff is council-approved. The caller-facing
  `gpu-job` entry point now resolves to `gpu_job.cli_public:main` and is limited
  to local/static planning, local state inspection, schema/contract inspection,
  and non-executing verification. It does not import provider adapters at
  startup and does not expose live provider API, canary, destructive cleanup, or
  execution commands.
- The full operational CLI remains available as `gpu-job-admin`, backed by the
  existing `gpu_job.cli:main` parser. Provider-specific canaries, live provider
  probes, image build/mirror/probe operations, queue workers, submit/enqueue,
  workflow execution/approval/drain, and reaper apply modes belong to that
  administrative surface or later provider slices.

### Slice 2.3: Runner Binding And Execution Records

Files:

- `src/gpu_job/runner.py`
- `src/gpu_job/execution_record.py`
- `src/gpu_job/execution_plan.py`
- `src/gpu_job/plan_quote.py`
- `src/gpu_job/contracts.py` (PlanQuote generation surface only)
- `src/gpu_job/verify.py` (execution-bound verification only)
- `tests/test_production_contracts.py` runner/execution-record cases

Acceptance:

- `plan_quote` selected provider is the execution source of truth.
- Explicit provider mismatch is rejected before provider allocation.
- `workspace_plan.decision=requires_action` blocks execution before GPU
  allocation.
- Workspace plan drift between plan and execute is rejected before GPU
  allocation.
- Execution records are written for terminal outcomes and do not copy raw
  provider errors.

Diagnostic status:

- `tests/test_production_contracts.py`: 16 passed.
- `py_compile` and `git diff --check` passed for Slice 2.3 files.
- Initial council audit found a real mismatch: `runner._plan_quote` preferred
  `workflow_plan_quote` for non-helper jobs, while `execution_record` could
  record the child `plan_quote`. The fix made the quote-selection logic
  identical in both files.
- Post-fix council audit also checked the empty `workflow_plan_quote={}` case;
  execution records now fall back to the child quote just like the runner.

Exact-diff status:

- Slice 2.3 is council-approved at the exact-diff level after the quote binding
  fixes above.
- This does not approve the rest of the mixed physical worktree, especially
  Slice 2.1 config changes and Slice 2.2 active API / CLI surfaces.

### Slice 2.4: Workflow Orchestration And Helper Jobs

Files:

- `src/gpu_job/workflow.py`
- `src/gpu_job/workflow_helpers.py`
- `tests/test_workflow_planner.py`

Acceptance:

- Workflow `plan_quote` is propagated to children.
- Child workspace plans are derived from the workflow quote, not stale template
  metadata.
- Non-text splitters create CPU helper jobs instead of doing heavy work in the
  API server.
- Budget hard-cap drift drains queued children deterministically.

Diagnostic status:

- `tests/test_workflow_planner.py`: 17 passed.
- `py_compile` and `git diff --check` passed for Slice 2.4 files.
- Initial council audit found that `advance_workflows` could inspect
  `requires_action` / `pending_approval` manifests. The fix makes those states
  non-runnable for workflow advancement and adds a regression test.
- Post-fix council audit then found that `enforce_workflow_budget_drains`
  skipped `requires_action` but not `pending_approval`. The fix makes
  `pending_approval`, `requires_action`, and `draining` non-drainable and adds
  a regression test.

Exact-diff status:

- Slice 2.4 is council-approved at the exact-diff level after the workflow state
  fixes above.
- Remaining risks are operational follow-ups, not launch blockers for this
  slice: media reducer naming is ASR-oriented, worker images must include
  `ffmpeg` / `ffprobe`, and future non-media reducers may need strategy-specific
  extraction.

Slice 2 diagnostic commands are allowed, but their results do not approve
Slice 2 while the physical worktree remains mixed with provider slices. Treat
them as triage only until each sub-slice is isolated.

## Slice 3: Lifecycle And Reconciliation

These files define provider-side lifecycle observation and reconciliation. This
slice must land before any destructive orphan reaper is enabled:

- `src/gpu_job/orphan_inventory.py`
- `src/gpu_job/providers/vast.py` (lifecycle phase observation)
- `tests/test_vast_orphan_inventory.py`
- `tests/test_vast_asr_provider.py`

Launch condition:

- Vast must expose a phase-aware timeline for direct instance execution.
- `vast-orphan-inventory` remains dry-run only until the lifecycle model is
  tested and reviewed.
- Full orphan reaping must be implemented only after lifecycle phases exist.
- Reaper apply mode must be gated by exact provider identifiers, terminal local
  state, cleanup phase evidence, and a fresh provider read.
- Vast workspace observation must expose the same categories used by RunPod pod
  contract probes before destructive reaping is enabled.

### Slice 3-A: Vast Lifecycle Observation

Slice 3-A is limited to evidence-first observation. It may normalize
`timing_v2` phase evidence, Vast provider state snapshots, orphan candidate
categories, and dry-run inventory output. It must not enable or broaden
destructive reaper apply behavior.

Accepted 3-A candidate categories:

- `ghost`: a labelled active Vast instance exists, but no local job file exists.
- `job_unreadable`: a local job file exists but cannot be parsed, so the
  instance is report-only and must not be collapsed into `ghost`.
- `zombie`: a terminal local job still has an active matching Vast instance.
- `id_mismatch`: a non-terminal local job exists, but its `provider_job_id`
  differs from the labelled Vast instance id.

Reaper eligibility in this observation slice requires exact provider id match,
terminal local job state, and `cleaning_up` phase evidence with both enter and
exit events. Cleanup exit may be successful or failed; the evidence records the
exit status and error class so 3-B can decide the destructive policy explicitly.

This 3-A scope is not the full RunPod-parity workspace observation gate. The
remaining RunPod-parity categories, such as image contract, secret/cache
availability, worker startup, artifact contract, cleanup result, and provider
residue, remain a later provider/workspace observation gate before destructive
reaper apply or production ASR dispatch is broadened.

Step 9/10 update:

- Vast orphan candidates now include deterministic provider lifecycle phase
  evidence derived from `actual_status` / `cur_state`.
- Vast orphan candidates also include deterministic job lifecycle evidence from
  `timing_v2` / `timing_summary`, including open phases and cleanup spans.
- Reaper apply remains conservative: only `terminal_job_active_instance` with an
  exact provider id match, terminal local job state, cleanup phase evidence,
  destructive preflight, principal, and fresh provider read can be destroyed.
- `provisioning`, `starting`, `loading`, `stopping`, and `exiting` style
  provider phases are explicitly non-destroyable even when the local job is
  terminal.
- `ghost`, `job_unreadable`, and `id_mismatch` remain report-only. They are not
  launch-approved for destructive cleanup.

## Slice 4: Provider And Worker Changes: High Risk

These changes are necessary for ASR/diarization work, but have the largest blast
radius and require separate provider canaries before launch. Review providers as
separate sub-slices rather than as one undifferentiated patch:

- `docker/asr-worker.Dockerfile`
- `src/gpu_job/workers/asr.py`
- `src/gpu_job/modal_asr.py`
- `src/gpu_job/modal_llm.py`
- `src/gpu_job/modal_vlm.py`
- `src/gpu_job/providers/modal.py`
- `src/gpu_job/providers/runpod.py`
- `src/gpu_job/providers/vast.py`
- `src/gpu_job/image.py`

`src/gpu_job/image.py` is intentionally listed in both the core and high-risk
sections: its `image_contract_*` functions are contract surface, while its build
and provider image operations affect provider runtime behavior.

Required tests for this stack:

- `tests/test_image_distribution.py`
- `tests/test_modal_llm_quality.py`
- `tests/test_modal_provider.py`
- `tests/test_vast_asr_provider.py`
- `tests/test_runpod_config.py`

Launch condition:

- No runtime dependency installation in provider adapters for production paths.
- Provider execution must consume `ExecutionPlan` / `ProviderWorkspaceRegistry`.
- RunPod ASR diarization must pass a workspace canary before production ASR
  dispatch is allowed.
- Vast direct ASR r8 remains evidence, not a template for unguarded production
  execution.
- Vast and RunPod ASR canaries must report the same observation categories:
  image contract, token/secret availability, workspace/cache readiness, worker
  startup, artifact contract, cleanup result, and provider resource residue.

### Slice 4a: Vast Direct Instance

Vast work must first finish Slice 3 lifecycle observation, then use it as the
basis for any reaper or production direct-instance dispatch.

### Slice 4b: RunPod Serverless ASR

RunPod serverless ASR remains blocked for production dispatch until its workspace
contract/canary matches the Vast r8 observation granularity.

Allowed now:

- Contract and planner skeletons.
- Explicit serverless endpoint/workspace schema.
- Read-only plan/estimate output and required-action responses.

Blocked now:

- Generic router production dispatch to RunPod serverless ASR.
- Auto-discovery of serverless ASR endpoints.
- Live serverless canaries without an agreed observation schema and stop
  conditions.

### Slice 4c: Modal

Modal changes are reviewed separately from Vast and RunPod. Modal fixes must not
be used to justify weakening generic provider contracts.

## Slice 5: Runtime Config Changes

- `config/execution-policy.json`
  - Provider limits are now profile-aware, e.g. `modal:asr` and
    `runpod:llm_heavy`.
  - This must remain paired with `src/gpu_job/concurrency.py`.
- `config/gpu-profiles.json`
  - Adds `asr_diarization`.
  - This profile must not be routed to unverified provider images.

## Verification Baselines

- RunPod pod ASR contract probe baseline:
  `contract-probe-asr-20260420-143245-6cfd3710` passed live with
  `cleanup_ok=true` and `post_submit_guard.active_total=0`.
- Current local CI-equivalent gate after Step 12 cleanup:
  `compileall`, `unittest discover`, `ruff check`, `ruff format --check`,
  `pytest`, `gpu-job selftest`, and
  `gpu-job validate examples/jobs/asr.example.json` all pass.
- Latest full test counts:
  `pytest`: 247 passed, 7 subtests passed.
  `unittest discover`: 247 tests OK.
- Latest scoped RunPod contract tests:
  `tests/test_runpod_config.py tests/test_provider_contract_probe.py
  tests/test_asr_diarization_contract.py`: 61 passed.
- Full-suite counts may differ as slices are separated. Each slice must record
  its own exact command and result instead of relying on stale aggregate counts.

## Remaining Cleanup Gates

1. Isolate Slice 0 and Slice 1 from the broader dirty worktree before treating
   them as launchable.
2. Review Slice 0 and Slice 1 before changing provider behavior.
3. Review Slice 2 before connecting contracts to runtime execution.
4. Review Slice 3 before adding any destructive cleanup path.
5. Review provider/worker changes by provider sub-slice, not as one patch.
6. Convert provider-specific lab scripts into formal `contract-probe` entries or
   keep them ignored.
7. Add RunPod serverless ASR contract/plan skeleton before live serverless work.
8. Only after serverless canary evidence matches Vast r8 / RunPod pod categories,
   allow production ASR diarization dispatch to RunPod serverless.

## Slice Status

| Slice | Status | Evidence | Not Yet Approved |
| --- | --- | --- | --- |
| 0: Cleanup metadata | In progress | `.gitignore` isolates local lab/audit files; this document records council boundaries | Physical worktree still contains future slices |
| 1: Contract and broker core | Locally verified | Slice 1 targeted tests: 82 passed after provider support contract and documented workspace modes; full suite now 247 passed, 7 subtests after ExecutionPlan, workspace observation coverage, and lifecycle/reaper tests; py_compile passed previously; diff check passed | Commit isolation and final audit of the exact Slice 1 diff |
| 2: Workflow and policy binding | Mixed sub-slice status | Slice 2 diagnostic tests: 44 passed, 4 subtests passed; 2.1-A provider operations split approved after 28 targeted tests, 4 subtests; 2.2-A public API surface approved after 19 targeted tests, 7 subtests; 2.2-B public/admin CLI split approved after 27 targeted tests, 7 subtests; 2.3 exact diff approved after 16 targeted tests; 2.4 exact diff approved after 17 targeted tests | Commit isolation from remaining provider/lifecycle slices |
| 3: Lifecycle and reconciliation | Mixed sub-slice status | 3-A Vast orphan evidence and phase-aware conservative reaper approved locally after 32 targeted tests; Step 8 RunPod/Vast workspace observation coverage approved locally after 56 targeted tests | Provider/worker live behavior remains Slice 4; ghost/id-mismatch destructive cleanup remains blocked |
| 4: Provider and worker changes | Not approved | RunPod pod baseline exists; provider changes require separate sub-slice audit | Vast, RunPod serverless, Modal, worker image changes |
| 5: Runtime config changes | Not approved | None in this cleanup pass | Provider limits, profiles, production routing changes |

## Next Implementation Order

1. Land `00_launch_metadata` so launch boundaries and provider research facts
   are reviewable independently.
2. Land `01_contract_core` so PlanQuote, ExecutionPlan, workspace registry,
   requirement registry, image contracts, provider catalog, timing, and
   provider contract probes become stable module contracts.
3. Land `02_runtime_binding` so public/admin CLI split, API schemas, policy,
   router, runner, queue, workflow, circuit, WAL, and verification surfaces bind
   to the contracts without exposing live provider execution through public
   caller paths.
4. Land `03_lifecycle_reconciliation` so Vast orphan inventory and conservative
   reaper evidence are in place before direct-instance work is promoted.
5. Land `05_runtime_configuration` only after confirming profiles and execution
   policy do not route production jobs to unverified provider images.
6. Review provider slices independently and in this order for fastest reliable
   GPU availability:
   `04_provider_common`, `04_modal`, `04_runpod`, `04_vast`.
7. Promote each provider only after its canary proves image contract, workspace
   readiness, startup timing, cost guard, artifact contract, cleanup result, and
   provider residue evidence.
