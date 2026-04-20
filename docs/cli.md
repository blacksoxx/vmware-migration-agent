# CLI Reference

CLI entrypoint is cli/main.py.

## Command: terragen

Converts VMware discovery JSON into provider HCL via the full pipeline.

Required options:

- --input
- --cloud (aws|azure|gcp|openstack)
- --llm-provider (anthropic|openai|google)
- --environment
- --owner

Optional options:

- --llm-model
- --llm-base-url (or env var LLM_BASE_URL)
- --llm-api-key (or env var LLM_API_KEY)
- --output-dir (default: output)
- --config (default: config.yaml)

Flags:

- --dry-run
- --skip-validation (disables terraform validate, tflint, OPA)
- --skip-opa

Example:

```bash
vma terragen --input discovery.json --cloud aws --llm-provider anthropic --environment prod --owner platform-team
```

## Command: report

Reads existing artifacts from an output directory.

Arguments/options:

- output_dir (required argument)
- --format (text|json)

Expected files:

- review_notes.md
- quarantine_report.json

Example:

```bash
vma report output --format text
```

## Command: mcp-check

Validates Terraform MCP stdio connectivity.

Options:

- --config (default: config.yaml)
- --servers (comma-separated names or configured)
- --top-k
- --timeout
- --format (text|json)

Example:

```bash
vma mcp-check --servers terraform --timeout 10 --format text
```

## Runtime Overrides Applied By CLI

Before graph execution, terragen injects overrides into runtime config:

- target_cloud from --cloud.
- llm.provider and llm.model from CLI values.
- llm.base_url when provided.
- output.base_dir from --output-dir.
- validation skip flags from --skip-validation and --skip-opa.
- required_tags.Environment and required_tags.Owner from CLI.
