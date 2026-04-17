# GHCR Publish Runbook

## Decision

Use GitHub Container Registry as the provider-neutral registry for gpu-job-control worker images.

Primary image:

```text
ghcr.io/<owner>/gpu-job-control-runpod-llm:<tag>
```

For repository `OWNER/gpu-job-control`, the workflow publishes:

```text
ghcr.io/OWNER/gpu-job-control-runpod-llm:canary
ghcr.io/OWNER/gpu-job-control-runpod-llm:<git-sha>
```

## Why GHCR

- The image remains provider-neutral and can be reused by RunPod, Vast, and any compatible Docker host.
- GitHub Actions can publish using `GITHUB_TOKEN` with `packages: write`.
- No long-lived registry token needs to be stored on a workload host.
- Docker Hub is intentionally not required.

## Workflow

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
- The GHCR package must be public before RunPod can pull it without a private registry token.

## Visibility

Keep the repository and package private until public-repository hygiene checks pass. A public package can be pulled by RunPod without registry credentials; a private package requires configuring pull credentials in the provider template.
