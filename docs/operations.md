# Operations

This document describes a generic deployment posture. It intentionally avoids environment-specific hostnames, IP addresses, endpoint IDs, and credential paths.

## Runtime State

`gpu-job-control` follows XDG paths by default:

```text
config:  $XDG_CONFIG_HOME/gpu-job-control
data:    $XDG_DATA_HOME/gpu-job-control
cache:   $XDG_CACHE_HOME/gpu-job-control
```

Provider credentials should be supplied by provider CLIs, environment variables, or a host-local secret manager. They must not be embedded in job JSON, examples, docs, or source code.

GitHub is not part of the required runtime path. Use it for source distribution, issues, releases, and CI evidence. The control plane, provider credentials, guard loop, queue, and production worker image references should remain operable from your own hosts and registries.

## First Five Minutes

The first run should require no cloud provider account and no paid GPU resource:

```bash
uv --version
uv run gpu-job doctor
uv run gpu-job validate examples/jobs/embedding.local.json
uv run gpu-job route examples/jobs/embedding.local.json
uv run gpu-job submit examples/jobs/embedding.local.json --provider local --execute
uv run gpu-job selftest
```

Expected properties:

- provider credentials are not required;
- the selected provider is local or deterministic test execution;
- no persistent external resource is created;
- artifact verification can run against the local artifact directory.

Create local configuration files only when you need to change policy, provider profiles, model capabilities, or budgets:

```bash
uv run gpu-job config init
uv run gpu-job config paths
```

The command copies safe defaults into the user configuration directory and will not overwrite existing files unless `--force` is used.

## Configuration Overrides

Public defaults live in `config/`. Deployments should keep environment-specific values outside the repository and point to them with:

```text
GPU_JOB_EXECUTION_POLICY=/path/to/execution-policy.json
GPU_JOB_PROFILES_CONFIG=/path/to/gpu-profiles.json
GPU_JOB_CAPABILITIES_CONFIG=/path/to/model-capabilities.json
```

The loader also checks `$XDG_CONFIG_HOME/gpu-job-control/` before falling back to repository defaults.

## Git-Based Operational Sync

Use GitHub or another private Git remote as the canonical source, and run production from a normal clone. Avoid maintaining a hand-copied deployment directory.

Recommended layout:

```text
canonical source:  private Git remote
operational clone: /home/admin/gpu-job-control
runtime config:    $XDG_CONFIG_HOME/gpu-job-control
runtime data:      $XDG_DATA_HOME/gpu-job-control
```

Environment-specific policy, provider budgets, approved persistent volumes, and tokens belong in XDG config or a host-local secret manager, not in the repository. For example:

```text
GPU_JOB_EXECUTION_POLICY=$XDG_CONFIG_HOME/gpu-job-control/execution-policy.json
```

Routine update:

```bash
cd /home/admin/gpu-job-control
git status --short
git pull --ff-only
uv sync --extra providers
gpu-job doctor
gpu-job selftest
gpu-job guard
sudo systemctl restart gpu-job-api.service gpu-job-worker.service
curl -sS -H "Authorization: Bearer $GPU_JOB_API_TOKEN" \
  http://127.0.0.1:8765/guard
```

Before replacing an existing non-Git deployment, stage the migration as a canary:

```bash
cd /home/admin
git clone <private-repo-url> gpu-job-control.gitcheck
cd gpu-job-control.gitcheck
uv sync --extra providers
gpu-job doctor
gpu-job selftest
gpu-job guard
```

Only switch the service directory after the canary clone passes guard with no active or queued work. Preserve the old deployment instead of deleting it:

```bash
sudo systemctl stop gpu-job-worker.service gpu-job-api.service
mv /home/admin/gpu-job-control /home/admin/gpu-job-control.manual-$(date +%Y%m%d-%H%M%S)
mv /home/admin/gpu-job-control.gitcheck /home/admin/gpu-job-control
rm -rf /home/admin/gpu-job-control/.venv
cd /home/admin/gpu-job-control
uv sync --extra providers
sudo systemctl start gpu-job-api.service gpu-job-worker.service
gpu-job doctor
gpu-job selftest
gpu-job guard
```

