from __future__ import annotations

import re

from cim.schema import CanonicalInfrastructureModel, ComputeUnit
from providers.aws.sizing_table import get_instance_type


def render_compute(
    cim: CanonicalInfrastructureModel,
    required_tags: dict[str, str] | None = None,
) -> dict[str, str]:
    """Render one AWS compute HCL file per ComputeUnit."""
    tags = _build_required_tags(cim.source_vcenter, required_tags)

    output: dict[str, str] = {}
    for compute_unit in cim.compute_units:
        path = f"aws-migration/compute/{compute_unit.name}.tf"
        output[path] = _render_compute_unit(compute_unit, tags)

    return output


def _render_compute_unit(compute_unit: ComputeUnit, tags: dict[str, str]) -> str:
    resource_name = _terraform_identifier(compute_unit.name)
    instance_type = get_instance_type(compute_unit.vcpus, compute_unit.ram_mb)

    lines: list[str] = []
    lines.append(f'resource "aws_instance" "{resource_name}" {{')
    lines.append('  ami           = var.default_ami_id')
    lines.append(f'  instance_type = "{instance_type}"')
    lines.append(f'  subnet_id     = aws_subnet.{_subnet_ref(compute_unit)}.id')
    lines.append('  vpc_security_group_ids = [aws_security_group.compute_unit.id]')

    if compute_unit.cluster_ref:
        lines.append(
            f'  placement_group = aws_placement_group.{_terraform_identifier(compute_unit.cluster_ref)}.name'
        )

    if compute_unit.has_vtpm:
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
