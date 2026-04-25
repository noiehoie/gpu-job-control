# gpu-job-control Agent Rules

This file adds repository-local rules on top of the parent operating context.

## Council Gate Protocol

All material work must preserve an external CLI council trail. Material work
means literature research, design, code changes, provider behavior changes,
launch gates, production/canary promotion, and final audit.

The default required phases are:

- `research`
- `design`
- `code`
- `audit`

The default council members are:

- `gemini` through the real `gemini` CLI
- `composer2` through `agent --model composer-2`

Additional available members may be used with `agent --model <model>` when the
task needs broader review. Missing optional members must be recorded in the
audit log rather than silently ignored.

Use the repository wrapper for council calls:

```bash
./scripts/council-run --phase design --task-id <task-id> --member gemini -- <prompt>
./scripts/council-run --phase design --task-id <task-id> --member composer2 -- <prompt>
uv run python scripts/validate_council_audit.py --task-id <task-id>
```

The wrapper appends JSONL records under `docs/council-audit/`. The validator is
the source of truth for whether the required phases and members are present.

Fail closed:

- Do not claim council review without a JSONL audit artifact.
- Do not proceed from design to code without at least `research` and `design`
  council records, unless the change is explicitly marked non-material.
- Do not finalize material work without `audit` council records.
- If a required CLI is missing, not authenticated, times out, or exits non-zero,
  record that failure and treat the phase as blocked.
- `routing_by_module_enabled` must remain false unless a separate council-gated
  design, implementation, and audit explicitly approves module routing.

The protocol does not make model memory reliable. It makes missing council
evidence visible and rejectable by local checks and CI.

## Runtime Environment Boundary

`netcup` is the production/runtime command center for this project. Treat
Mac Studio as a development workstation and SSH hub only.

Run these classes of work on `netcup` through SSH or repository wrappers such
as `scripts/remote-docker` and `scripts/remote-docker-build-check`:

- infrastructure setup;
- dependency installation or provider CLI environment repair;
- Docker build/check/push work;
- provider runtime checks that depend on Linux server tooling;
- image registry authentication checks used by provider canaries.

Do not use Mac Studio to solve production-runtime dependency problems, build or
push images, or recreate Docker locally. If a task cannot run locally without
changing Mac Studio into a production-like runtime, stop and move that task to
`netcup`.
