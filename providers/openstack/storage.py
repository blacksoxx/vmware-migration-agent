from __future__ import annotations

import re

from cim.schema import CanonicalInfrastructureModel, ComputeUnit, StorageVolume


def render_storage(
    cim: CanonicalInfrastructureModel,
    required_tags: dict[str, str] | None = None,
) -> dict[str, str]:
    """Render OpenStack storage HCL files for ComputeUnit StorageVolume resources."""
    metadata = _build_required_metadata(cim.source_vcenter, required_tags)

    output: dict[str, str] = {}
    for compute_unit in cim.compute_units:
        path = f"openstack-migration/storage/{compute_unit.name}_disks.tf"
        output[path] = _render_compute_unit_storage(compute_unit, metadata)

    return output


def _render_compute_unit_storage(compute_unit: ComputeUnit, metadata: dict[str, str]) -> str:
    instance_ref = _terraform_identifier(compute_unit.name)
    lines: list[str] = []

    volumes = list(compute_unit.storage_volumes)
    if not volumes:
        lines.append(f"# ComputeUnit {compute_unit.name} has no StorageVolume entries.")
        return "\n".join(lines) + "\n"

    for index, volume in enumerate(volumes, start=1):
        volume_ref = _volume_ref(compute_unit, volume, index)
        attach_ref = f"{volume_ref}_attach"

        lines.extend(_render_volume(volume_ref, volume, compute_unit, metadata))
        lines.append("")

        lines.append(f'resource "openstack_compute_volume_attach_v2" "{attach_ref}" {{')
        lines.append(f'  instance_id = openstack_compute_instance_v2.{instance_ref}.id')
        lines.append(f'  volume_id   = openstack_blockstorage_volume_v3.{volume_ref}.id')
        lines.append('}')
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _render_volume(
    volume_ref: str,
    volume: StorageVolume,
    compute_unit: ComputeUnit,
    metadata: dict[str, str],
) -> list[str]:
    lines: list[str] = []

    lines.append(f'resource "openstack_blockstorage_volume_v3" "{volume_ref}" {{')
    lines.append(f'  name        = "{compute_unit.name}-{volume_ref}"')
    lines.append(f'  size        = {int(volume.size_gb)}')
    lines.append('  volume_type = var.encrypted_volume_type')
    lines.append('')

    if volume.is_shared:
        lines.append('  # TODO: StorageVolume marked shared; validate multi-attach requirements manually.')

    if volume.is_rdm:
        lines.append('  # TODO: StorageVolume marked RDM-derived; manual validation required.')

    lines.append('')
    lines.append('  metadata = {')
    lines.append(f'    Name = "{compute_unit.name}-{volume_ref}"')
    lines.append(f'    Datastore = "{(volume.datastore or "").strip()}"')
    lines.append('    StorageEncrypted = "true"')
    for key, value in metadata.items():
        lines.append(f'    {key} = "{value}"')
    lines.append('  }')
    lines.append('}')

    return lines


def _build_required_metadata(
    source_vcenter: str,
    required_tags: dict[str, str] | None,
) -> dict[str, str]:
    defaults = {
        "Environment": "${var.environment}",
        "Owner": "${var.owner}",
        "MigratedFrom": "vmware-vcenter",
        "SourceVCenter": source_vcenter,
    }

    if not required_tags:
        return defaults

    merged = dict(defaults)
    for key, value in required_tags.items():
        merged[str(key)] = str(value)

    return merged


def _volume_ref(compute_unit: ComputeUnit, volume: StorageVolume, index: int) -> str:
    parts = [compute_unit.name, volume.id, str(index)]
    return _terraform_identifier("_".join(part for part in parts if part))


def _terraform_identifier(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", value.strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")

    if not normalized:
        return "resource"
    if normalized[0].isdigit():
        normalized = f"r_{normalized}"

    return normalized.lower()
