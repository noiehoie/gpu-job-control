# Public API

The public product surface is closed. Endpoints not listed here are `admin`,
`operator`, `internal`, or `legacy` surfaces and are not promised to external
callers.

Authentication is required unless the operator deliberately starts the server
with `GPU_JOB_ALLOW_UNAUTHENTICATED=1` on a trusted network. Supported auth
headers are:

- `Authorization: Bearer <token>`
- `X-GPU-Job-Token: <token>`

The server must be placed behind operator-controlled TLS or a trusted private
network boundary before external use.

## Public Endpoint Set

| Method | Endpoint | Request | Response | Error/status | Auth |
|---|---|---|---|---|---|
| `GET` | `/schemas/caller-request` | none | canonical caller JSON Schema | `401` unauthorized | required |
| `GET` | `/schemas/contracts` | none | public job contract schema | `401` unauthorized | required |
| `GET` | `/schemas/plan-quote` | none | plan quote schema | `401` unauthorized | required |
| `GET` | `/schemas/execution-record` | none | execution record schema | `401` unauthorized | required |
| `GET` | `/schemas/provider-workspace` | none | provider workspace schema | `401` unauthorized | required |
| `GET` | `/schemas/provider-module` | none | provider module audit schema | `401` unauthorized | required |
| `GET` | `/schemas/provider-contract-probe` | none | provider contract probe schema | `401` unauthorized | required |
| `GET` | `/catalog/operations` | none | closed operation catalog | `401` unauthorized | required |
| `GET` | `/catalog/caller-prompt` | none | canonical caller prompt metadata | `401` unauthorized | required |
| `POST` | `/validate` | caller request or legacy job | validation result | `400` malformed JSON/request | required |
| `POST` | `/route` | caller request or legacy job | deterministic route result | `400` malformed JSON/request | required |
| `POST` | `/plan` | caller request or legacy job | deterministic plan result | `400` malformed JSON/request | required |
| `POST` | `/submit` | caller request or legacy job | submission result | `202` accepted, `400`, `409`, `429` | required |
| `GET` | `/jobs/{job_id}` | path id | public job status | `404` unknown job | required |
| `GET` | `/verify/{job_id}` | path id | artifact verification result | `404` unknown artifact/job | required |

The machine-readable reference is
[`schemas/gpu-job-public-api.openapi.json`](../schemas/gpu-job-public-api.openapi.json).

## Public Request Shapes

`/validate`, `/route`, `/plan`, and `/submit` accept:

- the canonical caller-facing request from
  [`schemas/gpu-job-caller-request.schema.json`](../schemas/gpu-job-caller-request.schema.json);
- the legacy internal job shape during the `v1` transition only.

New external integrations must use the canonical caller request.

Provider selection, when an operator-approved integration must pin a fixed
local or cloud provider, is a transport-level option, not a caller request
field. Use `?provider=<provider>` on `/validate`, `/plan`, or `/submit`, or an
equivalent wrapper that keeps the canonical request under `job`. Do not place
`provider` inside the caller request JSON itself.

## Public Response Rules

- All public JSON responses include either an `ok` field or a resource payload
  whose endpoint section documents success.
- Failed validation and malformed JSON return a non-success status and a stable
  error string.
- Backpressure returns `429` when the public API can classify it before
  submission. `Retry-After` may be present and must be honored.
- Artifact verification failure is not a successful job outcome, even if the
  provider returned a nominal success status.

See [Error Codes](error-codes.md) for the caller-facing failure taxonomy.

## Compatibility

- current caller contract: `gpu-job-caller-request-v1`
- current public job contract: `gpu-job-contract-v1`
- current product boundary: `external GPUs for unsuitable workloads only`
- current lane boundary: `all cloud provider lanes are generic GPU candidates; ASR is validation evidence, not a lane restriction`
- current routing invariant: `routing_by_module_enabled=false`
- current OpenAPI document: `schemas/gpu-job-public-api.openapi.json`

## Deprecation policy

Legacy job-shaped public requests remain accepted during the `v1` caller-contract transition. New external integrations must target the caller-facing contract.

Breaking changes require a new contract version, changelog entry, migration
note, and release tag. New optional fields may be added without changing the
contract version when existing valid requests keep their behavior.

## Non-Public Surfaces

Queue mutation, workflow mutation, approvals, destructive preflights,
reconciliation, provider diagnostics, audit-chain inspection, and launch-gate
diagnostics are admin/operator/internal surfaces. They may exist in `api.py`,
but they are not public product promises unless they appear in the table above.
