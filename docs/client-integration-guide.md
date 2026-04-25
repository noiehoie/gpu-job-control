# Client Integration Guide

For downstream systems, the integration order is:

1. use the [Generic System Integration Prompt](generic-system-integration-prompt.md)
2. emit the [Caller Contract](caller-contract.md)
3. choose an operation from the [Operation Catalog](operation-catalog.md)
4. send that request to the [Public API](public-api.md)

## Python reference client

Use [`src/gpu_job/public_client.py`](../src/gpu_job/public_client.py) as the minimal stdlib-based reference client.

## Integration constraints

- callers do not select providers
- callers do not emit execution-job fields directly
- callers must supply idempotency and limits
- callers must fail closed on local validation failure
