from __future__ import annotations

import re

from cim.schema import CanonicalInfrastructureModel, ComputeUnit
from providers.openstack.sizing_table import get_instance_type


def render_compute(
    cim: CanonicalInfrastructureModel,
    required_tags: dict[str, str] | None = None,
) -> dict[str, str]:
    """Render one OpenStack compute HCL file per ComputeUnit."""
    metadata = _build_required_metadata(cim.source_vcenter, required_tags)

    output: dict[str, str] = {}
    for compute_unit in cim.compute_units:
        path = f"openstack-migration/compute/{compute_unit.name}.tf"
        output[path] = _render_compute_unit(compute_unit, metadata)

    return output


def _render_compute_unit(compute_unit: ComputeUnit, metadata: dict[str, str]) -> str:
    resource_name = _terraform_identifier(compute_unit.name)
    flavor_name = get_instance_type(compute_unit.vcpus, compute_unit.ram_mb)
    network_ref = _network_ref(compute_unit)

    lines: list[str] = []
    lines.append(f'resource "openstack_compute_instance_v2" "{resource_name}" {{')
    lines.append(f'  name            = "{compute_unit.name}"')
    lines.append(f'  flavor_name     = "{flavor_name}"')
    lines.append('  image_name      = var.default_image_name')
    lines.append('  key_pair        = var.key_pair_name')
    lines.append('  security_groups = [openstack_networking_secgroup_v2.compute_unit.name]')
    lines.append('')
    lines.append('  network {')
    lines.append(f'    name = openstack_networking_network_v2.{network_ref}.name')
    lines.append('  }')

    if compute_unit.cluster_ref:
        lines.append('')
        lines.append('  # ClusterSemantics placement should be applied using server groups as needed.')

    if compute_unit.has_vtpm:
        lines.append('')
        lines.append('  # TODO: ComputeUnit has vTPM and needs manual review.')

    lines.append('')
    lines.append('  metadata = {')
    lines.append(f'    Name = "{compute_unit.name}"')
    for key, value in metadata.items():
        lines.append(f'    {key} = "{value}"')
    lines.append('  }')
    lines.append('}')

    return "\n".join(lines)


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


def _network_ref(compute_unit: ComputeUnit) -> str:
    if compute_unit.nics:
        return _terraform_identifier(compute_unit.nics[0].port_group_ref)
    return "default_dvs"


def _terraform_identifier(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", value.strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")

    if not normalized:
        return "resource"
    if normalized[0].isdigit():
        normalized = f"r_{normalized}"

    return normalized.lower()
