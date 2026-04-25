## Serverless Community Consensus Snapshot

Date: 2026-04-25 JST

Scope:

- Public/community signal around RunPod serverless and Vast.ai serverless
- Cross-checked against this repo's April 2026 canaries and council audit
- Synthesis only; launch policy, provider adapters, and routing gates do not
  change here

Related evidence:

- `docs/council-audit/runpod-community-scan-20260424.jsonl`
- `docs/council-audit/provider-community-consensus-20260424.jsonl`
- `docs/research/runpod-serverless-phase0-20260423.md`
- `docs/research/serverless-official-path-20260424.md`

## RunPod: points with broad agreement

| Symptom | Community reading | Current repo fit |
| --- | --- | --- |
| `IN_QUEUE` for a long time with `throttled=1`, `ready=0`, `initializing=0` | Capacity or placement pressure on the serverless pool; sometimes account-level limits | Matches current account behavior across GraphQL, REST, CLI, and official public templates |
| `workersStandby=1` even when `workersMin=0` | Hidden warm-capacity or scaler behavior that operators do not expect from scale-to-zero | Matches current guard findings; keep fail-closed |
| `desiredStatus=EXITED` before first success | Often image pull failure, boot crash, env/CUDA mismatch, or provider-side worker-init failure | Mixed: still appeared on official/public paths, so not enough to blame only custom images |
| Long `initializing` or `loading` | Normal cold start for large images/models, but indistinguishable from stuck startup until logs are checked | Treat as startup evidence, not success |

Additional current repo fit:

- Official `runpodctl` create and official public template runs still reproduced
  `workersStandby=1`, `IN_QUEUE`, and `EXITED` on this account.
- Public CLI/docs parity remains unclear; docs mention `--hub-id`, but the
  tested CLI surface did not expose it.

## RunPod: disputed or unresolved points

1. Whether Hub/Console deployment adds backend metadata that public REST/CLI
   create surfaces do not reproduce.
2. Whether `workersStandby=1` is an intended scaler feature, account-level
   policy, or a provider bug.
3. Whether `IN_QUEUE` with a `ready` worker is dispatch failure or simply
   extreme capacity pressure.

## Vast: points with broad agreement

| Symptom | Community reading | Current repo fit |
| --- | --- | --- |
| `manifest not found` / registry pull failures | Image/tag issue first, especially on `:latest` | Matches `vastai/vllm:latest` failures |
| Worker stuck in `loading` / `model_loading` | Startup/bootstrap/model-load blocker, not yet a routing issue | Matches current pyworker evidence |
| Route/auth confusion | Endpoint identity, API key, and pyworker/template contract are easy to mismatch | Current route tests improved once endpoint name/API key were aligned |
| Residue after timeout/failure | Endpoint/workergroup cleanup is not safely implicit; explicit post-guard is required | Matches current cleanup observations |

## Vast: disputed or unresolved points

1. Which public template hashes are truly pyworker-ready versus generic base
   images with superficially similar names.
2. How `search_params` and `gpu_ram` constraints interact across CLI, SDK, and
   saved template JSON.
3. Which deployment path is canonical for reproducible serverless canaries:
   endpoint + workergroup, direct instance bootstrap, or pyworker-first flow.

## Practical operator playbook

### RunPod

1. Guard first. Treat non-zero `workersStandby` with `workersMin=0` as dirty
   until proven otherwise.
2. Compare in this order:
   - official public template
   - official CLI/REST create
   - custom image
3. If all three show the same `IN_QUEUE`/`throttled` shape, classify the next
   action as provider/account investigation, not image debugging.
4. For `EXITED`, collect endpoint id, worker id, job id, UTC window, and
   dashboard/system logs before changing local code.
5. Keep Pod-based routes as the bounded fallback while serverless remains red.

### Vast

1. Reject `:latest` and unverified template hashes.
2. Resolve the exact template hash before route experiments.
3. Treat `loading`/`model_loading` as startup evidence; do not over-attribute
   failures to route/auth until a pyworker-ready template is confirmed.
4. Run post-guard after every canary and record residue explicitly.
5. Prefer official pyworker bootstrap patterns over generic base-image
   templates.

## Current repo stance

This synthesis does not change the launch stance:

- `routing_by_module_enabled=false`
- provider adapter diffs remain out of scope
- RunPod serverless stays blocked until a real endpoint canary produces success
  evidence
- Vast serverless requires a verified pyworker-ready template and successful
  startup evidence before promotion
