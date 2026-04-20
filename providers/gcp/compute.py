from __future__ import annotations

import re

from cim.schema import CanonicalInfrastructureModel, ComputeUnit
from providers.gcp.sizing_table import get_instance_type


def render_compute(
    cim: CanonicalInfrastructureModel,
    required_tags: dict[str, str] | None = None,
) -> dict[str, str]:
    """Render one GCP compute HCL file per ComputeUnit."""
    labels = _build_required_labels(cim.source_vcenter, required_tags)

    output: dict[str, str] = {}
    for compute_unit in cim.compute_units:
        path = f"gcp-migration/compute/{compute_unit.name}.tf"
        output[path] = _render_compute_unit(compute_unit, labels)

    return output


def _render_compute_unit(compute_unit: ComputeUnit, labels: dict[str, str]) -> str:
    resource_name = _terraform_identifier(compute_unit.name)
    machine_type = get_instance_type(compute_unit.vcpus, compute_unit.ram_mb)
    subnet_ref = _subnet_ref(compute_unit)

    lines: list[str] = []
    lines.append(f'resource "google_compute_instance" "{resource_name}" {{')
    lines.append(f'  name         = "{compute_unit.name}"')
    lines.append('  project      = var.project_id')
    lines.append('  zone         = var.zone')
    lines.append(f'  machine_type = "{machine_type}"')
    lines.append('')
    lines.append('  boot_disk {')
    lines.append('    initialize_params {')
    lines.append('      image = var.default_image')
    lines.append('      size  = 20')
    lines.append('      type  = "pd-balanced"')
    lines.append('    }')
    lines.append('  }')
    lines.append('')
    lines.append('  network_interface {')
    lines.append(f'    subnetwork = google_compute_subnetwork.{subnet_ref}.id')
    lines.append('  }')

    if compute_unit.cluster_ref:
        lines.append('')
        lines.append('  # ClusterSemantics placement should be applied via resource policies.')

    if compute_unit.has_vtpm:
        lines.append('')
        lines.append('  # TODO: ComputeUnit has vTPM and needs manual review.')

    lines.append('')
    lines.append('  labels = {')
    lines.append(f'    name = "{_label_value(compute_unit.name)}"')
    for key, value in labels.items():
        lines.append(f'    {key} = "{_label_value(value)}"')
    lines.append('  }')
    lines.append('}')

    return "\n".join(lines)


def _build_required_labels(
    source_vcenter: str,
    required_tags: dict[str, str] | None,
) -> dict[str, str]:
    defaults = {
        "environment": "${var.environment}",
        "owner": "${var.owner}",
        "migrated_from": "vmware-vcenter",
        "source_vcenter": source_vcenter,
    }

    if not required_tags:
        return defaults

    merged = dict(defaults)
    for key, value in required_tags.items():
        normalized_key = _label_key(str(key))
        merged[normalized_key] = str(value)

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


def _label_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]", "_", value.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")

    if not normalized:
        return "label"
    if normalized[0].isdigit():
        normalized = f"k_{normalized}"

    if len(normalized) > 63:
        return normalized[:63]

    return normalized


def _label_value(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", str(value).strip())
    if len(normalized) > 63:
        return normalized[:63]
    return normalized
