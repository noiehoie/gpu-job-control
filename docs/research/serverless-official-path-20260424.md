# Serverless Official Path Findings

Date: 2026-04-24

## Scope

- RunPod serverless sidecar probe without provider adapter changes
- Vast pyworker serverless sidecar probe without provider adapter changes
- netcup execution only

## RunPod

Current direct sidecar probe still creates GraphQL templates/endpoints by default.
That path remains blocked by the same provider-side behavior already observed in
earlier evidence: jobs can stay queued and GraphQL-created endpoints do not yet
prove parity with official Hub/Console deployment.

Additional 2026-04-24 netcup facts:

- The only retained official endpoint on the account is
  `vllm-623t63akmshaoi` / `gpu-job-disabled`.
- Its snapshot is:

```text
templateId=7nwbqj31ti
workersMax=0
workersMin=0
workersStandby=0
```

- Probing that endpoint with existing-endpoint mode submitted a real provider
  job id, but the job stayed `IN_QUEUE` until timeout/cancel and endpoint health
  never showed any worker:

```text
provider_job_id=dc9b92c7-2960-4dee-9767-8680c5aa21bf-e2
status=IN_QUEUE -> CANCELLED
workers.total=0
```

- The latest downloadable `runpodctl` release still reports `2.1.9-673143d`
  and does **not** expose `hub` or `serverless create --hub-id`, despite the
  current docs showing that surface.

The sidecar now has an **existing endpoint mode**:

```text
uv run python scripts/runpod-asr-serverless-contract-probe.py \
  --existing-endpoint-id <endpoint_id> \
  --existing-endpoint-name <optional endpoint name> \
  --existing-template-id <optional template_id> \
  --expected-provider-image <optional image override> \
  --serverless-api-key <optional endpoint token>
```

Behavior:

- skips `saveTemplate` / `saveEndpoint`
- skips delete/disable cleanup for shared official endpoints
- can resolve the retained official endpoint by id or exact name
- auto-reads template metadata from official REST `/templates/{templateId}` when
  `templateId` is known, so provider image evidence no longer depends on a
  manual `--expected-provider-image`
- still records endpoint health, submit/status/cancel artifacts, endpoint id,
  provider job id, and post-guard state
- poll samples now also carry endpoint health snapshots so queue/throttle drift
  is preserved in artifacts

This isolates **runtime contract validation** from **GraphQL creation-path drift**.

Consequence:

- existing-endpoint mode is still useful for evidence capture, but it does not
  solve the launch blocker when the only retained official endpoint is
  intentionally disabled.
- public CLI surface still does not reproduce the documented Hub deployment
  path, so RunPod Hub/Console parity remains unresolved.

## Vast

The latest netcup probe proved:

- endpoint create works
- workergroup create works
- route/auth works when using endpoint **name** plus endpoint **API key**
- a worker is actually created
- cleanup remains clean with no residue

Observed command result:

```text
uv run --with vastai-sdk python scripts/vast-pyworker-serverless-contract-probe.py \
  --template-hash 3109ee3e3500e00a0e4ed073a6446be7 \
  --request-timeout 70
```

Observed artifact facts:

- `endpoint_id=21064`
- `workergroup_id=27597`
- SDK worker list showed `id=35493386`, `status=loading`
- route attempts returned status lines with `loading workers: 1`
- cleanup deleted endpoint/workergroup successfully
- post-guard reported `billable_resources=[]`

Workergroup logs showed:

```text
Worker 35493386: created -> loading
Worker 35493386: loading -> model_loading
```

The critical finding is that template hash `3109ee3e3500e00a0e4ed073a6446be7`
is **not a pyworker-ready serverless template** for this probe. It resolves to
the public Vast base image:

```text
id=333156
name=NVIDIA CUDA
image=vastai/base-image
onstart=entrypoint.sh
```

That explains the current state:

- auth/route are no longer the primary blocker
- the worker never reaches ready because the selected template is only the base
  image and does not establish the needed pyworker/model-ready path for this
  canary

