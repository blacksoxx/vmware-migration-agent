# Configuration Reference

Runtime configuration is loaded from config.yaml and then selectively overridden by CLI flags.

## llm

- provider: anthropic | openai | google
- models: per-provider defaults
- temperature_hcl: must stay 0.0
- temperature_report: report generation temperature
- max_tokens
- timeout_seconds

Notes:

- hcl_generator uses LLMClient.generate_hcl.
- reporter uses LLMClient.generate_review_notes.

## target_cloud

Default target provider for generation unless overridden by CLI --cloud.

## pipeline

- batch_size
- max_retries
- abort_threshold_pct

Notes:

- hcl_generator resolves retry count from validation.max_retries first, then pipeline.max_retries.

## validation

- run_terraform_validate
- run_tflint
- run_opa
- terraform_bin
- tflint_bin
- opa_mode (binary|server)
- opa_bin
- opa_server_url
- policies_dir

Behavior:

- validator executes terraform init + terraform validate when enabled.
- validator executes tflint when enabled.
- validator executes OPA policy evaluation when enabled.

## mcp

- enabled
- tool_timeout_seconds
- terraform.image
- terraform.toolsets (must be registry)

Behavior:

- mcp_client requires mcp.enabled = true.
- mcp_client expects registry-only toolset for Terraform MCP retrieval.

## output

- base_dir
- structure (template reference)
- write_quarantine_report
- quarantine_report_filename
- write_review_notes
- review_notes_filename
- overwrite

Behavior:

- reporter writes hcl_output keys under output.base_dir.
- reporter removes provider root directories when overwrite = true.

## vsphere

- drs_rule_mapping
- hard_blockers
- soft_blockers

Behavior:

- blocker_parser enforces hard blockers before CIM mapping.
- soft blockers typically produce needs_review behavior later in pipeline.

## required_tags

Default tags injected into generated resources:

- Environment
- Owner
- MigratedFrom
- MigrationTool
- SourceVCenter
- GeneratedAt

CLI overwrites Environment and Owner at runtime.

## logging

- level
- format (json or console)
