from __future__ import annotations

import re

from cim.schema import CanonicalInfrastructureModel, DistributedSwitch


def render_networking(cim: CanonicalInfrastructureModel) -> dict[str, str]:
    """Render GCP networking HCL files from NetworkTopology."""
    switches = list(cim.network_topology.distributed_switches)
    if not switches:
        switches = [DistributedSwitch(name="default-dvs", port_groups=[])]

    vpc_hcl = _render_vpc_file(cim, switches)
    subnets_hcl = _render_subnets_file(switches)
    security_hcl = _render_security_file(switches)

    return {
        "gcp-migration/networking/vpc.tf": vpc_hcl,
        "gcp-migration/networking/subnets.tf": subnets_hcl,
        "gcp-migration/networking/security_groups.tf": security_hcl,
    }


def _render_vpc_file(cim: CanonicalInfrastructureModel, switches: list[DistributedSwitch]) -> str:
    lines: list[str] = []

    for switch in switches:
        network_ref = _terraform_identifier(switch.name)

        lines.append(f'resource "google_compute_network" "{network_ref}" {{')
        lines.append(f'  name                    = "{_network_name(switch.name)}"')
        lines.append('  project                 = var.project_id')
        lines.append('  auto_create_subnetworks = false')
        lines.append('  routing_mode            = "REGIONAL"')
        lines.append('}')
        lines.append('')

        lines.append(f'resource "google_compute_network_peering_routes_config" "{network_ref}_routes" {{')
        lines.append(f'  network              = google_compute_network.{network_ref}.name')
        lines.append('  peering              = var.peering_name')
        lines.append('  import_custom_routes = true')
        lines.append('  export_custom_routes = true')
        lines.append('}')
        lines.append('')

    lines.append('locals {')
    lines.append(f'  source_vcenter = "{cim.source_vcenter}"')
    lines.append('}')

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

            lines.append(f'resource "google_compute_subnetwork" "{subnet_ref}" {{')
            lines.append(f'  name          = "{_subnet_name(port_group.name)}"')
            lines.append('  project       = var.project_id')
            lines.append('  region        = var.region')
            lines.append(f'  network       = google_compute_network.{network_ref}.id')
            lines.append(f'  ip_cidr_range = "{cidr}"')
            lines.append('  private_ip_google_access = true')
            lines.append('')
            lines.append('  description = "Migrated from VMware PortGroup; vlan_id=' + port_group.vlan_id + '"')
            lines.append('}')
            lines.append('')

        if not port_groups:
            has_any_subnet = True
            lines.append('resource "google_compute_subnetwork" "default" {')
            lines.append('  name          = "default"')
            lines.append('  project       = var.project_id')
            lines.append('  region        = var.region')
            lines.append(f'  network       = google_compute_network.{network_ref}.id')
            lines.append('  ip_cidr_range = "10.60.1.0/24"')
            lines.append('  private_ip_google_access = true')
            lines.append('}')
            lines.append('')

    if not has_any_subnet:
        lines.append('resource "google_compute_subnetwork" "default" {')
        lines.append('  name          = "default"')
        lines.append('  project       = var.project_id')
        lines.append('  region        = var.region')
        lines.append('  network       = google_compute_network.default_dvs.id')
        lines.append('  ip_cidr_range = "10.60.1.0/24"')
        lines.append('  private_ip_google_access = true')
        lines.append('}')
        lines.append('')

    return "\n".join(lines).strip() + "\n"


def _render_security_file(switches: list[DistributedSwitch]) -> str:
    lines: list[str] = []

    seen_networks: set[str] = set()
    for switch in switches:
        network_ref = _terraform_identifier(switch.name)
        if network_ref in seen_networks:
            continue
        seen_networks.add(network_ref)

        lines.append(f'resource "google_compute_firewall" "{network_ref}_allow_ssh_internal" {{')
        lines.append(f'  name    = "{network_ref}-allow-ssh-internal"')
        lines.append('  project = var.project_id')
        lines.append(f'  network = google_compute_network.{network_ref}.name')
        lines.append('  direction = "INGRESS"')
        lines.append('  source_ranges = ["10.0.0.0/8"]')
        lines.append('')
        lines.append('  allow {')
        lines.append('    protocol = "tcp"')
        lines.append('    ports    = ["22"]')
        lines.append('  }')
        lines.append('}')
        lines.append('')

        lines.append(f'resource "google_compute_firewall" "{network_ref}_egress_internal" {{')
        lines.append(f'  name    = "{network_ref}-egress-internal"')
        lines.append('  project = var.project_id')
        lines.append(f'  network = google_compute_network.{network_ref}.name')
        lines.append('  direction = "EGRESS"')
        lines.append('  destination_ranges = ["10.0.0.0/8"]')
        lines.append('')
        lines.append('  allow {')
        lines.append('    protocol = "all"')
        lines.append('  }')
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


def _network_name(value: str) -> str:
    # GCP network names must match [a-z]([-a-z0-9]*[a-z0-9])?
    normalized = re.sub(r"[^a-z0-9-]", "-", value.strip().lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")

    if not normalized:
        return "default-network"
    if not normalized[0].isalpha():
        normalized = f"n-{normalized}"

    return normalized[:63]


def _subnet_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]", "-", value.strip().lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")

    if not normalized:
        return "default-subnet"
    if not normalized[0].isalpha():
        normalized = f"s-{normalized}"

    return normalized[:63]


def _subnet_cidr(switch_index: int, subnet_index: int) -> str:
    second_octet = 59 + switch_index
    if second_octet > 250:
        second_octet = 250

    third_octet = subnet_index
    if third_octet > 250:
        third_octet = 250

    return f"10.{second_octet}.{third_octet}.0/24"
