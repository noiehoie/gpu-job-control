# Error Codes

This is the caller-facing error taxonomy for the public API.

Public responses use HTTP status codes for transport outcome and an `error` or
`class` string for programmatic handling. Callers must branch on the structured
class when present, not on human-readable text.

| HTTP status | Class | Retry | Caller action |
|---|---|---:|---|
| 400 | `validation_error` | no | Fix the request to match `gpu-job-caller-request-v1`. |
| 400 | `unsupported_job_type` | no | Use an operation from the operation catalog. |
| 400 | `context_overflow` | no | Reduce input size or operation parameters. |
| 401 | `unauthorized` | no | Send `Authorization: Bearer <token>` or `X-GPU-Job-Token`. |
| 403 | `approval_required` | no | Operator approval is required before the operation can run. |
| 409 | `policy_block` | no | The request conflicts with local policy; change request or policy. |
| 409 | `quota_block` | no | Reduce cost/runtime/output limits or wait for quota reset. |
| 409 | `cost_block` | no | Lower requested spend or obtain explicit operator approval. |
| 409 | `secret_block` | no | Configure secrets out of band; never put secrets in payloads. |
| 409 | `artifact_integrity_failed` | no | Treat output as invalid and inspect the support bundle. |
| 409 | `artifact_contract_failure` | no | Worker output did not satisfy the artifact contract. |
| 429 | `backpressure` | yes | Retry after `Retry-After` when present, otherwise exponential backoff. |
| 429 | `provider_backpressure` | yes | Retry later or choose a lower-cost/lower-capacity operation. |
| 429 | `provider_rate_limit` | yes | Retry after the provider or API rate-limit window. |
| 500 | `unknown` | no | Do not blindly retry; attach support bundle. |
| 502 | `provider_transient` | yes | Retry with backoff. |
| 503 | `endpoint_unreachable` | yes | Retry after provider recovery or fail over by policy. |
| 503 | `cold_start_timeout` | yes | Retry later; first request may have warmed the provider cache. |
| 503 | `startup_timeout` | yes | Retry after provider capacity stabilizes. |
| 504 | `provider_timeout` | yes | Retry only if idempotency key is stable. |

Provider-native classifications are defined in
[`src/gpu_job/error_class.py`](../src/gpu_job/error_class.py). Public docs must
not expose provider secrets, private endpoint identifiers, or private network
addresses while explaining failures.

## Provider Responsibility Boundary

`gpu-job-control` guarantees deterministic validation, planning, policy checks,
submission shape, status reporting, and artifact verification. It does not
guarantee Modal, RunPod, Vast.ai, registry, network, or model-provider uptime.

Provider failures must be surfaced as provider classes such as
`provider_backpressure`, `provider_transient`, `provider_timeout`,
`endpoint_unreachable`, `image_pull_failed`, or `image_not_found`. Caller errors
must be surfaced as validation, quota, cost, policy, or artifact classes.

## Retry Rules

- Retry only retryable classes.
- Preserve `idempotency.key` across retries.
- Do not retry `validation_error`, `policy_block`, `quota_block`, `cost_block`,
  `secret_block`, `artifact_contract_failure`, or `artifact_integrity_failed`
  without changing the request or operator state.
- Respect `Retry-After` when the API returns it.
