from __future__ import annotations

import re

from cim.schema import CanonicalInfrastructureModel, ComputeUnit
from providers.azure.sizing_table import get_instance_type


def render_compute(
    cim: CanonicalInfrastructureModel,
    required_tags: dict[str, str] | None = None,
) -> dict[str, str]:
    """Render one Azure compute HCL file per ComputeUnit."""
    tags = _build_required_tags(cim.source_vcenter, required_tags)

    output: dict[str, str] = {}
    for compute_unit in cim.compute_units:
        path = f"azure-migration/compute/{compute_unit.name}.tf"
        output[path] = _render_compute_unit(compute_unit, tags)

    return output


def _render_compute_unit(compute_unit: ComputeUnit, tags: dict[str, str]) -> str:
    resource_name = _terraform_identifier(compute_unit.name)
    vm_size = get_instance_type(compute_unit.vcpus, compute_unit.ram_mb)
    nic_ref = _nic_ref(compute_unit)

    lines: list[str] = []

    lines.append(f'resource "azurerm_network_interface" "{nic_ref}" {{')
    lines.append('  name                = "${var.name_prefix}-nic-' + resource_name + '"')
    lines.append('  location            = var.location')
    lines.append('  resource_group_name = var.resource_group_name')
    lines.append('')
    lines.append('  ip_configuration {')
    lines.append('    name                          = "internal"')
    lines.append(f'    subnet_id                     = azurerm_subnet.{_subnet_ref(compute_unit)}.id')
    lines.append('    private_ip_address_allocation = "Dynamic"')
    lines.append('  }')
    lines.append('')
    lines.append('  tags = {')
    lines.append(f'    Name = "{compute_unit.name}"')
    for key, value in tags.items():
        lines.append(f'    {key} = "{value}"')
    lines.append('  }')
    lines.append('}')
    lines.append('')

    lines.append(f'resource "azurerm_linux_virtual_machine" "{resource_name}" {{')
    lines.append('  name                = "${var.name_prefix}-' + resource_name + '"')
    lines.append('  location            = var.location')
    lines.append('  resource_group_name = var.resource_group_name')
    lines.append(f'  size                = "{vm_size}"')
    lines.append('  admin_username      = var.admin_username')
    lines.append(f'  network_interface_ids = [azurerm_network_interface.{nic_ref}.id]')
    lines.append('')
    lines.append('  admin_ssh_key {')
    lines.append('    username   = var.admin_username')
    lines.append('    public_key = var.admin_public_key')
    lines.append('  }')
    lines.append('')
    lines.append('  os_disk {')
    lines.append('    caching              = "ReadWrite"')
    lines.append('    storage_account_type = "StandardSSD_LRS"')
    lines.append('  }')
    lines.append('')
    lines.append('  source_image_reference {')
    lines.append('    publisher = "Canonical"')
    lines.append('    offer     = "0001-com-ubuntu-server-jammy"')
    lines.append('    sku       = "22_04-lts"')
    lines.append('    version   = "latest"')
    lines.append('  }')

    if compute_unit.has_vtpm:
        lines.append('')
        lines.append('  # TODO: ComputeUnit has vTPM and needs manual review.')

    lines.append('')
    lines.append('  tags = {')
    lines.append(f'    Name = "{compute_unit.name}"')
    for key, value in tags.items():
        lines.append(f'    {key} = "{value}"')
    lines.append('  }')
    lines.append('}')

    return "\n".join(lines)


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


def _nic_ref(compute_unit: ComputeUnit) -> str:
    return f"nic_{_terraform_identifier(compute_unit.name)}"


def _subnet_ref(compute_unit: ComputeUnit) -> str:
    if compute_unit.nics:
        return _terraform_identifier(compute_unit.nics[0].port_group_ref)
    return "default"


def _terraform_identifier(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", value.strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")

    if not normalized:
        return "resource"
    if normalized[0].isdigit():
        normalized = f"r_{normalized}"

    return normalized.lower()
