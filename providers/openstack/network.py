from __future__ import annotations

import re

from cim.schema import CanonicalInfrastructureModel, DistributedSwitch


def render_networking(cim: CanonicalInfrastructureModel) -> dict[str, str]:
    """Render OpenStack networking HCL files from NetworkTopology."""
    switches = list(cim.network_topology.distributed_switches)
    if not switches:
        switches = [DistributedSwitch(name="default-dvs", port_groups=[])]

    network_hcl = _render_networks_file(cim, switches)
    subnets_hcl = _render_subnets_file(switches)
    security_hcl = _render_security_groups_file(switches)

    return {
        "openstack-migration/networking/vpc.tf": network_hcl,
        "openstack-migration/networking/subnets.tf": subnets_hcl,
        "openstack-migration/networking/security_groups.tf": security_hcl,
    }


def _render_networks_file(cim: CanonicalInfrastructureModel, switches: list[DistributedSwitch]) -> str:
    lines: list[str] = []

    for switch in switches:
        network_ref = _terraform_identifier(switch.name)
        lines.append(f'resource "openstack_networking_network_v2" "{network_ref}" {{')
        lines.append(f'  name           = "{switch.name}"')
        lines.append('  admin_state_up = true')
        lines.append('  shared         = false')
        lines.append('')
        lines.append('  tags = [')
        lines.append('    "MigratedFrom=vmware-vcenter",')
        lines.append(f'    "SourceVCenter={cim.source_vcenter}",')
        lines.append('    "Environment=${var.environment}",')
        lines.append('    "Owner=${var.owner}"')
        lines.append('  ]')
        lines.append('}')
        lines.append('')

    return "\n".join(lines).strip() + "\n"


def _render_subnets_file(switches: list[DistributedSwitch]) -> str:
    lines: list[str] = []
    has_any_subnet = False

    for switch_index, switch in enumerate(switches, start=1):
        network_ref = _terraform_identifier(switch.name)
        port_groups = list(switch.port_groups)

        for subnet_index, port_group in enumerate(port_groups, start=1):
            has_any_subnet = True
            subnet_ref = _terraform_identifier(port_group.name)
            cidr = _subnet_cidr(switch_index, subnet_index)

            lines.append(f'resource "openstack_networking_subnet_v2" "{subnet_ref}" {{')
            lines.append(f'  name            = "{port_group.name}"')
            lines.append(f'  network_id      = openstack_networking_network_v2.{network_ref}.id')
            lines.append('  ip_version      = 4')
            lines.append(f'  cidr            = "{cidr}"')
            lines.append('  enable_dhcp     = true')
            lines.append('  dns_nameservers = ["8.8.8.8", "8.8.4.4"]')
            lines.append('')
            lines.append('  # VLAN tag from PortGroup is preserved in description for traceability.')
            lines.append(f'  description = "vlan_id={port_group.vlan_id}"')
            lines.append('}')
            lines.append('')

        if not port_groups:
            has_any_subnet = True
            lines.append('resource "openstack_networking_subnet_v2" "default" {')
            lines.append('  name            = "default"')
            lines.append(f'  network_id      = openstack_networking_network_v2.{network_ref}.id')
            lines.append('  ip_version      = 4')
            lines.append('  cidr            = "10.80.1.0/24"')
            lines.append('  enable_dhcp     = true')
            lines.append('  dns_nameservers = ["8.8.8.8", "8.8.4.4"]')
            lines.append('}')
            lines.append('')

    if not has_any_subnet:
        lines.append('resource "openstack_networking_subnet_v2" "default" {')
        lines.append('  name            = "default"')
        lines.append('  network_id      = openstack_networking_network_v2.default_dvs.id')
        lines.append('  ip_version      = 4')
        lines.append('  cidr            = "10.80.1.0/24"')
        lines.append('  enable_dhcp     = true')
        lines.append('  dns_nameservers = ["8.8.8.8", "8.8.4.4"]')
        lines.append('}')
        lines.append('')

    return "\n".join(lines).strip() + "\n"


def _render_security_groups_file(switches: list[DistributedSwitch]) -> str:
    lines: list[str] = []

    lines.append('resource "openstack_networking_secgroup_v2" "compute_unit" {')
    lines.append('  name        = "compute-unit-sg"')
    lines.append('  description = "Security group for migrated ComputeUnits"')
    lines.append('}')
    lines.append('')

    lines.append('resource "openstack_networking_secgroup_rule_v2" "allow_ssh_internal" {')
    lines.append('  direction         = "ingress"')
    lines.append('  ethertype         = "IPv4"')
    lines.append('  protocol          = "tcp"')
    lines.append('  port_range_min    = 22')
    lines.append('  port_range_max    = 22')
    lines.append('  remote_ip_prefix  = "10.0.0.0/8"')
    lines.append('  security_group_id = openstack_networking_secgroup_v2.compute_unit.id')
    lines.append('}')
    lines.append('')

    seen_networks: set[str] = set()
    for switch in switches:
        network_ref = _terraform_identifier(switch.name)
        if network_ref in seen_networks:
            continue
        seen_networks.add(network_ref)

        router_ref = f"{network_ref}_router"
        router_interface_ref = f"{network_ref}_router_if"
        subnet_ref = _first_subnet_ref_for_switch(switch)

        lines.append(f'resource "openstack_networking_router_v2" "{router_ref}" {{')
        lines.append(f'  name                = "{switch.name}-router"')
        lines.append('  admin_state_up      = true')
        lines.append('  external_network_id = var.external_network_id')
        lines.append('}')
        lines.append('')

        lines.append(
            f'resource "openstack_networking_router_interface_v2" "{router_interface_ref}" {{'
        )
        lines.append(f'  router_id = openstack_networking_router_v2.{router_ref}.id')
        lines.append(f'  subnet_id = openstack_networking_subnet_v2.{subnet_ref}.id')
        lines.append('}')
        lines.append('')

    return "\n".join(lines).strip() + "\n"


def _first_subnet_ref_for_switch(switch: DistributedSwitch) -> str:
    if switch.port_groups:
        return _terraform_identifier(switch.port_groups[0].name)
    return "default"


def _terraform_identifier(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", value.strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")

    if not normalized:
        return "default"
    if normalized[0].isdigit():
        normalized = f"r_{normalized}"

    return normalized.lower()


def _subnet_cidr(switch_index: int, subnet_index: int) -> str:
    second_octet = 79 + switch_index
    if second_octet > 250:
        second_octet = 250

    third_octet = subnet_index
    if third_octet > 250:
        third_octet = 250

    return f"10.{second_octet}.{third_octet}.0/24"
