# Configuration

The files in this directory are safe defaults and examples.

Runtime deployments should override them with one of:

```text
GPU_JOB_EXECUTION_POLICY=/path/to/execution-policy.json
GPU_JOB_PROVIDER_OPERATIONS_POLICY=/path/to/provider-operations.json
GPU_JOB_PROFILES_CONFIG=/path/to/gpu-profiles.json
GPU_JOB_CAPABILITIES_CONFIG=/path/to/model-capabilities.json
```

or by placing files under:

```text
$XDG_CONFIG_HOME/gpu-job-control/
```

Do not commit live provider IDs, private network addresses, API keys, or production secrets.

`execution-policy.json` is the generic broker policy: timeouts, provider/profile
concurrency, budget classes, and deterministic routing limits.
Provider-specific operational allowances such as live RunPod network volumes and
secret scopes belong in a private provider operations file. Use
`provider-operations.example.json` as the safe template and keep local values in
`provider-operations.local.json` or the file pointed to by
`GPU_JOB_PROVIDER_OPERATIONS_POLICY`.

Provider operations resolution is deterministic:

1. `GPU_JOB_PROVIDER_OPERATIONS_POLICY`, when set.
2. `$XDG_CONFIG_HOME/gpu-job-control/provider-operations.json`, when present.
3. Repository default `config/provider-operations.json`, when present.
4. `provider-operations.local.json` in the same directory as the resolved
   primary provider-operations path, when present and that primary file is
   absent. With the repository defaults, this is
   `config/provider-operations.local.json`.
5. No provider operations overlay.

Passing an explicit path to `load_execution_policy(path=...)` loads only that
file and intentionally does not merge provider operations.

To create a private per-user configuration directory from the safe defaults:

```bash
uv run gpu-job config init
uv run gpu-job config paths
```

This writes to `$XDG_CONFIG_HOME/gpu-job-control/`, or `~/.config/gpu-job-control/` when `XDG_CONFIG_HOME` is not set. Existing files are not overwritten unless `--force` is passed.
