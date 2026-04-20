from __future__ import annotations

import re

from cim.schema import CanonicalInfrastructureModel, DistributedSwitch


def render_networking(cim: CanonicalInfrastructureModel) -> dict[str, str]:
    """Render Azure networking HCL files from NetworkTopology."""
    switches = list(cim.network_topology.distributed_switches)
    if not switches:
        switches = [DistributedSwitch(name="default-dvs", port_groups=[])]

    vnet_hcl = _render_vnet_file(cim, switches)
    subnets_hcl = _render_subnets_file(switches)
    security_groups_hcl = _render_security_groups_file(switches)

    return {
        "azure-migration/networking/vpc.tf": vnet_hcl,
        "azure-migration/networking/subnets.tf": subnets_hcl,
        "azure-migration/networking/security_groups.tf": security_groups_hcl,
    }


def _render_vnet_file(cim: CanonicalInfrastructureModel, switches: list[DistributedSwitch]) -> str:
    lines: list[str] = []

    for switch_index, switch in enumerate(switches, start=1):
        vnet_name = _terraform_identifier(switch.name)
        cidr = _vnet_cidr(switch_index)

        lines.append(f'resource "azurerm_virtual_network" "{vnet_name}" {{')
        lines.append(f'  name                = "${{var.name_prefix}}-{vnet_name}"')
        lines.append('  location            = var.location')
        lines.append('  resource_group_name = var.resource_group_name')
        lines.append(f'  address_space       = ["{cidr}"]')
        lines.append('')
        lines.append('  tags = {')
        lines.append(f'    Name = "{switch.name}"')
        lines.append('    Environment = "${var.environment}"')
        lines.append('    Owner = "${var.owner}"')
        lines.append('    MigratedFrom = "vmware-vcenter"')
        lines.append(f'    SourceVCenter = "{cim.source_vcenter}"')
        lines.append('  }')
        lines.append('}')
        lines.append('')

    return "\n".join(lines).strip() + "\n"


def _render_subnets_file(switches: list[DistributedSwitch]) -> str:
    lines: list[str] = []
    has_any_subnet = False

    for switch_index, switch in enumerate(switches, start=1):
        vnet_ref = _terraform_identifier(switch.name)
        port_groups = list(switch.port_groups)

        for subnet_index, port_group in enumerate(port_groups, start=1):
            has_any_subnet = True
            subnet_ref = _terraform_identifier(port_group.name)
            cidr = _subnet_cidr(switch_index, subnet_index)

            lines.append(f'resource "azurerm_subnet" "{subnet_ref}" {{')
            lines.append(f'  name                 = "{port_group.name}"')
            lines.append('  resource_group_name  = var.resource_group_name')
            lines.append(f'  virtual_network_name = azurerm_virtual_network.{vnet_ref}.name')
            lines.append(f'  address_prefixes     = ["{cidr}"]')
            lines.append('}')
            lines.append('')

        if not port_groups:
            has_any_subnet = True
            lines.append('resource "azurerm_subnet" "default" {')
            lines.append('  name                 = "default"')
            lines.append('  resource_group_name  = var.resource_group_name')
            lines.append(f'  virtual_network_name = azurerm_virtual_network.{vnet_ref}.name')
            lines.append('  address_prefixes     = ["10.10.1.0/24"]')
            lines.append('}')
            lines.append('')

    if not has_any_subnet:
        lines.append('resource "azurerm_subnet" "default" {')
        lines.append('  name                 = "default"')
        lines.append('  resource_group_name  = var.resource_group_name')
        lines.append('  virtual_network_name = azurerm_virtual_network.default_dvs.name')
        lines.append('  address_prefixes     = ["10.10.1.0/24"]')
        lines.append('}')
        lines.append('')

    return "\n".join(lines).strip() + "\n"


def _render_security_groups_file(switches: list[DistributedSwitch]) -> str:
    lines: list[str] = []

    lines.append('resource "azurerm_network_security_group" "compute_unit" {')
    lines.append('  name                = "${var.name_prefix}-compute-unit-nsg"')
    lines.append('  location            = var.location')
    lines.append('  resource_group_name = var.resource_group_name')
    lines.append('')
    lines.append('  security_rule {')
    lines.append('    name                       = "AllowSshFromVnet"')
    lines.append('    priority                   = 100')
    lines.append('    direction                  = "Inbound"')
    lines.append('    access                     = "Allow"')
    lines.append('    protocol                   = "Tcp"')
    lines.append('    source_port_range          = "*"')
    lines.append('    destination_port_range     = "22"')
    lines.append('    source_address_prefix      = "VirtualNetwork"')
    lines.append('    destination_address_prefix = "*"')
    lines.append('  }')
    lines.append('')
    lines.append('  tags = {')
    lines.append('    Name = "compute-unit-nsg"')
    lines.append('    Environment = "${var.environment}"')
    lines.append('    Owner = "${var.owner}"')
    lines.append('    MigratedFrom = "vmware-vcenter"')
    lines.append('  }')
    lines.append('}')
    lines.append('')

    subnet_refs: list[str] = []
    for switch in switches:
        if switch.port_groups:
            subnet_refs.extend(_terraform_identifier(pg.name) for pg in switch.port_groups)
        else:
            subnet_refs.append("default")

    seen: set[str] = set()
    for subnet_ref in subnet_refs:
        if subnet_ref in seen:
            continue
        seen.add(subnet_ref)

        assoc_ref = f"{subnet_ref}_nsg_assoc"
        lines.append(f'resource "azurerm_subnet_network_security_group_association" "{assoc_ref}" {{')
        lines.append(f'  subnet_id                 = azurerm_subnet.{subnet_ref}.id')
        lines.append('  network_security_group_id = azurerm_network_security_group.compute_unit.id')
        lines.append('}')
        lines.append('')

    return "\n".join(lines).strip() + "\n"


def _terraform_identifier(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", value.strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")

    if not normalized:
        return "default"
    if normalized[0].isdigit():
        normalized = f"r_{normalized}"

    return normalized.lower()


def _vnet_cidr(index: int) -> str:
    octet = 39 + index
    if octet > 250:
        octet = 250
    return f"10.{octet}.0.0/16"


def _subnet_cidr(switch_index: int, subnet_index: int) -> str:
    second_octet = 39 + switch_index
    if second_octet > 250:
        second_octet = 250

    third_octet = subnet_index
    if third_octet > 250:
        third_octet = 250

    return f"10.{second_octet}.{third_octet}.0/24"