## Consequence

The next Vast step is not more route experimentation. It is to use an official
pyworker-compatible template or create one from official Vast launch patterns,
then rerun the same sidecar and require:

- endpoint/workergroup ids
- successful request
- positive GPU metrics
- clean post-guard

The sidecar should also support an **existing resource mode** for official-path
validation:

```text
uv run --with vastai-sdk python scripts/vast-pyworker-serverless-contract-probe.py \
  --existing-endpoint-id <endpoint_id> \
  --existing-workergroup-id <workergroup_id> \
  --existing-endpoint-name <optional endpoint name>
```

Behavior:

- skips endpoint/workergroup create/delete for shared official resources
- still records route attempts, worker request, endpoint/workergroup ids, logs,
  and post-guard state
- keeps `routing_by_module_enabled=false` and provider adapter diffs out of scope

Before a live rerun, use `vastai search templates 'hash_id == <hash>' --raw`
and reject hashes that resolve only to `vastai/base-image` without a pyworker
ready stack.

Additional 2026-04-24 netcup facts:

- Official creator `62897` does expose a public serverless-ready template named
  `vLLM (Serverless)`.
- One observed public hash was:

```text
template_id=322483
template_hash=67fec7936ea9b96c8fad38a0f82957bd
image=vastai/vllm
onstart=export HF_TOKEN="${HF_TOKEN:-1}"
entrypoint.sh &

bootstrap_script=https://raw.githubusercontent.com/vast-ai/pyworker/refs/heads/main/start_server.sh;
curl -L "$bootstrap_script" | bash;
```

- The first probe against that template still failed for two concrete reasons:
  1. netcup `.venv` did not have the Python `vastai` package, so the script's
     SDK path failed with `No module named 'vastai'`;
  2. the first manual payload omitted the required `{"input": ...}` nesting
     documented by Vast's vLLM serverless examples.

- After switching to `~/.local/bin/uv run --with vastai-sdk ...` and the
  documented nested payload shape, worker lifecycle advanced further:

```text
Worker 35504222: creating -> created
Worker 35504222: created -> model_loading
endpoint log: failed to find rdy worker for req: 1
```

This narrowed the next blocker to worker readiness, but a later rerun clarified
that the image/tag path is still unstable.

Later 2026-04-24 netcup rerun with a private template derived from the official
vLLM serverless layout:

```text
template_id=392815
template_hash=65582b441723f6deda807806ff3cae79
image=vastai/vllm
tag=latest
endpoint_id=21083
workergroup_id=27615
```

Observed probe result:

```text
sdk_request.error=Timed out after 182.6s waiting for worker to become ready
verify.checks.cleanup_ok=true
verify.checks.post_guard_clean=true
```

The decisive workergroup log lines were:

```text
Worker 35504760 encountered error: Error response from daemon: manifest for vastai/vllm:latest not found
Worker 35504603 encountered error: ... docker1.vast.ai ... 503 Service Unavailable
```

This means the current Vast serverless blocker is ordered as:

## Additional RunPod 2026-04-24 live fact

Another managed netcup retest used the existing RunPod ASR serverless sidecar
create path with:

```text
gpuIds=AMPERE_16,AMPERE_24,ADA_24
workersMax=1
workersMin=0
flashBootType=FLASHBOOT
```

The created endpoint came back as:

```text
endpoint_id=w8fxrfteibyzvj
template_id=h0qwadr1xj
workersStandby=1
pods=[{desiredStatus: EXITED}]
submit.status=IN_QUEUE
```

That means the creation request did **not** preserve the intended scale-to-zero
contract. The live endpoint was immediately outside policy because warm standby
capacity appeared before any successful request.

Observed cleanup result after explicit quiesce:

```text
saveEndpoint(id=w8fxrfteibyzvj, workersMax=0, workersMin=0)
gpu-job-admin guard --provider runpod -> ok=true
deleteTemplate(templateName="gpu-job-asr-diarization-20260424140810") -> data.deleteTemplate=null
```

