# Generic System Integration Prompt v1

Canonical versioned prompt for downstream systems integrating with `gpu-job-control`.

- Prompt version: `generic-system-integration-prompt-v1`
- Caller contract: [Caller Contract](caller-contract.md)
- Operation catalog: [Operation Catalog](operation-catalog.md)
- Public API: [Public API](public-api.md)

```text
You are modifying the current software system so it can call an external GPU job product safely and deterministically.

Your task is to adapt this system so that GPU-requiring work is no longer executed through provider-specific SDKs, cloud-specific APIs, ad hoc shell commands, or implicit local GPU assumptions. Instead, this system must emit requests to a single external GPU job product using a strict machine-usable contract.

Follow these rules exactly.

1. Goal
- Introduce a deterministic integration layer that converts this system’s internal GPU-requiring work requests into the canonical caller request accepted by gpu-job-control.
- The integration must be provider-agnostic.
- The integration must not depend on any specific cloud GPU vendor, endpoint shape, SDK, queue system, or image runtime beyond the published contract of the GPU job product.
- The integration must preserve existing business logic while moving GPU execution behind the external product boundary.

2. Required output of your work
Modify this system so that it can:
- detect when a task requires external GPU execution,
- normalize that task into the canonical caller request contract,
- send that request to the GPU job product,
- poll or read the returned status/result deterministically,
- map the result back into this system’s native success/failure model,
- fail closed when the request cannot be expressed in the canonical contract.

3. Required request shape
The caller must emit one strict JSON object with these top-level fields:
- `contract_version`
- `operation`
- `input`
- `output_expectation`
- `limits`
- `idempotency`
- `caller`
- optional `trace_context`
- optional `preferences`

4. Required field rules
- `contract_version` must equal the published current version.
- `operation` must be a closed product-defined operation identifier, not free text.
- Use a named operation when one fits. Use `gpu.container.run` only for bounded GPU workloads that do not fit the named ASR, LLM, embedding, or OCR operations.
- `input.uri` must be explicit and machine-readable.
- `output_expectation.target_uri` must be explicit.
- `limits` must always be present and finite.
- `idempotency.key` must always be present.
- `caller.system`, `caller.operation`, `caller.request_id`, and `caller.version` are mandatory.
- `preferences.execution_lane_id` is optional and must be one of the operation's published `allowed_lanes`. Use it only when the caller is deliberately testing or operating a specific approved product lane; it never enables provider-module routing or bypasses product policy.
- `preferences.provider_module_id` is a legacy compatibility alias for `execution_lane_id`. Prefer `execution_lane_id` in new integrations.
- `gpu-job-control` is not a generic small-LLM wrapper. Use it for workloads that are unsuitable for local fixed resources.
- For production-quality `llm.generate`, set `preferences.quality_tier` to `production_quality`, `preferences.quality_requires_gpu` to `true`, `preferences.local_fixed_resource_policy` to `unsuitable`, and either `preferences.model_size_billion_parameters` to at least `70` or `preferences.model_size_class` to `at_least_70b`.
- 27B/32B-class LLMs may be used for `smoke`, `development`, or `degraded` flows, but must not be treated as production-quality external-GPU evidence.
- ASR is not the product boundary. Do not assume RunPod or Vast are ASR-only; every cloud provider lane is a generic GPU execution lane, with production use controlled by product policy and evidence.

5. Deterministic behavior requirements
- Same caller intent plus same inputs must produce the same canonical request.
- Do not use LLM judgment, fuzzy scoring, hidden heuristics, or free-form natural-language routing inside the integration.
- Do not silently rewrite user intent.
- Do not auto-upgrade operation type, model, gpu_profile, or limits without an explicit local rule.
- Do not introduce provider-specific branching unless the published product contract explicitly requires it.
- If the current system cannot map a request into the canonical contract without guessing, reject it with a structured error.

6. Forbidden behaviors
Do not:
- call cloud GPU providers directly,
- embed provider credentials into request payloads,
- generate ad hoc shell pipelines as the execution plan,
- use `provider_module_id` as a routing key; use `execution_lane_id` only as the documented product-lane request field,
- hide retries, hidden fallbacks, or hidden local execution,
- depend on conversational interpretation at runtime,
- emit partially specified requests,
- omit `idempotency.key`,
- omit explicit limits,
- omit explicit artifact expectations,
- bypass validation because the request is probably fine.

7. Required validation before send
Before the caller sends any request, it must validate:
- all required top-level fields exist,
- all required nested fields exist,
- `operation` is one of the system’s supported operations,
- the request is internally self-consistent,
- `input.uri` / `output_expectation.target_uri` are non-empty and structurally valid,
- limits are finite and positive,
- `output_expectation.required_files` is non-empty,
- no forbidden execution-job fields are present at top level.

If validation fails, return a structured local error and do not send the request.

8. Required response handling
Your integration must treat the GPU job product as an external deterministic execution service.
It must:
- record the request body before send,
- record the returned job identifier,
- poll or fetch status through the published product surface only,
- map terminal states deterministically,
- preserve external error payloads,
- verify artifact expectations after completion,
- return a structured failure if required artifacts are missing.

9. Required local architecture changes
Refactor the current system so that:
- business logic produces an internal operation request,
- one narrow adapter converts that request into the canonical GPU job caller request,
- one transport client sends the request,
- one result adapter maps product results back into this system,
- all provider-specific or cloud-specific code is removed from the caller path.

10. Acceptance condition
Your work is complete only when this system can accept its own native GPU-requiring tasks, convert them into the canonical caller request contract, send them to the external GPU job product deterministically, and consume the results without any provider-specific logic in the caller path.
```
