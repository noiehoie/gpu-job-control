# Data Lifecycle

This document fixes how public caller data is handled by `gpu-job-control`.

## Inputs

Caller requests may include operation inputs, object-storage URIs, local URIs,
HTTP(S) URIs, text prompts, limits, idempotency keys, caller identity, and trace
context. Caller payloads must not include provider API keys, registry tokens,
Hugging Face tokens, private endpoint credentials, private IP addresses, or
destructive-operation approvals.

Secrets are configured out of band through environment variables, provider CLIs,
system service configuration, or the operator secret store.

## Outputs and Artifacts

Workers write artifacts under the configured artifact store. A successful job
must provide enough files for deterministic verification, normally:

- `result.json`
- `metrics.json`
- `verify.json`
- `stdout.log`
- `stderr.log`

Artifact verification, not provider logs, is the public success boundary.

## Logs

Logs may contain job ids, caller request ids, trace ids, operation names,
provider names, timing, cost estimates, retry classes, and artifact summaries.
Logs must not contain provider tokens, registry credentials, private endpoint
secrets, or raw secret payloads.

## Retention

Retention is operator-controlled. The public contract is:

- caller request metadata is retained while the job record is retained;
- artifacts are retained while the artifact directory is retained;
- audit records are append-only operational evidence;
- support bundles may include redacted request, plan, execution, verification,
  and log excerpts.

Use `uv run gpu-job retention` to inspect local retention policy.

## Deletion

Deletion of local records or provider resources is an operator action. The
deletion boundary is part of the operator surface, not an implicit caller
feature. Any
destructive operation must be policy-gated and must not be triggered by a
caller-facing request unless that public endpoint explicitly documents the
approval boundary.

## Privacy and Redistribution

Operators are responsible for ensuring that caller inputs and generated outputs
are lawful to process on the selected provider. `gpu-job-control` records the
control-plane evidence needed to audit routing and verification. The privacy
boundary is documentation and audit evidence, not a grant of rights to
redistribute caller content, model output, provider logs, or third-party
datasets.