This narrows the RunPod serverless launch blocker further:

1. current public create path on this account can surface `workersStandby=1`
   even when `workersMin=0` and `workersMax=1` were requested;
2. the endpoint can show an `EXITED` pod before any successful handler run;
3. job submission can remain `IN_QUEUE` despite that warm-capacity residue;
4. until an official Hub/Console-created endpoint is observed with
   `workersStandby=0`, RunPod serverless remains a provider-side blocker rather
   than a local adapter or artifact problem.

Another 2026-04-24 netcup rerun removed GraphQL create drift from the picture.
The sidecar was changed to use the official REST create surface
`rest.runpod.io/v1` and to expand RunPod GPU pool aliases into the concrete GPU
type names accepted by the REST schema.

Observed managed REST create result:

```text
template_id=wse9f4sivj
endpoint_id=v4p9qvdwfufua7
workersMin=0
workersMax=1
workersStandby=1
gpuTypeIds=[
  NVIDIA RTX A4000,
  NVIDIA RTX A4500,
  NVIDIA RTX 4000 Ada Generation,
  NVIDIA RTX 2000 Ada Generation,
  NVIDIA RTX A5000,
  NVIDIA L4,
  NVIDIA GeForce RTX 3090,
  NVIDIA GeForce RTX 4090
]
submit.job_id=044cc74f-741b-4e3a-a919-0fbca877c1fd-e2
submit.status=IN_QUEUE
guard.billable_resources=[serverless_endpoint_warm_capacity]
endpoint workers.exited=1
```

Cleanup was forced after observation:

```text
saveEndpoint(id=v4p9qvdwfufua7, workersMax=0, workersMin=0)
deleteEndpoint(v4p9qvdwfufua7)
gpu-job-admin guard --provider runpod -> ok=true
deleteTemplate(templateName="gpu-job-asr-diarization-20260424153334")
-> graphql error: Template is associated with AI API v4p9qvdwfufua7
```

This is the strongest RunPod serverless fact so far:

1. official REST create can succeed;
2. even on that official create surface, the endpoint still returns
   `workersStandby=1` immediately;
3. a real async submit can still stay `IN_QUEUE`;
4. the endpoint can simultaneously report an exited worker and warm-capacity
   residue;
5. the blocker is therefore not limited to GraphQL create drift.

Additional 2026-04-24 netcup reruns strengthened that conclusion again on the
official CLI and on the public official Faster Whisper template.

Observed official CLI create with the verified custom handler image:

```text
runpodctl template create --serverless --image ghcr.io/noiehoie/gpu-job-control-runpod-asr@sha256:e73ac...
runpodctl serverless create --template-id rtm2v05aww --gpu-id "NVIDIA GeForce RTX 4090" --workers-min 0 --workers-max 1

endpoint_id=cq3osv9k1bcjei
template_id=rtm2v05aww
worker_id=x7sk98ca5jkn9e
desiredStatus=EXITED
workersStandby=1
```

When the direct probe tried to reuse that endpoint in `existing-endpoint` mode,
it stopped before submit because pre-guard was already dirty:

```text
errors=["RunPod pre-guard is not clean"]
guard.billable_resources=[serverless_endpoint_warm_capacity]
```

That means the warm-standby residue is visible even on the official CLI create
path, not only the sidecar REST/GraphQL paths.

Observed official public Faster Whisper template facts:

```text
template_id=bem34sz6ol
image=runpod/ai-api-faster-whisper:0.4.1
repo=https://github.com/runpod-workers/worker-faster_whisper
```

Two official CLI create variants were exercised.

1. With explicit GPU override:

```text
runpodctl serverless create --template-id bem34sz6ol --gpu-id "NVIDIA GeForce RTX 4090" --workers-min 0 --workers-max 1
endpoint_id=2o5wg2qyatp6fe
worker_id=vr10stvsx8vdlg
desiredStatus=EXITED
imageName=runpod/ai-api-faster-whisper:0.4.1
```

