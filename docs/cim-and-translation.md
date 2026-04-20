# CIM And Translation

The Canonical Infrastructure Model (CIM) is defined in cim/schema.py and is the contract between VMware discovery input and provider HCL output.

## Core CIM Models

- CanonicalInfrastructureModel
  - cim_schema_version: "1.0"
  - source_vcenter
  - target_provider
  - network_topology
  - clusters
  - compute_units

- ComputeUnit
  - id
  - name
  - vcpus
  - ram_mb
  - migration_status: ready | blocked | needs_review
  - blockers
  - cluster_ref
  - is_encrypted
  - has_vtpm
  - nics
  - storage_volumes

- StorageVolume
  - id
  - size_gb
  - datastore
  - thin_provisioned
  - is_encrypted
  - is_shared
  - is_rdm

- NetworkTopology
  - distributed_switches

- DistributedSwitch
  - name
  - port_groups

- PortGroup
  - name
  - vlan_id

- ComputeCluster
  - name
  - semantics: ha | anti_affinity | affinity | lb | unknown

## VMware To CIM Translation

Implemented in cim/vmware_translator.py.

Translation rules:

- virtual_machines list in discovery payload drives ComputeUnit creation.
- ComputeUnit id priority:
  - discovery_key
  - moid
  - uuid
- ComputeUnit name priority:
  - vm_name
  - name
  - unknown-compute-unit
- vcpus and ram_mb default to 1 when absent.

Network translation:

- Each discovered distributed switch becomes one DistributedSwitch.
- Each discovered port group becomes one PortGroup.
- VLAN IDs are preserved as strings.
- NIC port_group_ref points to translated PortGroup name.

Storage translation:

- Each VMware disk becomes one StorageVolume.
- is_rdm inferred from backing_type containing "rdm".
- is_shared inferred from multi-writer or non-default sharing mode.

## Blockers And Migration Status In Translation

Known blocker values:

- encrypted_vm_present
- rdm_disk
- shared_disk
- vapp_config

Status inference:

- blocked when known blockers are present or migration_blocked is true.
- needs_review when has_vtpm is true without hard blockers.
- ready otherwise.

## Additional Blocker Filtering Node

Before translation, blocker_parser removes hard-blocked workloads from active processing and appends quarantine queue items.
