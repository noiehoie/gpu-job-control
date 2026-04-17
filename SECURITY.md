# Security Policy

## Supported Versions

This project is pre-1.0. Security fixes apply to the default branch.

## Secret Handling

Do not commit:

- provider API keys;
- bearer tokens;
- SSH keys;
- registry tokens;
- private endpoint IDs when they identify a live deployment;
- host-specific private IP addresses;
- production `.env` files;
- provider-side credentials embedded in job JSON.

Provider credentials should come from environment variables, provider CLIs, or a host-local secret manager.

## Reporting a Vulnerability

Open a private security advisory if available. If not, contact the maintainer through the repository security advisory workflow without posting exploit details publicly.

## Operational Safety

The control plane is designed to fail closed:

- billing guard blocks unknown paid resources;
- destructive provider operations must be explicitly modeled and policy-gated before exposure;
- provider promotion requires canary evidence;
- secret policy blocks unapproved secret references;
- artifact verification is required before success is trusted.
