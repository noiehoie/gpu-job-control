# Finished Product Gate

This document is the release-blocking acceptance gate for calling
`gpu-job-control` a finished external product.

The gate is a checklist of acceptance conditions, not a task list. A condition
passes only when it has:

- a tracked artifact path;
- a deterministic verification command;
- an expected output or assertion;
- an owner surface: `public`, `admin`, `operator`, or `internal`;
- a release-blocker decision.

## Current Verdict

`gpu-job-control` is not a finished external product until every `blocker=yes`
condition below passes on the release commit.

## Required Release Command Set

The final release candidate must run these commands from a clean checkout of
the release commit:

```bash
uv run pytest -q
uv run python -m unittest discover -s tests -q
uv run --with ruff ruff check src tests
uv run --with ruff ruff format --check src tests
uv run gpu-job selftest
uv run gpu-job validate examples/jobs/asr.example.json
uv run python -m gpu_job.cli readiness --phase-report
git diff --check
git status --short --branch
```

The readiness output must include:

```text
phase_0_current_diff_fixed=true
phase_1_contract_core_launch_candidate=true
phase_2_runtime_config_cross_check=true
phase_3_modal_canary=true
phase_4_runpod_bounded_canary=true
phase_5_vast_reserve_canary=true
provider_adapter_diff=[]
routing_by_module_enabled=false
stop_conditions=[]
```

## Gate Criteria

