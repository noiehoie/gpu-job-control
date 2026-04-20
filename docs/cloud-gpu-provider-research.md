# Cloud GPU Provider Research Gate

Date: 2026-04-21 JST

This document is the research gate for provider-specific implementation in
gpu-job-control. It exists to prevent provider behavior from being guessed in
code. Provider-specific workspace, lifecycle, canary, routing, cleanup, or
production dispatch work must cite documented provider facts here or explicitly
name an assumption that needs live canary proof.

## Council Scope

Engines consulted:

- Composer 2: execution strategy and provider research brief.
- Kimi K2.5: provider research brief and codebase cross-check.
- Gemini: strategy audit; approved the docs gate before provider-specific work.
- Codex: source reconciliation, official documentation reading, and final
  implementation gate owner.

Standing rule:

- Official documentation defines the provider contract.
- Community reports define risk signals and canary questions only.
- Local live canary evidence can promote an assumption to a gpu-job-control
  contract.

## Sources

Official documentation reviewed:

- RunPod Serverless overview:
  https://docs.runpod.io/serverless/overview
- RunPod endpoint settings:
  https://docs.runpod.io/serverless/endpoints/endpoint-configurations
- RunPod network volumes:
  https://docs.runpod.io/storage/network-volumes
- Modal images:
  https://modal.com/docs/guide/images
- Modal volumes:
  https://modal.com/docs/guide/volumes
- Modal timeouts:
  https://modal.com/docs/guide/timeouts
- Modal failures and retries:
  https://modal.com/docs/guide/retries
- Modal secrets:
  https://modal.com/docs/guide/secrets
- Vast Docker execution environment:
  https://docs.vast.ai/documentation/instances/docker-environment

Community / operational risk sources reviewed as signals:

- RunPod cold start and network volume discussions in RunPod and Stable
  Diffusion communities.
- Reports of network-volume datacenter availability constraints, model cache
  misses, image setup delays, endpoint worker readiness gaps, and resource
  residue after failed runs.

Community reports must not directly change production behavior. They must become
one of:

- a field in `PlanQuote` / `ProviderWorkspaceRegistry` / `ExecutionRecord`;
- a `requires_action`;
- a blocked state;
- a canary assertion.

## RunPod Brief

### Resource Model

RunPod Serverless uses endpoints as the request target and workers as container
instances that execute custom Docker images. Workers are started when requests
arrive and stopped when idle. Queue-based endpoints queue requests and expose
status polling; load-balancing endpoints route traffic directly to available
workers and do not provide a backlog queue model.

Endpoint settings include active workers, max workers, GPUs per worker, idle
timeout, execution timeout, job TTL, FlashBoot, GPU type priority, data center
restriction, CUDA version, and network volumes.

### Workspace Model

RunPod network volumes are persistent resources independent of compute. For
Serverless, a network volume mounts at `/runpod-volume`. For Pods, a network
volume replaces the default volume disk and is typically mounted at
`/workspace`. Pod network volumes must be attached at deployment time and cannot
be attached or detached later without deleting the Pod.

Network volumes are useful for model and artifact persistence, but a single
volume constrains worker placement to the volume datacenter. Multiple volumes
can improve availability, but data does not sync automatically between them.

### Timing Model

If no Serverless workers are active, RunPod starts a worker, queues the request,
runs the handler, and returns results via status polling or sync execution.
Cold start includes container start, model loading into GPU memory, and runtime
initialization. Larger models increase cold start and response time. Cached
models, FlashBoot, and active worker counts can reduce cold starts.

Endpoint defaults include active workers `0`, max workers `3`, idle timeout
`5s`, execution timeout `600s`, job TTL `24h`, and FlashBoot enabled. Active
workers reduce or eliminate cold starts but are billable while idle.

### Cost Model

Serverless is pay-as-you-go for compute time when processing requests, but
active workers create idle cost. Network volumes have independent monthly
storage cost and can be terminated if the account lacks funds. Volume cost and
active-worker idle cost are therefore separate from per-request GPU runtime.

### Status And Failure Model

The broker must treat queue time, job TTL, execution timeout, result retention,
worker startup, and worker readiness as separate states. A job can lose useful
execution time while queued because TTL starts at submission, not at execution.
Network volume datacenter binding can reduce GPU availability and failover
options. Concurrent writes to the same volume can cause corruption unless the
application prevents it.

### Canary Requirements

RunPod ASR diarization cannot be considered production until gpu-job-control
can prove:

- endpoint or Pod identity;
- image digest / image contract;
- volume mount path and cache readiness;
- HF token or required secret availability;
- worker startup and model load timing;
- queue and execution timing;
- artifact contract;
- cleanup result;
- post-submit resource residue;
- cost estimate versus observed billable resource shape.

## Modal Brief

### Resource Model

Modal is function-oriented rather than SSH-instance-oriented. GPU resources,
secrets, volumes, image definitions, timeouts, retries, and lifecycle hooks are
part of the Modal app/function definition.

### Workspace Model

Modal images are defined explicitly and rebuilt when image inputs change.
Runtime package installation in production paths is not an acceptable substitute
for an image contract. Modal secrets are injected into functions as environment
variables. Modal volumes are persistent distributed filesystems mounted into
functions and can be used for model weights or cache.

