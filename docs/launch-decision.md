# Launch Decision

Updated: 2026-04-22 JST.

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
| Modal GPU worker / `modal_function` | Production primary | Use for external GPU `llm_heavy`, ASR diarization, and bursty short jobs when guard is clean. |
| Ollama on netcup | Fixed-capacity local | Use only within resource guard and token limits. |
| RunPod serverless handler / `runpod_serverless` | Contracted canary path | ASR handler contract exists; production dispatch waits for serverless workspace parity. RunPod Serverless vLLM / Hub-template is not included. |
| RunPod bounded Pod HTTP worker / `runpod_pod` | Conditional batch route | Use only through create -> health/generate canary -> artifact verification -> terminate -> post-guard. |
| RunPod Network Volumes | Approved fixed-cost storage | Use only approved volumes within monthly storage budget. |
| Vast serverless pyworker / `vast_pyworker_serverless` | Canary / reserve | Use only after endpoint/workergroup canary evidence and cleanup proof. |
| Vast direct instance / `vast_instance` | Canary / reserve | Use only through guarded prebuilt-image lifecycle routes; no unbounded instance execution. |

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
As of 2026-04-22 JST, Phase 0, Phase 1, Phase 2, Phase 3, and the bounded
RunPod guard path pass. RunPod no longer reports active billable Pods after
explicit operator-approved cleanup. Modal LLM cache was prewarmed and then
validated by two passing `modal.llm_heavy.qwen2_5_32b` probes. Modal ASR
diarization was validated by two passing `modal.asr_diarization.pyannote`
probes. Vast serverless and Vast direct instance remain reserve/canary only.
The Vast direct prebuilt-image lifecycle passed twice with
`vast.instance_smoke.cuda`: instances `35390103` and `35397598` created,
reported `NVIDIA GeForce RTX 5060 Ti`, completed `GPU_JOB_SMOKE_DONE`, were
destroyed, and the post-guard reported no Vast.ai billable resources. A follow-up
audit-complete smoke run, instance `35399672`, recorded
`provider_module_canary_evidence.ok=true` with no missing or failed required
categories for `vast_instance`.

RunPod `runpod.asr_diarization.serverless_handler` was attempted on
2026-04-22 JST and created Pod `tlcnopcuaqw3s5`, but it timed out before HTTP
worker readiness or artifact generation. The Pod was removed after explicit
operator approval; post-guard and orphan inventory reported no active RunPod
billable resources.

Vast `vast.asr.serverless_template` was attempted on 2026-04-22 JST, but the
current execution path produced direct instance smoke evidence
(`provider_job_id=35406597`) rather than endpoint/workergroup pyworker evidence.
That result must not promote `vast_pyworker_serverless`; the module audit gate
now requires endpoint ID and workergroup ID evidence for that module.

Vast ASR/pyannote still stops before GPU allocation with
`failure.class=secret_block` because no local Hugging Face token is available.

## Support Track

The RunPod Support packet remains in [runpod-support-question.md](runpod-support-question.md). It should be sent after launch pressure is off, or earlier only if RunPod Serverless vLLM becomes a hard requirement.
