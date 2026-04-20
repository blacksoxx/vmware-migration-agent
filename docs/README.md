# VMware Migration Agent Docs

This folder contains implementation-accurate documentation for the current codebase.

Snapshot date: 2026-04-20

## What This Tool Does

The CLI transforms VMware discovery JSON into Terraform HCL through a deterministic LangGraph pipeline.

Flow:

1. Ingest discovery input.
2. Filter blockers and build quarantine queue.
3. Enrich and map to CIM (Canonical Infrastructure Model).
4. Apply deterministic sizing lookup.
5. Retrieve Terraform documentation context from MCP.
6. Generate HCL (LLM).
7. Run deterministic validation (terraform validate, tflint, OPA).
8. Write output artifacts and review notes.

## Documentation Index

- pipeline.md: Graph order, node responsibilities, routing behavior, and MigrationState.
- cli.md: Commands, options, and practical examples.
- configuration.md: Runtime config contract from config.yaml and CLI override behavior.
- cim-and-translation.md: CIM model structure and VMware-to-CIM translation rules.
- output-and-validation.md: Output layout, quarantine/report files, and validation gates.

## Canonical Terms

Use these project terms consistently:

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

## Notes On Current Architecture

- The active graph uses mcp_client, not rag_retriever.
- LLM usage is limited to hcl_generator and reporter.
- Validation runs in two places:
  - Internal feedback loop in hcl_generator.
  - Final gate in validator before reporter routing.
