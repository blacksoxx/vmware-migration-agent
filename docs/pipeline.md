# Pipeline And State

This document describes the active LangGraph workflow and state contract as implemented.

## Graph Execution Order

Defined in agent/graph.py:

1. ingest
2. blocker_parser
3. enricher
4. cim_mapper
5. sizer
6. mcp_client
7. hcl_generator
8. validator
9. reporter

Edges:

- START -> ingest
- ingest -> blocker_parser -> enricher -> cim_mapper -> sizer -> mcp_client -> hcl_generator -> validator
- validator -> reporter (only when validation_result.passed == true)
- validator -> END (when validation_result.passed == false)
- reporter -> END

## Node Responsibilities

- ingest: Validates and normalizes initial discovery input and runtime config in state.
- blocker_parser: Detects hard blockers, removes blocked ComputeUnits from active workload, appends quarantine items.
- enricher: Applies deterministic metadata enrichment before translation.
- cim_mapper: Builds CIM document from discovery payload.
- sizer: Applies provider sizing lookup to each ComputeUnit.
- mcp_client: Pulls provider resource documentation snippets from Terraform MCP server and stores mcp_context.
- hcl_generator: Uses LLM to produce HCL, then runs deterministic pre-output validation feedback loop.
- validator: Runs final deterministic validation gate and sets pass/fail route state.
- reporter: Writes HCL files, quarantine report, and review notes to output.

## Retry Behavior

There is no graph-level retry edge back to hcl_generator.

Retries happen inside hcl_generator:

- max_retries resolved from validation.max_retries, fallback pipeline.max_retries, fallback default 3.
- Each attempt rebuilds prompt with validation feedback from previous attempt.
- On exhausted retries, hcl_generator returns last candidate and validation_result; final pass/fail is still decided by validator.

## MigrationState Contract

Defined in agent/state.py:

Required top-level fields:

- discovery_data: input VMware payload.
- config: runtime config map.
- target_provider: TargetProvider enum.
- cim: CanonicalInfrastructureModel.
- sized_cim: CanonicalInfrastructureModel after sizing lookup.
- mcp_context: Terraform docs context from MCP.
- hcl_output: map of output path -> HCL content.
- validation_result: pass/fail flags plus errors and warnings.
- review_notes: generated report markdown.
- quarantine_queue: list of QuarantineItem.
- messages: audit trail strings.
- retry_count: integer generation retry count.
- status: pending | running | succeeded | failed.

QuarantineItem fields:

- compute_unit_id
- compute_unit_name
- reasons: list[str]
- stage: blocker_parser | validator

ValidationResult fields:

- passed
- terraform_validate_passed
- tflint_passed
- opa_passed
- errors
- warnings

## Important Drift Note

A rag_retriever node exists in source, but the active graph uses mcp_client.
