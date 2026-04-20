from __future__ import annotations

from typing import Literal, TypedDict

from cim.schema import CanonicalInfrastructureModel, TargetProvider

JSONValue = (
    None
    | bool
    | int
    | float
    | str
    | list["JSONValue"]
    | dict[str, "JSONValue"]
)


class QuarantineItem(TypedDict):
    compute_unit_id: str
    compute_unit_name: str
    reasons: list[str]
    stage: Literal["blocker_parser", "validator"]


class ValidationResult(TypedDict, total=False):
    passed: bool
    terraform_validate_passed: bool
    tflint_passed: bool
    opa_passed: bool
    errors: list[str]
    warnings: list[str]


class MigrationState(TypedDict):
    discovery_data: dict[str, JSONValue]
    config: dict[str, JSONValue]
    target_provider: TargetProvider

    cim: CanonicalInfrastructureModel
    sized_cim: CanonicalInfrastructureModel
    mcp_context: str
    hcl_output: dict[str, str]
    validation_result: ValidationResult
    review_notes: str

    quarantine_queue: list[QuarantineItem]
    messages: list[str]
    retry_count: int
    status: Literal["pending", "running", "succeeded", "failed"]
