# Launch Decision

Updated: 2026-04-25 JST.

## Decision

Launch proceeds with Modal as the production primary route.

The finished provider surface is five execution systems:

1. Modal function execution: `modal_function`
2. Vast serverless pyworker execution: `vast_pyworker_serverless`
3. Vast direct instance execution: `vast_instance`
4. RunPod serverless handler execution: `runpod_serverless`
5. RunPod bounded Pod execution: `runpod_pod`

Provider modules are still audit and promotion metadata. They are not routing
keys while `provider_module_routing.routing_by_module_enabled=false`.

RunPod Serverless vLLM / Hub-template support remains deferred.

That route is deferred because the current evidence shows endpoint/template creation works, but worker readiness and request dispatch do not become reliable through the public GraphQL or `runpodctl` path. Support escalation is useful, but it is not on the launch critical path.

## Production Routes

| Route | Launch Status | Rule |
| --- | --- | --- |
| Modal GPU worker / `modal_function` | Production primary | Use for external GPU workloads allowed by the operation catalog when guard is clean. `llm.generate` production quality still requires 70B+ external GPU evidence. |
| Ollama on netcup | Fixed-capacity local | Use only within resource guard and token limits. |
| RunPod serverless handler / `runpod_serverless` | Contracted canary path | Approved endpoint identity evidence exists on netcup; production dispatch still remains endpoint-scoped and conservative. The lane is generic, though RunPod Serverless vLLM / Hub-template is not included. |
| RunPod bounded Pod HTTP worker / `runpod_pod` | Conditional batch route | Generic bounded GPU batch route; use only through create -> health/generate canary -> artifact verification -> terminate -> post-guard. |
| RunPod Network Volumes | Approved fixed-cost storage | Use only approved volumes within monthly storage budget. |
| Vast serverless pyworker / `vast_pyworker_serverless` | Canary / reserve | Generic pyworker GPU lane; use only after endpoint/workergroup canary evidence and cleanup proof. The reserve role is proven; it is not a production-primary route. |
| Vast direct instance / `vast_instance` | Canary / reserve | Generic direct-instance GPU lane; use only through guarded prebuilt-image lifecycle routes; no unbounded instance execution. |

## Deferred Routes

| Route | Reason |
| --- | --- |
| RunPod raw GraphQL Serverless vLLM | Workers remained unreachable or jobs stayed queued. |
| RunPod Hub/Console vLLM reproduction | Public CLI/API path has not reproduced Console/Hub deployment semantics. |
| Any newly created Serverless endpoint | Requires full promotion gates before production traffic. |

## Non-Negotiable Launch Gates

1. `gpu-job guard` reports no active billable compute resources.
2. Queue/capacity reports no active jobs before launch.
3. Provider stability keeps `modal` as production primary.
4. RunPod Serverless vLLM is not required for readiness.
5. Every paid provider action has a bounded runtime, cleanup path, and post-guard.
6. Network volume spend remains within the approved fixed storage budget.
7. `gpu-job-admin readiness --phase-report` reports Phase 0-2 pass before any provider canary, and Phase 3-5 remain blocked until their provider-specific canary evidence is present.
8. Provider module contracts remain audit metadata only; `provider_module_routing.routing_by_module_enabled` remains `false`.

## Current Launch-Gate Status

The Phase 0-5 gate is documented in [Launch Phase 0-5 Gate](launch-phase0-5-gate.md).
As of 2026-04-25 JST, the netcup clean clone reproduced the launch boundary on
commit `492310e9949552bdb407c1666cc873cdfbca1e31`.

The netcup `R0` logs now confirm:

- `pytest -q`: `355 passed, 14 subtests passed`
- `python -m unittest discover -s tests -q`: `Ran 313 tests ... OK`
- `gpu-job selftest`: `ok=true`
- `gpu-job validate examples/jobs/asr.example.json`: `ok=true`
- `ruff check`: `All checks passed!`
- `ruff format --check`: `122 files already formatted`

The netcup `R1` readiness log then confirmed:

- `phase_0_current_diff_fixed=true`
- `phase_1_contract_core_launch_candidate=true`
- `phase_2_runtime_config_cross_check=true`
- `phase_3_modal_canary=true`
- `phase_4_runpod_bounded_canary=true`
- `phase_5_vast_reserve_canary=true`
- `stop_conditions=[]`
- `provider_adapter_diff=[]`
- `routing_by_module_enabled=false`

Modal remains the production-primary route. RunPod Pod remains the conditional
batch route. RunPod serverless now has approved endpoint identity evidence on
netcup through `runpod.asr.official_whisper_smoke`, but it stays an
endpoint-scoped canary/contract path rather than a general production route.
That ASR-labeled evidence is historical proof of the serverless lane identity,
not an ASR-only product boundary. Vast direct instance and Vast pyworker
serverless remain generic reserve/canary lanes only.

The current repo-tracked netcup-backed serverless identity evidence is fixed in
`config/provider-operations.json` and backed by the following logs:

- RunPod serverless official smoke artifact:
  `docs/launch-logs/20260425-R1-runpod-serverless.out`
- Vast pyworker serverless artifact:
  `docs/launch-logs/20260425-R1-vast-serverless.out`

Both artifacts parse with `provider_module_canary_evidence.ok=true`, and the
netcup `R1` readiness run accepts them for Phase 4 and Phase 5.

Vast ASR/pyannote direct secret-gated runs remain separate from the reserve
serverless proof and still require Hugging Face token availability when that
specific path is exercised.

The follow-up netcup `R3` repeat cycle kept the launch state green after
re-running:

- Modal LLM canary: `docs/launch-logs/20260425-R3-modal-llm.json`
- RunPod bounded Pod canary: `docs/launch-logs/20260425-R3-runpod-pod.json`
- Vast direct instance canary: `docs/launch-logs/20260425-R3-vast-instance.json`
- final repeat readiness: `docs/launch-logs/20260425-R3-readiness-fixed.json`

`docs/launch-logs/20260425-R3-modal-asr.json` records the earlier netcup
secret-gate miss for the Modal ASR rerun. That gap is now closed by the tracked
provider-operations policy in `config/provider-operations.json`, which allows
`modal:contract-probe:asr -> hf_token` in clean clones and removes the need for
artifact re-append as the steady-state fix.

## Support Track

The RunPod Support packet remains in [runpod-support-question.md](runpod-support-question.md). It should be sent after launch pressure is off, or earlier only if RunPod Serverless vLLM becomes a hard requirement.