If the clone was moved after `uv sync`, rebuild `.venv`. Console-script shebangs inside `.venv/bin` contain the original absolute path and can break after a directory rename.

## API

The built-in API is intended as a localhost or private-network sidecar. It uses the Python standard library HTTP server to keep the control-plane core dependency-free; put it behind a production reverse proxy if exposing it beyond a trusted host boundary.

Run the API on a private interface or localhost, and require a token:

```bash
GPU_JOB_API_TOKEN=replace-me uv run gpu-job serve \
  --host 127.0.0.1 \
  --port 8765 \
  --require-token
```

The caller may authenticate with either:

```text
Authorization: Bearer <token>
X-GPU-Job-Token: <token>
```

If `GPU_JOB_API_TOKEN` is missing, the API generates an ephemeral token at startup and enforces it. This prevents accidental unauthenticated localhost APIs. For an explicitly unauthenticated disposable development process only, set:

```bash
GPU_JOB_ALLOW_UNAUTHENTICATED=1 uv run gpu-job serve --host 127.0.0.1 --port 8765
```

CORS is disabled by default. If a browser UI needs access, enumerate trusted origins exactly:

```bash
GPU_JOB_CORS_ORIGINS=http://127.0.0.1:3000,http://localhost:3000
```

Large JSON requests are rejected before read. The default cap is 10 MiB and can be changed with `GPU_JOB_MAX_JSON_BODY_BYTES`.

## Provider SDKs

The core package has no mandatory cloud SDK dependency. Provider-specific SDKs are optional:

```bash
uv sync --extra providers
```

RunPod SDK subprocesses use the current Python interpreter by default. Set `RUNPOD_PYTHON=/path/to/python` only when the provider SDK is installed in a separate interpreter.

## Guard Loop

Run `gpu-job guard` before and after any provider operation. Production deployments should also run it periodically from a scheduler.

The guard should fail closed when it sees:

- active paid resources not associated with an approved job;
- warm serverless workers outside policy;
- unknown persistent storage;
- local memory, swap, load, or disk pressure;
- provider queues that exceed job deadlines;
- stale running jobs.

## Docker Builds

Do not require Docker on a developer workstation. Prefer CI or a dedicated Linux builder.

For local development on a remote builder, synchronize source explicitly and run Docker only there. Keep registry credentials scoped and short-lived where possible. The helper scripts in `scripts/remote-docker*` are thin SSH wrappers around a caller-supplied `GPU_JOB_DOCKER_BUILDER`.

## Worker Image Distribution

Worker images may be built by GitHub Actions, a remote Linux builder, or a provider-native build process. Production routing should reference an image location that you control operationally:

- a provider-native serverless template;
- a self-hosted or cloud container registry;
- a mirror of a public demonstration image;
- an immutable digest reference already accepted by the provider.

Do not make production job execution depend on live access to this GitHub repository or to GitHub Actions. GHCR can be useful for examples, but deployments should mirror images and pin digests in their own configuration.

Plan a mirror operation without touching local Docker:

```bash
uv run gpu-job image mirror \
  --source ghcr.io/example/gpu-job-control-runpod-llm@sha256:<digest> \
  --target registry.example.com/gpu-job-control/runpod-llm@sha256:<digest> \
  --builder gpu-builder
```

Add `--execute` only on a builder host that has registry credentials and Docker Buildx configured.

## Public Repository Hygiene

Before making a deployment public:

1. Scan tracked files and history for API keys and provider tokens.
2. Remove environment-specific hostnames, IPs, endpoint IDs, volume IDs, and paths.
3. Replace operational incident reports with generic runbooks.
4. Ensure examples use placeholder IDs and local paths.
5. Rebuild history before publication if sensitive data was ever committed.