Volumes require explicit commit/reload semantics for consistency. Background
commits exist, but changes made by one container are not visible in another
container until commit/reload rules are satisfied. Volume performance and
latency depend on file count and filesystem behavior.

The current gpu-job-control workspace contract uses `/mnt` as the Modal volume
mount default in registry metadata. Individual Modal functions may still define
their concrete mount paths explicitly; any function-specific override must be
recorded in the workspace contract before execution.

### Timing Model

Modal functions have execution timeout and startup timeout as separate concerns.
The default execution timeout is documented as 300 seconds, and user-specified
timeouts may range up to 24 hours. Startup timeout covers container startup,
including large model loading or imports. Retries reset execution timeout per
attempt.

### Cost Model

The broker must quote Modal as function execution with provider-defined GPU
resources, not as an instance lease. Volume storage and app/function lifecycle
must remain separate from per-call execution time in the quote model.

### Status And Failure Model

Function timeout, startup timeout, retries, container crash rescheduling,
crash-loop backoff, and deployed-app behavior must be modeled separately.
Container crashes can be rescheduled by Modal; gpu-job-control must avoid
mistaking provider retry behavior for a caller-approved retry unless the plan
explicitly records that policy.

### Canary Requirements

Modal canaries must prove:

- function/app identity;
- image contract;
- volume mount and cache availability where used;
- secret availability;
- startup versus execution timing;
- artifact contract;
- timeout/retry behavior recorded in `ExecutionRecord`;
- no raw secrets or raw provider stack traces leak to public records.

## Vast Brief

### Resource Model

Vast instances run as Linux Docker containers. Vast automatically configures
resource allocation based on GPU allocation. GPUs are exclusive while an
instance is running; stopped instances have no GPU reservation. Disk allocation
is static at creation time.

### Workspace Model

Vast supports Entrypoint, SSH, and Jupyter launch modes. SSH and Jupyter launch
modes inject setup scripts and replace the original image entrypoint. Custom
images that rely on entrypoint scripts must account for this or use Entrypoint
mode. Environment variables may need explicit export to `/etc/environment` for
SSH/tmux/Jupyter visibility.

Vast provides per-instance variables such as `CONTAINER_ID`, `GPU_COUNT`, and
Vast port variables. Public IP may change; the current address can be queried
with the per-instance API key. Ports are mapped through shared public IPs and
random external ports, with a documented limit on total open ports.

### Timing Model

Vast timing must model offer selection, instance reservation, image
materialization, SSH or entrypoint readiness, payload transfer, worker startup,
GPU execution, artifact collection, and cleanup separately. Disk sizing cannot
be fixed after creation, so the plan must quote disk requirements before
allocation.

### Cost Model

Vast is market/instance oriented. The broker must quote the selected offer and
must record pre-allocation cost assumptions. Because instances are billable
resources, cleanup evidence is part of cost control, not an optional diagnostic.

### Status And Failure Model

Failure modes include offer disappearance, image pull/materialization failure,
entrypoint replacement surprises, SSH readiness failure, dynamic IP/port
changes, static disk under-sizing, RAM baseline/OOM risk, artifact collection
failure, cleanup failure, and orphan instances.

### Canary Requirements

Vast canaries must prove:

- selected offer identity and cost;
- instance identity and labels;
- launch mode;
- image contract;
- environment and port readiness;
- workspace path;
- startup phase timings;
- artifact contract;
- cleanup transition;
- post-cleanup provider read showing no active matching instance.

## Cross-Provider Contract Implications

ProviderWorkspaceRegistry must not be a live discovery client. It should be a
pure deterministic registry of declared workspace contracts and latest approved
canary evidence. Live provider API calls belong in admin canaries, probes, or
execution adapters.

PlanQuote must include:

- selected provider;
- selected GPU/profile;
- selected image/workspace contract;
- cost estimate and cost basis;
- timing estimate and timing basis;
- provider readiness level;
- required actions;
- assumptions that require canary proof;
- explanation of why lower-ranked providers were rejected.

ExecutionRecord must include:

- the PlanQuote used for execution;
- provider resource identity;
- workspace contract snapshot;
- phase timings;
- observed cost when available;
- artifact verification result;
- cleanup result;
- provider residue check where relevant.

The broker must reject or return `requires_action` when:

- required image contract is missing or unverified;
- required secret/token is missing;
- provider workspace has not passed the required canary;
- cold-start or TTL assumptions cannot fit within caller limits;
- cost cannot be bounded;
- cleanup cannot be verified for a billable resource.

## Open Research Items

Before broadening production routes, the council must still verify:

- Modal exact billing dimensions used by the current account and GPU types.
- Modal image rebuild and deployment behavior for the current app layout.
- RunPod exact status API fields used by endpoint and Pod routes in this repo.
- RunPod Serverless result retention and artifact handling for ASR outputs
  longer than the default retention window.
- Vast exact CLI/API state names used in current account responses.
- Vast billing behavior during failed image materialization and SSH-readiness
  failures.
