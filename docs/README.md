# VMware Migration Agent Docs

Implementation-accurate documentation for the current codebase.

Last refreshed: 2026-04-20

## At A Glance

- Purpose: Transform VMware discovery JSON into deployable Terraform HCL.
- Runtime shape: Deterministic LangGraph pipeline with limited LLM use.
- LLM scope: HCL generation and review-note writing only.
- Core contract: CIM (Canonical Infrastructure Model) is the system boundary between VMware input and provider output.

## Current Pipeline

The active graph flow is:

1. ingest
2. blocker_parser
3. enricher
4. cim_mapper
5. sizer
6. mcp_client
7. hcl_generator
8. validator
9. reporter (only when validation passes)

Summary:

- Input: VMware discovery JSON.
- Middle: blocker filtering, CIM translation, deterministic sizing, MCP docs retrieval.
- Output: provider Terraform files, quarantine report, review notes.

## Architecture Diagram (ASCII)

```text
                                +--------------------+
                                |     config.yaml    |
                                +----------+---------+
                                           |
                                           v
+----------------------+         +---------+---------+
| VMware discovery JSON| ------> | CLI: vma terragen |
+----------+-----------+         +---------+---------+
           |                                 |
           |                                 v
           |                    +------------+-------------+
           |                    |     LangGraph Pipeline   |
           |                    +------------+-------------+
           |                                 |
           |    ingest -> blocker_parser -> enricher -> cim_mapper -> sizer
           |                                 |
           |                                 v
           |                           +-----+-----+
           |                           | mcp_client|
           |                           +-----+-----+
           |                                 |
           |                                 v
           |                           +-----+-----+      +------------------+
           |                           |hcl_generator| <---| LLMClient (HCL) |
           |                           +-----+-----+      +------------------+
           |                                 |
           |                                 v
           |                           +-----+-----+      +------------------+
           |                           | validator | ---> |TF + TFLint + OPA |
           |                           +-----+-----+      +------------------+
           |                                 |
           |                  passed=true ---+--- passed=false
           |                                 |           |
           |                                 v           v
           |                           +-----+-----+    END
           |                           | reporter  |
           |                           +-----+-----+
           |                                 |
           v                                 v
  +--------+----------+            +---------+-------------------------------+
  | quarantine_report |            | output/{provider}-migration/** + notes  |
  +-------------------+            +-----------------------------------------+
```

Notes:

- CIM is produced at cim_mapper and transformed further by sizer.
- LLM usage is restricted to hcl_generator and reporter.
- reporter only runs when validator reports passed=true.

## Docs Map

- [pipeline.md](pipeline.md): Graph order, node responsibilities, routing, MigrationState.
- [cli.md](cli.md): Commands, options, and practical examples.
- [configuration.md](configuration.md): Runtime config contract and CLI override behavior.
- [cim-and-translation.md](cim-and-translation.md): CIM models and VMware-to-CIM translation rules.
- [output-and-validation.md](output-and-validation.md): Output layout, quarantine/report artifacts, validation gates.

## Command Line Overview

Primary command group: `vma`

Core commands:

- `terragen`: Run the full migration pipeline from discovery JSON to Terraform output.
- `report`: Read generated artifacts from an existing output directory.
- `mcp-check`: Validate Terraform MCP connectivity before generation runs.

Quick-start examples:

```bash
# Full pipeline run
vma terragen --input discovery.json --cloud aws --llm-provider anthropic --environment prod --owner platform-team

# Read output summary from the output directory
vma report output --format text

# Check Terraform MCP connectivity
vma mcp-check --servers terraform --timeout 10 --format text
```

Most-used `terragen` flags:

- Required: `--input`, `--cloud`, `--llm-provider`, `--environment`, `--owner`
- Useful optional: `--llm-model`, `--output-dir`, `--config`
- Control flags: `--dry-run`, `--skip-validation`, `--skip-opa`

For complete option details, see [cli.md](cli.md).

## Important Architecture Notes

- The active graph uses mcp_client, not rag_retriever.
- Validation happens in two phases:
  - Internal generation feedback loop in hcl_generator.
  - Final deterministic gate in validator.
- reporter runs only when validator sets validation_result.passed to true.

## Canonical Terms

Use these terms consistently in code, docs, and review notes:

- CIM
- ComputeUnit
- StorageVolume
- NetworkTopology
- ClusterSemantics
- ProviderModule
- MigrationState
- blocker
- quarantine queue
- sizing lookup
- HCL
