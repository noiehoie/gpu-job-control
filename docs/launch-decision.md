# Launch Decision

Updated: 2026-04-18 JST.

## Decision

Launch proceeds without RunPod Serverless vLLM / Hub-template support.

That route is deferred because the current evidence shows endpoint/template creation works, but worker readiness and request dispatch do not become reliable through the public GraphQL or `runpodctl` path. Support escalation is useful, but it is not on the launch critical path.

## Production Routes

| Route | Launch Status | Rule |
| --- | --- | --- |
| Modal GPU worker | Primary | Use for external GPU `llm_heavy` and bursty short jobs when guard is clean. |
| Ollama on netcup | Fixed-capacity local | Use only within resource guard and token limits. |
| RunPod public OpenAI-compatible endpoint | Conditional | Use only when an existing endpoint is explicitly configured, health is clean, and no warm billable capacity is detected. |
| RunPod bounded Pod HTTP worker | Conditional batch route | Use only through create -> health/generate canary -> artifact verification -> terminate -> post-guard. |
| RunPod Network Volumes | Approved fixed-cost storage | Use only approved volumes within monthly storage budget. |
| Vast.ai | Canary / reserve | Use only through guarded serverless or explicitly bounded lifecycle routes; no direct unbounded instance execution. |

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

## Support Track

The RunPod Support packet remains in [runpod-support-question.md](runpod-support-question.md). It should be sent after launch pressure is off, or earlier only if RunPod Serverless vLLM becomes a hard requirement.
