# Public API

Public caller-facing endpoints:

- `GET /schemas/caller-request`
- `GET /catalog/operations`
- `POST /validate`
- `POST /route`
- `POST /plan`
- `POST /submit`
- `GET /jobs/{job_id}`
- `GET /verify/{job_id}`

Public read-only schema and catalog endpoints from the launch surface remain available.

## Request shapes

- caller-facing canonical request
- legacy internal job shape

`/validate`, `/route`, `/plan`, and `/submit` accept either shape. New callers should use the canonical caller request.

## Compatibility

- current caller contract: `gpu-job-caller-request-v1`
- current public job contract: `gpu-job-contract-v1`
- current routing invariant: `routing_by_module_enabled=false`

## Deprecation policy

Legacy job-shaped public requests remain accepted during the `v1` caller-contract transition. New external integrations must target the caller-facing contract.
