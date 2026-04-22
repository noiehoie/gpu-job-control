# Provider Module Contracts

Date: 2026-04-22 JST

`gpu-job-control` does not replace provider-native runtimes. It records,
compares, and audits them through deterministic contracts.

The control plane must not reimplement RunPod Serverless workers, RunPod Pods,
Vast PyWorker, Vast instance boot logic, or Modal functions. Those are provider
execution planes. `gpu-job-control` owns the contract around them: requirements,
quote, approval, execution record, artifact verification, cleanup evidence, and
billing evidence.

## Contract Units

The top-level provider adapter keys remain stable:

```text
modal
runpod
vast
local
ollama
```

Provider module contracts are a lower-level visibility layer:

```text
modal_function
runpod_serverless
runpod_pod
vast_instance
vast_pyworker_serverless
```

Current implementation records the module contract in `workspace_plan` and
derived `PlanQuote` / `ExecutionRecord` fields. It does not yet route by module.
The parent provider adapter remains the execution key until routing-by-module is
explicitly enabled.

## Caller Input

Callers may request a provider module as recorded metadata:

```json
{
  "metadata": {
    "provider_module_id": "runpod_pod"
  }
}
```

`provider_contract_unit` is accepted as a compatibility alias. CLI callers can
also pass `--provider-module-id` or `--provider-contract-unit` to `validate`,
`plan`, `route`, `submit`, `enqueue`, and `intake`. API callers can pass the
same fields at the top level next to `provider`, or inside `job.metadata`.

This input is validated and echoed in plan/validation output. It does not change
the parent provider routing key. Invalid module requests fall back to the parent
provider default and are marked with `requested_module_valid=false`.

## Non-Reimplementation Boundary

Provider-native behavior must be delegated to provider-native tools:

| Provider module | Native source of behavior | gpu-job-control role |
| --- | --- | --- |
| `runpod_serverless` | RunPod Serverless v2 endpoint + worker SDK | quote endpoint use, record status/cancel/result/retention, verify artifacts, audit cost |
| `runpod_pod` | RunPod Pod / Network Volume / REST / GraphQL / runpodctl | quote pod/volume/proxy/SSH shape, record lifecycle and billing, guard cleanup |
| `vast_instance` | Vast offer + instance + base-image boot/provisioner | quote offer/disk/image/provisioning, record boot phases, destroy and verify no residue |
| `vast_pyworker_serverless` | Vast Endpoint + Workergroup + PyWorker | quote endpoint/workergroup/template, record worker state, healthcheck, benchmark, logs, residue |
| `modal_function` | Modal Function / Image / Volume | quote function/image/volume semantics, record startup/execution/retry/artifact evidence |

The following are out of scope for `gpu-job-control` production code:

- replacing provider job brokers;
- replacing provider worker frameworks;
- replacing provider image builders;
- replacing provider artifact upload primitives;
- inferring provider status with an LLM;
- allocating cloud GPUs before `requires_action` is resolved.

## Source Evidence

RunPod official repositories read and fixed by commit:

- `runpod/runpod-python`: Serverless client, worker contract, webhook, upload/download/cleanup/cache.
- `runpod/runpodctl`: REST v1, GraphQL, Pod, endpoint, billing, network volume, transfer commands.
- `runpod/containers`: official container and workspace patterns.
- `runpod/test-runner`: endpoint/template create and Serverless v2 run/status/cancel examples.
- `runpod/serverless-workers`: worker implementation patterns.

Vast official repositories read and fixed by commit:

- `vast-ai/vast-cli`: offers, instances, endpoints, workergroups, billing, serverless package.
- `vast-ai/vast-sdk`: SDK data and serverless contracts.
- `vast-ai/pyworker`: WorkerConfig / HandlerConfig / healthcheck / benchmark examples.
- `vast-ai/base-image`: `vast_boot.d`, provisioning manifest, supervisor, PyWorker boot gate.
- `vast-ai/docs`: serverless architecture, worker states, storage and instance behavior.

AI-Dock ComfyUI is not a provider module. It is an image/workspace pattern used
as evidence for prebuilt ComfyUI API-wrapper and supervisor-based workspaces.

## Required Records

Every provider module route must eventually produce:

- `PlanQuote`: selected parent provider, visible provider module, cost/time basis, rejected alternatives, required actions.
- `WorkspacePlan`: provider-native workspace and image contract, module contract, required actions, contract probe status.
- `ExecutionRecord`: provider resource IDs, status timeline, phase timings, artifact verification, cleanup result, billing evidence.
- Canary evidence: proof that provider-native behavior was observed in a live or approved test path.

## Canary Evidence Schema

`provider_module_canary_evidence` is the module-level audit surface for canary
proof. It is derived from `provider_contract_probe` artifact parsing and does
not allocate provider resources.

The schema has version `gpu-job-provider-module-canary-evidence-v1` and maps
each module's `canary_requirements` onto the shared provider-contract
observation categories:

```text
provider_resource_identity
image_contract
secret_availability
workspace_cache
startup_phases
queue_or_reservation
model_load
gpu_execution
artifact_contract
cost_guard
cleanup_result
provider_residue
```

All modules use the same category vocabulary. Module-specific requirements such
as `runpod_pod.terminate`, `vast_instance.destroy_instance`, and
`modal_function.function_invoke` are normalized to those categories so fixture
and live canaries can be compared without reimplementing provider runtimes.

`provider_module_canary_evidence` is read-side audit metadata. It does not
change `probe_name`, parent-provider routing, provider adapters, or
`routing_by_module_enabled`.

## Routing Feature Flag

The policy key `provider_module_routing.routing_by_module_enabled` exists only
as a design surface. The default and only currently valid value is `false`.

`config/execution-policy.json` records:

```json
{
  "provider_module_routing": {
    "routing_by_module_enabled": false,
    "activation_stage": "design_only",
    "canary_evidence_required": true
  }
}
```

Policy validation rejects `routing_by_module_enabled=true` until a separate
patch defines workspace plan hashing, idempotency, fallback semantics, and
provider adapter changes.

## CLI/API Proof Points

The schema is visible without live provider calls:

```text
uv run gpu-job-admin contract-probe schema
uv run gpu-job-admin contract-probe plan --provider runpod --probe runpod.asr_diarization.serverless_handler
GET /schemas/provider-module
GET /schemas/provider-contract-probe
```

Expected stable fields:

```text
provider_module_canary_evidence_version=gpu-job-provider-module-canary-evidence-v1
provider_module_routing_flag.current_allowed_values=[false]
provider_module_probe_name=runpod_serverless.asr_diarization.serverless_handler
provider_module_contract.selection.routing_by_module_enabled=false
```

## Current Implementation Boundary

`src/gpu_job/provider_module_contracts.py` declares the module contracts.

`src/gpu_job/workspace_registry.py` attaches `provider_module_contract` to the
workspace plan. This field is excluded from `workspace_plan_id` hashing because
it is currently additive visibility metadata. Changing module metadata must not
invalidate existing workspace records.

`src/gpu_job/execution_record.py` exposes the visible module contract in derived
plan quotes. `routing_by_module_enabled` is currently false.

## Promotion Path

1. Keep parent-provider routing stable.
2. Add module-specific canary definitions and deterministic module probe aliases.
3. Add CLI/API selection of `provider_module_id` as recorded metadata.
4. Add module-specific PlanQuote comparisons behind a feature flag.
5. Enable routing-by-module only after canaries prove status, cleanup, billing,
   and artifact contracts for each module.
