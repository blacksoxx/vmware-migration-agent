from __future__ import annotations

from collections import defaultdict
from typing import Any

from cim.schema import (
    CanonicalInfrastructureModel,
    ClusterSemantics,
    ComputeCluster,
    ComputeUnit,
    DistributedSwitch,
    MigrationStatus,
    NetworkTopology,
    NIC,
    PortGroup,
    StorageVolume,
    TargetProvider,
)

SUPPORTED_BLOCKERS = {
    "encrypted_vm_present",
    "rdm_disk",
    "shared_disk",
    "vapp_config",
}


def _to_target_provider(provider: TargetProvider | str) -> TargetProvider:
    if isinstance(provider, TargetProvider):
        return provider
    return TargetProvider(str(provider).strip().lower())


def _infer_blockers(vm: dict[str, Any]) -> list[str]:
    blockers: list[str] = []

    for blocker in vm.get("migration_blockers", []) or []:
        value = str(blocker).strip()
        if value and value not in blockers:
            blockers.append(value)

    if vm.get("is_encrypted") and "encrypted_vm_present" not in blockers:
        blockers.append("encrypted_vm_present")
    if vm.get("has_rdm_disks") and "rdm_disk" not in blockers:
        blockers.append("rdm_disk")
    if vm.get("has_shared_disks") and "shared_disk" not in blockers:
        blockers.append("shared_disk")
    if vm.get("vapp_config_present") and "vapp_config" not in blockers:
        blockers.append("vapp_config")

    return blockers


def _infer_migration_status(vm: dict[str, Any], blockers: list[str]) -> MigrationStatus:
    has_known_blocker = any(blocker in SUPPORTED_BLOCKERS for blocker in blockers)
    if has_known_blocker or vm.get("migration_blocked"):
        return MigrationStatus.BLOCKED
    if vm.get("has_vtpm"):
        return MigrationStatus.NEEDS_REVIEW
    return MigrationStatus.READY


def _build_storage_volumes(vm: dict[str, Any]) -> list[StorageVolume]:
    volumes: list[StorageVolume] = []
    is_vm_encrypted = bool(vm.get("is_encrypted", False))

    for idx, disk in enumerate(vm.get("disks", []) or []):
        backing_type = str(disk.get("backing_type", "")).lower()
        sharing = str(disk.get("sharing", "")).lower()
        disk_id = str(
            disk.get("filename")
            or disk.get("label")
            or disk.get("unit_number")
            or f"disk-{idx + 1}"
        )
        size_gb_raw = disk.get("size_gb", 1)
        size_gb = max(1, int(round(float(size_gb_raw))))

        volumes.append(
            StorageVolume(
                id=disk_id,
                size_gb=size_gb,
                datastore=disk.get("datastore"),
                thin_provisioned=disk.get("thin_provisioned"),
                is_encrypted=is_vm_encrypted,
                is_shared=bool(disk.get("multi_writer")) or sharing != "sharingnone",
                is_rdm="rdm" in backing_type,
            )
        )

    return volumes


def _network_name(network: dict[str, Any], fallback_index: int) -> str:
    for key in ("network_name", "portgroup_name", "port_group", "label"):
        value = network.get(key)
        if value:
            return str(value)
    return f"port-group-{fallback_index}"


def _switch_name(network: dict[str, Any]) -> str:
    for key in ("distributed_switch", "switch_name", "vds_name", "dvs_name"):
        value = network.get(key)
        if value:
            return str(value)
    return "default-dvs"


def _build_nics(vm: dict[str, Any]) -> list[NIC]:
    nics: list[NIC] = []

    for idx, network in enumerate(vm.get("networks", []) or [], start=1):
        port_group_ref = _network_name(network, idx)
        nics.append(
            NIC(
                name=str(network.get("label") or f"nic-{idx}"),
                port_group_ref=port_group_ref,
                mac_address=network.get("mac_address"),
            )
        )

    return nics


def _build_network_topology(vms: list[dict[str, Any]]) -> NetworkTopology:
    switch_to_portgroups: dict[str, dict[str, str]] = defaultdict(dict)

    for vm in vms:
        networks = vm.get("networks", []) or []
        for idx, network in enumerate(networks, start=1):
            switch_name = _switch_name(network)
            port_group_name = _network_name(network, idx)
            vlan_id = str(network.get("vlan_id") or "")
            switch_to_portgroups[switch_name][port_group_name] = vlan_id

    distributed_switches: list[DistributedSwitch] = []
    for switch_name in sorted(switch_to_portgroups):
        port_groups = [
            PortGroup(name=name, vlan_id=vlan)
            for name, vlan in sorted(switch_to_portgroups[switch_name].items())
        ]
        distributed_switches.append(DistributedSwitch(name=switch_name, port_groups=port_groups))

    return NetworkTopology(distributed_switches=distributed_switches)


def _build_clusters(vms: list[dict[str, Any]]) -> list[ComputeCluster]:
    cluster_names = {
        str(vm.get("cluster")).strip()
        for vm in vms
        if vm.get("cluster") is not None and str(vm.get("cluster")).strip()
    }

    return [
        ComputeCluster(name=name, semantics=ClusterSemantics.UNKNOWN)
        for name in sorted(cluster_names)
    ]


def translate_vmware_discovery_to_cim(
    discovery_data: dict[str, Any],
    target_provider: TargetProvider | str,
) -> CanonicalInfrastructureModel:
    """Translate VMware discovery JSON into a CIM document."""
    vms = discovery_data.get("virtual_machines", []) or []

    if not isinstance(vms, list):
        raise ValueError("Expected 'virtual_machines' to be a list in discovery data")

    source_vcenter = ""
    if vms:
        source_vcenter = str(vms[0].get("source_vcenter") or "")
    if not source_vcenter:
        vcenters = discovery_data.get("metadata", {}).get("vcenters_queried", []) or []
        if vcenters:
            source_vcenter = str(vcenters[0])

    compute_units: list[ComputeUnit] = []
    for vm in vms:
        blockers = _infer_blockers(vm)
        migration_status = _infer_migration_status(vm, blockers)
        cluster_ref_raw = vm.get("cluster")
        cluster_ref = str(cluster_ref_raw).strip() if cluster_ref_raw is not None else None

        compute_unit = ComputeUnit(
            id=str(vm.get("discovery_key") or vm.get("moid") or vm.get("uuid") or ""),
            name=str(vm.get("vm_name") or vm.get("name") or "unknown-compute-unit"),
            vcpus=int(vm.get("vcpus") or 1),
            ram_mb=int(vm.get("ram_mb") or 1),
            migration_status=migration_status,
            blockers=blockers,
            cluster_ref=cluster_ref or None,
            is_encrypted=bool(vm.get("is_encrypted", False)),
            has_vtpm=bool(vm.get("has_vtpm", False)),
            nics=_build_nics(vm),
            storage_volumes=_build_storage_volumes(vm),
        )
        compute_units.append(compute_unit)

    return CanonicalInfrastructureModel(
        cim_schema_version="1.0",
        source_vcenter=source_vcenter,
        target_provider=_to_target_provider(target_provider),
        network_topology=_build_network_topology(vms),
        clusters=_build_clusters(vms),
        compute_units=compute_units,
    )
