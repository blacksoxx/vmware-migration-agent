from __future__ import annotations

from typing import cast

from loguru import logger

from agent.state import JSONValue, MigrationState


def enricher(state: MigrationState) -> MigrationState:
    """Normalize discovery payload fields for deterministic downstream processing."""
    next_state = cast(MigrationState, dict(state))

    discovery_data = next_state.get("discovery_data", {})
    if not isinstance(discovery_data, dict):
        raise ValueError("enricher: discovery_data must be a mapping")

    vms_raw = discovery_data.get("virtual_machines", [])
    if not isinstance(vms_raw, list):
        raise ValueError("enricher: discovery_data.virtual_machines must be a list")

    metadata = discovery_data.get("metadata", {})
    metadata_map = metadata if isinstance(metadata, dict) else {}
    fallback_source_vcenter = _resolve_fallback_source_vcenter(metadata_map)

    enriched_vms: list[dict[str, JSONValue]] = []
    for vm_raw in vms_raw:
        if not isinstance(vm_raw, dict):
            continue
        vm = cast(dict[str, JSONValue], vm_raw)
        enriched_vms.append(_enrich_vm(vm, fallback_source_vcenter))

    updated_discovery = cast(dict[str, JSONValue], dict(discovery_data))
    updated_discovery["virtual_machines"] = cast(JSONValue, enriched_vms)

    messages = list(next_state.get("messages", []))
    messages.append(f"enricher: normalized={len(enriched_vms)}")

    next_state["discovery_data"] = updated_discovery
    next_state["messages"] = messages
    next_state["status"] = "running"

    logger.info("enricher: normalized {} virtual_machines", len(enriched_vms))

    return next_state


def _resolve_fallback_source_vcenter(metadata: dict[str, JSONValue]) -> str:
    vcenters = metadata.get("vcenters_queried", [])
    if isinstance(vcenters, list) and vcenters:
        first = vcenters[0]
        if isinstance(first, str):
            return first
    return ""


def _enrich_vm(vm: dict[str, JSONValue], fallback_source_vcenter: str) -> dict[str, JSONValue]:
    normalized = cast(dict[str, JSONValue], dict(vm))

    vm_name = _as_str(normalized.get("vm_name")) or _as_str(normalized.get("name")) or "unknown-compute-unit"
    discovery_key = (
        _as_str(normalized.get("discovery_key"))
        or _as_str(normalized.get("moid"))
        or _as_str(normalized.get("uuid"))
        or vm_name
    )

    normalized["vm_name"] = vm_name
    normalized["discovery_key"] = discovery_key
    normalized["vcpus"] = _as_positive_int(normalized.get("vcpus"), default=1)
    normalized["ram_mb"] = _as_positive_int(normalized.get("ram_mb"), default=1)

    source_vcenter = _as_str(normalized.get("source_vcenter")) or fallback_source_vcenter
    normalized["source_vcenter"] = source_vcenter

    normalized["migration_blockers"] = cast(
        JSONValue,
        _normalize_blockers(normalized.get("migration_blockers")),
    )
    normalized["networks"] = cast(JSONValue, _normalize_networks(normalized.get("networks")))
    normalized["disks"] = cast(JSONValue, _normalize_disks(normalized.get("disks")))

    if "has_vtpm" not in normalized:
        normalized["has_vtpm"] = False
    if "is_encrypted" not in normalized:
        normalized["is_encrypted"] = False
    if "migration_blocked" not in normalized:
        normalized["migration_blocked"] = False

    return normalized


def _normalize_blockers(value: JSONValue | None) -> list[str]:
    if not isinstance(value, list):
        return []

    seen: set[str] = set()
    result: list[str] = []
    for item in value:
        blocker = _as_str(item).lower()
        if blocker and blocker not in seen:
            seen.add(blocker)
            result.append(blocker)

    return result


def _normalize_networks(value: JSONValue | None) -> list[dict[str, JSONValue]]:
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, JSONValue]] = []
    for idx, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue

        network = cast(dict[str, JSONValue], dict(item))
        network_name = (
            _as_str(network.get("network_name"))
            or _as_str(network.get("portgroup_name"))
            or _as_str(network.get("label"))
            or f"port-group-{idx}"
        )
        network["network_name"] = network_name
        network["vlan_id"] = _as_str(network.get("vlan_id"))
        normalized.append(network)

    return normalized


def _normalize_disks(value: JSONValue | None) -> list[dict[str, JSONValue]]:
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, JSONValue]] = []
    for idx, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue

        disk = cast(dict[str, JSONValue], dict(item))
        filename = _as_str(disk.get("filename")) or _as_str(disk.get("label")) or f"disk-{idx}"
        disk["filename"] = filename

        size_value = disk.get("size_gb")
        size_gb = _as_positive_float(size_value, default=1.0)
        disk["size_gb"] = size_gb

        normalized.append(disk)

    return normalized


def _as_str(value: JSONValue | None) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    if isinstance(value, (int, float, bool)):
        return str(value)
    return ""


def _as_positive_int(value: JSONValue | None, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if value > 0 else default
    if isinstance(value, float):
        result = int(value)
        return result if result > 0 else default
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            parsed = int(stripped)
            return parsed if parsed > 0 else default
    return default


def _as_positive_float(value: JSONValue | None, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if numeric > 0 else default
    if isinstance(value, str):
        try:
            numeric = float(value.strip())
            return numeric if numeric > 0 else default
        except ValueError:
            return default
    return default
