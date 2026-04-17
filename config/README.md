# Configuration

The files in this directory are safe defaults and examples.

Runtime deployments should override them with one of:

```text
GPU_JOB_EXECUTION_POLICY=/path/to/execution-policy.json
GPU_JOB_PROFILES_CONFIG=/path/to/gpu-profiles.json
GPU_JOB_CAPABILITIES_CONFIG=/path/to/model-capabilities.json
```

or by placing files under:

```text
$XDG_CONFIG_HOME/gpu-job-control/
```

Do not commit live provider IDs, private network addresses, API keys, or production secrets.

To create a private per-user configuration directory from the safe defaults:

```bash
uv run gpu-job config init
uv run gpu-job config paths
```

This writes to `$XDG_CONFIG_HOME/gpu-job-control/`, or `~/.config/gpu-job-control/` when `XDG_CONFIG_HOME` is not set. Existing files are not overwritten unless `--force` is passed.
