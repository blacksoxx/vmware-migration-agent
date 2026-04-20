from __future__ import annotations

import re

from cim.schema import CanonicalInfrastructureModel, DistributedSwitch


def render_networking(cim: CanonicalInfrastructureModel) -> dict[str, str]:
    """Render AWS networking HCL files from NetworkTopology."""
    switches = list(cim.network_topology.distributed_switches)
    if not switches:
        switches = [DistributedSwitch(name="default-dvs", port_groups=[])]

    vpc_hcl = _render_vpc_file(cim, switches)
    subnets_hcl = _render_subnets_file(switches)
    security_groups_hcl = _render_security_groups_file(switches)

    return {
        "aws-migration/networking/vpc.tf": vpc_hcl,
        "aws-migration/networking/subnets.tf": subnets_hcl,
        "aws-migration/networking/security_groups.tf": security_groups_hcl,
    }


def _render_vpc_file(cim: CanonicalInfrastructureModel, switches: list[DistributedSwitch]) -> str:
    lines: list[str] = []

    for switch_index, switch in enumerate(switches, start=1):
        vpc_name = _terraform_identifier(switch.name)
        cidr = _vpc_cidr(switch_index)

        lines.append(f'resource "aws_vpc" "{vpc_name}" {{')
        lines.append(f'  cidr_block = "{cidr}"')
        lines.append('  enable_dns_support = true')
        lines.append('  enable_dns_hostnames = true')
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
        switch_name = _terraform_identifier(switch.name)
        port_groups = list(switch.port_groups)

        if not port_groups:
            port_groups = []

        for subnet_index, port_group in enumerate(port_groups, start=1):
            has_any_subnet = True
            subnet_name = _terraform_identifier(port_group.name)
            cidr = _subnet_cidr(switch_index, subnet_index)

            lines.append(f'resource "aws_subnet" "{subnet_name}" {{')
            lines.append(f'  vpc_id     = aws_vpc.{switch_name}.id')
            lines.append(f'  cidr_block = "{cidr}"')
            lines.append('')
            lines.append('  tags = {')
            lines.append(f'    Name = "{port_group.name}"')
            lines.append(f'    VlanId = "{port_group.vlan_id}"')
            lines.append('    Environment = "${var.environment}"')
            lines.append('    Owner = "${var.owner}"')
            lines.append('    MigratedFrom = "vmware-vcenter"')
            lines.append('  }')
            lines.append('}')
            lines.append('')

    if not has_any_subnet:
        default_switch_name = _terraform_identifier(switches[0].name)
        lines.append('resource "aws_subnet" "default" {')
        lines.append(f'  vpc_id     = aws_vpc.{default_switch_name}.id')
        lines.append('  cidr_block = "10.10.1.0/24"')
        lines.append('')
        lines.append('  tags = {')
        lines.append('    Name = "default"')
        lines.append('    VlanId = ""')
        lines.append('    Environment = "${var.environment}"')
        lines.append('    Owner = "${var.owner}"')
        lines.append('    MigratedFrom = "vmware-vcenter"')
        lines.append('  }')
        lines.append('}')
        lines.append('')

    return "\n".join(lines).strip() + "\n"


def _render_security_groups_file(switches: list[DistributedSwitch]) -> str:
    default_vpc_ref = _terraform_identifier(switches[0].name)
    lines = [
        'resource "aws_security_group" "compute_unit" {',
        '  name        = "compute-unit-sg"',
        '  description = "Security group for migrated ComputeUnit resources"',
        f'  vpc_id      = aws_vpc.{default_vpc_ref}.id',
        '',
        '  ingress {',
        '    description = "Allow SSH from VPC range only"',
        '    from_port   = 22',
        '    to_port     = 22',
        '    protocol    = "tcp"',
        '    cidr_blocks = ["10.0.0.0/8"]',
        '  }',
        '',
        '  egress {',
        '    from_port   = 0',
        '    to_port     = 0',
        '    protocol    = "-1"',
        '    cidr_blocks = ["10.0.0.0/8"]',
        '  }',
        '',
        '  tags = {',
        '    Name = "compute-unit-sg"',
        '    Environment = "${var.environment}"',
        '    Owner = "${var.owner}"',
        '    MigratedFrom = "vmware-vcenter"',
        '  }',
        '}',
        '',
    ]
    return "\n".join(lines)


def _terraform_identifier(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", value.strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")

    if not normalized:
        return "default"
    if normalized[0].isdigit():
        normalized = f"r_{normalized}"

    return normalized.lower()


def _vpc_cidr(index: int) -> str:
    octet = 9 + index
    if octet > 250:
        octet = 250
    return f"10.{octet}.0.0/16"


def _subnet_cidr(switch_index: int, subnet_index: int) -> str:
    second_octet = 9 + switch_index
    if second_octet > 250:
        second_octet = 250

    third_octet = subnet_index
    if third_octet > 250:
        third_octet = 250

    return f"10.{second_octet}.{third_octet}.0/24"