2. With the template-default GPU selection, no `--gpu-id` override:

```text
runpodctl serverless create --template-id bem34sz6ol --workers-min 0 --workers-max 1
endpoint_id=fbvoxae2tdd79l
gpuIds=AMPERE_16,AMPERE_24
workersStandby=1
pods=[]
workers.total=0
```

We then polled that endpoint for 60 seconds:

```text
step=0..6
workersStandby=1
jobs.inQueue=0
jobs.inProgress=0
workers.total=0
workers.running=0
workers.exited=0
```

`workersStandby` never dropped back to `0` during that minute.

We also sent real `/run` jobs to official public-template endpoints.

Template:

```text
template_id=bem34sz6ol
image=runpod/ai-api-faster-whisper:0.4.1
```

Observed submit matrix:

```text
3090 override:
endpoint_id=i9aidzcxuhrosv
submit.job_id=224ef8ad-7181-4f60-add3-1ca82d4b45a8-e1
status=IN_QUEUE for 60s
workers not listed in endpoint get output

L4 override:
endpoint_id=ugwas8mwrm951z
submit.job_id=1110b9a5-92e0-4d89-9d4c-5f69cbda21e7-e1
status=IN_QUEUE for 60s
worker_id=hhew8avzbi2zvr
desiredStatus=EXITED

template-default GPU path:
endpoint_id=xjaq6p3icq5pog
submit.job_id=93922e82-c4f0-4206-92fe-4da3b8d2e8e9-e1
status=IN_QUEUE for 120s
worker_id=nm78lu8c7vp7cp
desiredStatus=EXITED
```

So even the official public Faster Whisper template did not produce a successful
serverless handler run on this account across the tested GPU routes. The live
failure shape varied slightly by route, but all variants stayed inside the same
blocker family:

- queue never dispatches to a completed run, or
- a worker is rented and then immediately exits, and
- the endpoint remains unusable for a bounded canary.

These additional runs mean the remaining blocker is no longer credibly
explained by GraphQL drift, sidecar REST drift, custom-image drift, or custom
template drift. The shared symptom is provider/account serverless warm-capacity
semantics on this account: newly created endpoints can surface
`workersStandby=1`, and some variants also rent a worker that immediately exits,
before any bounded canary can proceed.

1. image/tag resolution or registry pull failure
2. worker never becomes ready
3. route/request cannot succeed

So the next Vast step is not more route tuning. It is to pin a pullable image
reference from the official template path and rerun with the sidecar capturing
`image_pull_status` / `image_pull_error` explicitly.

One more 2026-04-24 rerun with the updated sidecar and a shorter timeout
changed the evidence again:

```text
request-timeout=60
endpoint_id=21100
workergroup_id=27626
instance_id=35505288
expected_image_ref=vastai/vllm:latest
sdk_request.error=Timed out after 61.0s waiting for worker to become ready
endpoint_workers[0].status=created
cleanup_ok=true
post_guard_clean=true
```

That run completed cleanly without manual residue and produced a synthetic route
observation instead of spending another full `route_attempts * poll_seconds`
after the SDK timeout.

So the currently confirmed Vast serverless ordering is:

1. expected image is still `vastai/vllm:latest`
2. image pull failure can happen on some offers, but is not deterministic
3. even when the image starts, the worker can remain `created/loading` and miss
   the ready window
4. route/request remains blocked downstream of worker readiness

Another 2026-04-24 refinement changed the artifact semantics, not the provider:

- Vast sidecar now emits `blocker_type=startup_timeout` when the SDK times out
  while `endpoint_workers[].status` stays in `created/loading/model_loading`
  and no image pull error is present.
- RunPod sidecar now emits `blocker_type=disabled_endpoint_queue` when an
  existing endpoint remains `IN_QUEUE` with `workersMin=0`, `workersMax=0`,
  `workersStandby=0`, and no active workers in endpoint health.

This keeps failure attribution aligned with the observed provider state instead
of collapsing both cases into a generic route/backpressure bucket.
