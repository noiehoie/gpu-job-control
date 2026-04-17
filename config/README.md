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
