from __future__ import annotations

from typing import cast

from loguru import logger

from agent.state import JSONValue, MigrationState, QuarantineItem

CANONICAL_HARD_BLOCKERS = {
    "encrypted_vm_present",
    "rdm_disk",
    "shared_disk",
    "vapp_config",
}

BLOCKER_NORMALIZATION = {
    "encrypted_vm_present": "encrypted_vm_present",
    "rdm_disk": "rdm_disk",
    "rdm_disk_present": "rdm_disk",
    "shared_disk": "shared_disk",
    "shared_disk_present": "shared_disk",
    "vapp_config": "vapp_config",
    "vapp_config_present": "vapp_config",
}


def blocker_parser(state: MigrationState) -> MigrationState:
    """Detect blockers, quarantine blocked ComputeUnits, and keep only clean resources."""
    next_state = cast(MigrationState, dict(state))

    discovery_data = next_state.get("discovery_data", {})
    if not isinstance(discovery_data, dict):
        raise ValueError("blocker_parser: discovery_data must be a mapping")

    vms_raw = discovery_data.get("virtual_machines", [])
    if not isinstance(vms_raw, list):
        raise ValueError("blocker_parser: discovery_data.virtual_machines must be a list")

    hard_blockers = _resolve_hard_blockers(next_state)
    quarantine_queue = list(next_state.get("quarantine_queue", []))

    clean_vms: list[dict[str, JSONValue]] = []
    blocked_count = 0

    for vm_raw in vms_raw:
        if not isinstance(vm_raw, dict):
            continue

        vm = cast(dict[str, JSONValue], vm_raw)
        normalized_blockers = _extract_vm_blockers(vm)
        matching_hard = sorted(b for b in normalized_blockers if b in hard_blockers)

        if matching_hard:
            blocked_count += 1
            quarantine_queue.append(
                _make_quarantine_item(
                    vm=vm,
                    reasons=matching_hard,
                )
            )
            continue

        clean_vms.append(vm)

    updated_discovery = cast(dict[str, JSONValue], dict(discovery_data))
    updated_discovery["virtual_machines"] = cast(JSONValue, clean_vms)

    messages = list(next_state.get("messages", []))
    messages.append(
        "blocker_parser: blocked={} clean={}".format(blocked_count, len(clean_vms))
    )

    next_state["discovery_data"] = updated_discovery
    next_state["quarantine_queue"] = quarantine_queue
    next_state["messages"] = messages
    next_state["status"] = "running"

    logger.info(
        "blocker_parser: filtered virtual_machines from {} to {} and quarantined {}",
        len(vms_raw),
        len(clean_vms),
        blocked_count,
    )

    return next_state


def _resolve_hard_blockers(state: MigrationState) -> set[str]:
    config = state.get("config", {})
    if not isinstance(config, dict):
        return set(CANONICAL_HARD_BLOCKERS)

    vsphere = config.get("vsphere", {})
    if not isinstance(vsphere, dict):
        return set(CANONICAL_HARD_BLOCKERS)

    configured = vsphere.get("hard_blockers", [])
    if not isinstance(configured, list):
        return set(CANONICAL_HARD_BLOCKERS)

    normalized: set[str] = set()
    for item in configured:
        key = str(item).strip().lower()
        canonical = BLOCKER_NORMALIZATION.get(key)
        if canonical:
            normalized.add(canonical)

    return normalized or set(CANONICAL_HARD_BLOCKERS)


def _extract_vm_blockers(vm: dict[str, JSONValue]) -> set[str]:
    found: set[str] = set()

    blockers = vm.get("migration_blockers", [])
    if isinstance(blockers, list):
        for blocker in blockers:
            key = str(blocker).strip().lower()
            canonical = BLOCKER_NORMALIZATION.get(key)
            if canonical:
                found.add(canonical)

    if bool(vm.get("is_encrypted")):
        found.add("encrypted_vm_present")
    if bool(vm.get("has_rdm_disks")):
        found.add("rdm_disk")
    if bool(vm.get("has_shared_disks")):
        found.add("shared_disk")
    if bool(vm.get("vapp_config_present")):
        found.add("vapp_config")

    return found


def _make_quarantine_item(vm: dict[str, JSONValue], reasons: list[str]) -> QuarantineItem:
    compute_unit_id = str(vm.get("discovery_key") or vm.get("moid") or vm.get("uuid") or "")
    compute_unit_name = str(vm.get("vm_name") or vm.get("name") or "unknown-compute-unit")

    return {
        "compute_unit_id": compute_unit_id,
        "compute_unit_name": compute_unit_name,
        "reasons": reasons,
        "stage": "blocker_parser",
    }