| ID | Acceptance condition | Artifact path | Verification command | Expected output | Surface | Blocker |
|---|---|---|---|---|---|---|
| FPG-01 | Product declaration no longer describes the repository as an early implementation. | `README.md` | `rg -n "early control-plane implementation|Alpha" README.md pyproject.toml` | No early-status contradiction remains for the release claim. | public | yes |
| FPG-02 | Package maturity, README status, changelog, version, and release tag are consistent. | `README.md`, `pyproject.toml`, `src/gpu_job/__init__.py`, `CHANGELOG.md` | `rg -n "version =|__version__|Development Status|^## \\[" pyproject.toml src/gpu_job/__init__.py CHANGELOG.md` | One release version is used consistently. | public | yes |
| FPG-03 | Public, admin, operator, internal, and legacy surfaces are explicitly separated. | `README.md`, `docs/public-api.md` | `rg -n "public|admin|operator|internal|legacy" README.md docs/public-api.md` | Each surface has a documented boundary. | public | yes |
| FPG-04 | Public endpoint closed set has one canonical source. | `docs/public-api.md` | `rg -n "^##|^###|Endpoint|Method|Auth" docs/public-api.md` | The supported caller-facing endpoints are listed as a closed set. | public | yes |
| FPG-05 | Non-public API paths are identified as admin, internal, or legacy. | `docs/public-api.md` | `rg -n "admin|internal|legacy|not public" docs/public-api.md` | No undocumented path is presented as a public promise. | public | yes |
| FPG-06 | Each public endpoint has fixed request, response, error, status-code, and auth rules. | `docs/public-api.md` | `rg -n "Request|Response|Error|Status|Auth" docs/public-api.md` | Every public endpoint section contains those fields. | public | yes |
| FPG-07 | Caller contract lifecycle is documented. | `docs/caller-contract.md` | `rg -n "Compatibility|contract_version|deprecated|fail-closed|forbidden" docs/caller-contract.md` | Version, compatibility, deprecation, forbidden fields, and fail-closed rules are present. | public | yes |
| FPG-08 | Canonical caller request schema exists and rejects unsupported top-level fields. | `schemas/gpu-job-caller-request.schema.json` | `rg -n "additionalProperties|contract_version|required" schemas/gpu-job-caller-request.schema.json` | Schema has fixed required fields and top-level `additionalProperties: false`. | public | yes |
| FPG-09 | Machine-readable public API specification exists. | `schemas/`, `docs/public-api.md` | `rg --files schemas docs | rg "openapi|schema|public-api|caller-request"` | OpenAPI or an equivalent schema bundle is tracked and linked. | public | yes |
| FPG-10 | Operation catalog is closed and consistent with caller schema. | `config/operation-catalog.json`, `docs/operation-catalog.md` | `uv run gpu-job caller catalog` | Catalog loads and exposes only supported operations. | public | yes |
| FPG-11 | Free-form public `job_type` is not part of the caller contract. | `docs/caller-contract.md`, `config/operation-catalog.json` | `rg -n "job_type|operation" docs/caller-contract.md config/operation-catalog.json` | Public docs route callers through `operation`, not arbitrary `job_type`. | public | yes |
| FPG-12 | Generic integration prompt is versioned and canonical. | `docs/generic-system-integration-prompt-v1.md`, `docs/generic-system-integration-prompt.md` | `rg -n "version|contract|operation|must|must not" docs/generic-system-integration-prompt*.md` | Prompt tells external systems how to integrate without internal repo knowledge. | public | yes |
| FPG-13 | Deterministic request compiler has regression coverage. | `src/gpu_job/caller_contract.py`, `tests/` | `uv run pytest -q tests -k "caller or public"` | Same input produces same validation, route, and plan. | public | yes |
| FPG-14 | Public API transport is thin and delegates caller logic to deterministic code. | `src/gpu_job/api.py`, `src/gpu_job/caller_contract.py` | `rg -n "caller|validate|plan|submit|verify" src/gpu_job/api.py src/gpu_job/caller_contract.py` | Transport and orchestration boundaries are visible. | public | yes |
| FPG-15 | Authentication and trust boundary are fixed. | `docs/public-api.md`, `docs/operations.md`, `SECURITY.md` | `rg -n "Bearer|X-GPU-Job-Token|GPU_JOB_ALLOW_UNAUTHENTICATED|TLS|trusted|auth" docs SECURITY.md` | Auth headers, unauthenticated mode, proxy/TLS assumptions, and auth failures are documented. | public | yes |
| FPG-16 | Approval and destructive-operation boundaries are documented. | `docs/operations.md`, `SECURITY.md` | `rg -n "destructive|approval|delete|terminate|destroy" docs/operations.md SECURITY.md` | Destructive surfaces are not exposed without explicit policy. | operator | yes |
| FPG-17 | Onboarding has one minimal success path. | `docs/client-integration-guide.md` | `rg -n "validate|submit|jobs|verify|curl|example" docs/client-integration-guide.md` | A caller can follow one path from validate through verify. | public | yes |
| FPG-18 | Onboarding has minimal failure examples. | `docs/client-integration-guide.md`, `docs/public-api.md` | `rg -n "validation|auth|429|backpressure|artifact|failure" docs/client-integration-guide.md docs/public-api.md` | Validation reject, auth fail, backpressure, and artifact failure examples exist. | public | yes |
| FPG-19 | Distribution path is real and pinned. | `README.md`, `CHANGELOG.md`, release artifacts | `rg -n "install|version|release|tag|artifact|pinned" README.md CHANGELOG.md docs` | External users can install the same release without guessing. | public | yes |
| FPG-20 | Reference SDK is distribution-ready. | `src/gpu_job/public_client.py`, `pyproject.toml` | `rg -n "public_client|retry|timeout|auth|export" src/gpu_job pyproject.toml` | SDK exposes auth, timeout, retry, and error mapping. | public | yes |
| FPG-21 | Public CLI is caller-friendly. | `src/gpu_job/cli_public.py`, `docs/client-integration-guide.md` | `uv run gpu-job --help` | Caller commands have discoverable summaries and useful errors. | public | no |
| FPG-22 | Quickstarts exist for ASR, LLM, and OCR or explicitly declare unsupported operations. | `docs/client-integration-guide.md`, `examples/caller-requests/` | `rg -n "ASR|LLM|OCR|unsupported" docs examples` | Each listed scenario has a supported example or explicit unsupported status. | public | yes |
| FPG-23 | Non-Python integration has raw HTTP and shell examples. | `docs/client-integration-guide.md` | `rg -n "curl|HTTP|shell|non-Python|language-agnostic" docs/client-integration-guide.md` | A non-Python caller can integrate from docs alone. | public | yes |
| FPG-24 | Support scope is explicit. | `docs/operations.md`, `SECURITY.md`, `README.md` | `rg -n "support|scope|contact|vulnerability|disclosure|security" docs README.md SECURITY.md` | Users know what is supported, where to report issues, and what is out of scope. | public | yes |
| FPG-25 | Error taxonomy is public and programmatic. | `docs/error-codes.md`, `src/gpu_job/error_class.py` | `rg -n "HTTP|error|retry|fallback|provider|caller" docs/error-codes.md src/gpu_job/error_class.py` | Error class, HTTP status, retry/fallback, and caller action are mapped. | public | yes |
| FPG-26 | Data lifecycle is public. | `docs/data-lifecycle.md` | `rg -n "input|output|artifact|log|retention|deletion|privacy" docs/data-lifecycle.md` | Retention, deletion, logs, artifacts, and privacy handling are documented. | public | yes |
| FPG-27 | Product invariants are public and release-blocking. | `docs/product-invariants.md` | `rg -n "routing_by_module_enabled=false|provider_adapter_diff=\\[\\]|phase_0|phase_5|stop_conditions=\\[\\]" docs/product-invariants.md` | Immutable launch and product conditions are listed. | public | yes |
| FPG-28 | Secret hygiene is enforced by CI or deterministic local checks. | `SECURITY.md`, `tests/`, `.github/workflows/` | `rg -n "secret|token|endpoint|private IP|hygiene" SECURITY.md tests .github` | Examples and docs cannot leak provider tokens, private IPs, or private endpoint IDs. | public | yes |
| FPG-29 | Schema and Python implementation cannot drift silently. | `schemas/gpu-job-caller-request.schema.json`, `src/gpu_job/caller_contract.py`, `tests/` | `uv run pytest -q tests -k "schema and caller"` | Schema and implementation reject/accept the same canonical cases. | public | yes |
| FPG-30 | Public API golden responses are tested. | `tests/`, `docs/public-api.md` | `uv run pytest -q tests -k "public and golden"` | Public response shape changes fail CI. | public | yes |
| FPG-31 | Cost controls, quota, rate limits, and abuse/backpressure behavior are documented and tested. | `docs/public-api.md`, `docs/routing-policy.md`, `src/gpu_job/quota.py`, `tests/` | `rg -n "quota|rate|cost|budget|backpressure|abuse" docs src/gpu_job tests` | Caller-facing limits and failure behavior are clear. | public | yes |
| FPG-32 | Provider failure transparency and responsibility boundary are documented. | `docs/provider-promotion.md`, `docs/public-api.md`, `docs/error-codes.md` | `rg -n "provider|SLA|failure|timeout|inventory|capacity|responsibility|fallback" docs` | Provider outages are distinguishable from caller errors and local policy failures. | public | yes |
| FPG-33 | Legal, license, privacy, and redistribution minimums are documented. | `LICENSE`, `README.md`, `docs/data-lifecycle.md`, `SECURITY.md` | `rg -n "Apache|License|privacy|redistribution|NOTICE|terms|liability" LICENSE README.md docs SECURITY.md` | External users have the minimum legal and data-handling information. | public | yes |
| FPG-34 | Repeat launch evidence is attached to the release. | `docs/launch-decision.md`, `docs/launch-phase0-5-gate.md`, `docs/launch-logs/` | `rg -n "R3|repeat|phase_5_vast_reserve_canary=true|provider_adapter_diff=\\[\\]|routing_by_module_enabled=false|stop_conditions=\\[\\]" docs/launch-decision.md docs/launch-phase0-5-gate.md` | At least two launch/canary cycles are recorded and linked. | operator | yes |
| FPG-35 | Final release freeze is auditable. | `CHANGELOG.md`, release tag, `docs/council-audit/` | `git tag --points-at HEAD && ./scripts/validate-council-audit --task-id <release-task-id> --require` | Release commit has a tag and passing council audit validation. | operator | yes |

## Release Interpretation

The project may be described as a finished external product only when every
`blocker=yes` row passes on the release commit. `blocker=no` rows are still
required for a polished release, but they do not prevent a constrained external
introduction when the documented public surface remains stable.

If any condition cannot be verified because an artifact path does not exist, the
condition fails. If any command is unavailable in a clean checkout, the condition
fails.

## Non-Negotiable Invariants

These invariants are part of the finished-product definition and are not product
features to relax during release pressure:

- `provider_adapter_diff=[]`
- `routing_by_module_enabled=false`
- `stop_conditions=[]`
- public caller requests are deterministic and fail closed;
- secrets are never accepted in caller payloads;
- provider module metadata remains audit evidence, not production routing logic;
- destructive cleanup remains explicitly policy-gated.
