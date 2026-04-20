from __future__ import annotations

from typing import cast

from loguru import logger

from agent.state import MigrationState
from cim.vmware_translator import translate_vmware_discovery_to_cim


def cim_mapper(state: MigrationState) -> MigrationState:
    """Map enriched VMware discovery payload to the canonical CIM document."""
    next_state = cast(MigrationState, dict(state))

    discovery_data = next_state.get("discovery_data")
    if not isinstance(discovery_data, dict):
        raise ValueError("cim_mapper: discovery_data must be present before mapping to CIM")

    target_provider = next_state.get("target_provider")
    if target_provider is None:
        raise ValueError("cim_mapper: target_provider is required")

    cim = translate_vmware_discovery_to_cim(
        discovery_data=discovery_data,
        target_provider=target_provider,
    )

    messages = list(next_state.get("messages", []))
    messages.append(
        "cim_mapper: compute_units={} clusters={} switches={}".format(
            len(cim.compute_units),
            len(cim.clusters),
            len(cim.network_topology.distributed_switches),
        )
    )

    next_state["cim"] = cim
    next_state["messages"] = messages
    next_state["status"] = "running"

    logger.info(
        "cim_mapper: produced CIM with {} ComputeUnits, {} clusters, {} distributed switches",
        len(cim.compute_units),
        len(cim.clusters),
        len(cim.network_topology.distributed_switches),
    )

    return next_state
