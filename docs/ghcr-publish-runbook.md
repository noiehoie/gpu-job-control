# Worker Image Distribution Runbook

## Decision

GitHub Container Registry is an optional distribution target for demonstration worker images. It is not required for production operation.

Example public image:

```text
ghcr.io/<owner>/gpu-job-control-runpod-llm:<tag>
```

For repository `OWNER/gpu-job-control`, the workflow publishes:

```text
ghcr.io/OWNER/gpu-job-control-runpod-llm:canary
ghcr.io/OWNER/gpu-job-control-runpod-llm:<git-sha>
```

## Runtime Boundary

Do not put GitHub, GitHub Actions, or GHCR in the critical runtime path unless you have consciously accepted that dependency.

Healthy production posture:

- GitHub stores source, releases, issues, and CI evidence.
- A workload host runs `gpu-job-control`.
- Provider credentials stay on the workload host or in a private secret manager.
- Worker images are mirrored to a registry or provider template system controlled by the operator.
- Production job definitions use digest-pinned image references.

This keeps provider routing and spend guards operational even if GitHub, GHCR, or Actions are unavailable.

## Why Keep an Optional GHCR Build

- It gives reviewers a reproducible public image build example.
- It lets new users inspect the worker image contract before designing a registry layout.
- It can be mirrored into Docker Hub, a cloud registry, self-hosted registry, RunPod template, or Vast endpoint template.
- It keeps publication separate from runtime operation.

## GitHub Workflow

The publish workflow is:

```text
.github/workflows/publish-runpod-worker.yml
```

It performs:

1. Docker build of `docker/runpod-llm-worker.Dockerfile`.
2. Local deterministic canary container run.
3. Login to `ghcr.io`.
4. Push `:canary` and `:<git-sha>` tags.
5. Print the immutable digest.

## Preconditions

- This directory is the GitHub repository root.
- Repository name should be `gpu-job-control`.
- The default branch should be `main`.
- GitHub Actions must have package write permission.
- Anonymous provider pulls require a public source image. Production deployments should usually avoid that dependency by mirroring into an operator-controlled registry or provider template.

## Visibility

Keep the repository and package private until public-repository hygiene checks pass. A public package can be pulled without registry credentials; a private package requires configuring pull credentials in the provider template.

GitHub currently exposes package visibility as a package-settings operation. For personal-account packages, use the package page, open Package settings, and change visibility in the Danger Zone. Treat that as publication administration, not as runtime automation.

## Mirroring Pattern

After an image is built, mirror it into the registry used by your providers:

```text
source: ghcr.io/<owner>/gpu-job-control-runpod-llm@sha256:<digest>
target: registry.example.com/gpu-job-control/runpod-llm@sha256:<digest>
```

Then configure production jobs and provider templates to use the target digest. The public source can disappear temporarily without affecting already-mirrored runtime execution.

The CLI can plan the mirror operation without using local Docker:

```bash
uv run gpu-job image mirror \
  --source ghcr.io/<owner>/gpu-job-control-runpod-llm@sha256:<digest> \
  --target registry.example.com/gpu-job-control/runpod-llm@sha256:<digest> \
  --builder gpu-builder
```

Add `--execute` only after the remote builder has access to both registries.
