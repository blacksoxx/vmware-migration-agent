from __future__ import annotations

import re

from cim.schema import CanonicalInfrastructureModel, ComputeUnit, StorageVolume


def render_storage(
    cim: CanonicalInfrastructureModel,
    required_tags: dict[str, str] | None = None,
) -> dict[str, str]:
    """Render Azure storage HCL files for ComputeUnit StorageVolume resources."""
    tags = _build_required_tags(cim.source_vcenter, required_tags)

    output: dict[str, str] = {}
    for compute_unit in cim.compute_units:
        path = f"azure-migration/storage/{compute_unit.name}_disks.tf"
        output[path] = _render_compute_unit_storage(compute_unit, tags)

    return output


def _render_compute_unit_storage(compute_unit: ComputeUnit, tags: dict[str, str]) -> str:
    vm_ref = _terraform_identifier(compute_unit.name)
    lines: list[str] = []

    volumes = list(compute_unit.storage_volumes)
    if not volumes:
        lines.append(f"# ComputeUnit {compute_unit.name} has no StorageVolume entries.")
        return "\n".join(lines) + "\n"

    for index, volume in enumerate(volumes, start=1):
        disk_ref = _disk_ref(compute_unit, volume, index)
        attach_ref = f"{disk_ref}_attach"
        lun = max(index - 1, 0)

        lines.extend(_render_managed_disk(disk_ref, volume, compute_unit, tags))
        lines.append("")

        lines.append(f'resource "azurerm_virtual_machine_data_disk_attachment" "{attach_ref}" {{')
        lines.append(f'  managed_disk_id    = azurerm_managed_disk.{disk_ref}.id')
        lines.append(f'  virtual_machine_id = azurerm_linux_virtual_machine.{vm_ref}.id')
        lines.append(f"  lun                = {lun}")
        lines.append('  caching            = "ReadWrite"')
        lines.append('}')
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _render_managed_disk(
    disk_ref: str,
    volume: StorageVolume,
    compute_unit: ComputeUnit,
    tags: dict[str, str],
) -> list[str]:
    lines: list[str] = []

    lines.append(f'resource "azurerm_managed_disk" "{disk_ref}" {{')
    lines.append(f'  name                 = "${{var.name_prefix}}-{disk_ref}"')
    lines.append('  location             = var.location')
    lines.append('  resource_group_name  = var.resource_group_name')
    lines.append('  storage_account_type = "StandardSSD_LRS"')
    lines.append('  create_option        = "Empty"')
    lines.append(f"  disk_size_gb         = {int(volume.size_gb)}")

    # Enforce encryption-at-rest for all generated managed disks.
    lines.append('  encryption_settings_enabled = true')

    if volume.is_shared:
        lines.append('  # TODO: StorageVolume marked shared; validate shared disk semantics manually.')

    if volume.is_rdm:
        lines.append('  # TODO: StorageVolume marked RDM-derived; manual validation required.')

    lines.append('')
    lines.append('  tags = {')
    lines.append(f'    Name = "{compute_unit.name}-{disk_ref}"')
    lines.append(f'    Datastore = "{(volume.datastore or "").strip()}"')
    lines.append('    StorageEncrypted = "true"')
    for key, value in tags.items():
        lines.append(f'    {key} = "{value}"')
    lines.append('  }')
    lines.append('}')

    return lines


def _disk_ref(compute_unit: ComputeUnit, volume: StorageVolume, index: int) -> str:
    parts = [compute_unit.name, volume.id, str(index)]
    return _terraform_identifier("_".join(part for part in parts if part))


def _build_required_tags(source_vcenter: str, required_tags: dict[str, str] | None) -> dict[str, str]:
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


def _terraform_identifier(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", value.strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")

    if not normalized:
        return "resource"
    if normalized[0].isdigit():
        normalized = f"r_{normalized}"

    return normalized.lower()
