from __future__ import annotations

from agent.nodes.validator import validator
from cim.schema import (
    CanonicalInfrastructureModel,
    ComputeUnit,
    MigrationStatus,
    NetworkTopology,
    TargetProvider,
)


def _empty_cim() -> CanonicalInfrastructureModel:
    return CanonicalInfrastructureModel(
        source_vcenter="vc01.local",
        target_provider=TargetProvider.AWS,
        network_topology=NetworkTopology(distributed_switches=[]),
        compute_units=[],
    )


def _non_empty_cim() -> CanonicalInfrastructureModel:
    return CanonicalInfrastructureModel(
        source_vcenter="vc01.local",
        target_provider=TargetProvider.AWS,
        network_topology=NetworkTopology(distributed_switches=[]),
        compute_units=[
            ComputeUnit(
                id="cu-1",
                name="app-1",
                vcpus=2,
                ram_mb=4096,
                migration_status=MigrationStatus.READY,
            )
        ],
    )


def test_validator_skips_when_workload_is_empty() -> None:
    state = {
        "sized_cim": _empty_cim(),
        "hcl_output": {},
        "messages": [],
        "status": "pending",
    }

    updated = validator(state)  # type: ignore[arg-type]

    assert updated["status"] == "running"
    assert updated["validation_result"]["passed"] is True
    assert updated["validation_result"]["errors"] == []


def test_validator_fails_empty_hcl_when_workload_exists() -> None:
    state = {
        "sized_cim": _non_empty_cim(),
        "hcl_output": {},
        "messages": [],
        "status": "pending",
    }

    updated = validator(state)  # type: ignore[arg-type]

    assert updated["status"] == "failed"
    assert updated["validation_result"]["passed"] is False
    assert "hcl_output is empty" in updated["validation_result"]["errors"][0]
