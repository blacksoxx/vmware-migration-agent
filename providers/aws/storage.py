from __future__ import annotations

import re

from cim.schema import CanonicalInfrastructureModel, ComputeUnit, StorageVolume


def render_storage(
    cim: CanonicalInfrastructureModel,
    required_tags: dict[str, str] | None = None,
) -> dict[str, str]:
    """Render AWS storage HCL files for ComputeUnit StorageVolume resources."""
    tags = _build_required_tags(cim.source_vcenter, required_tags)

    output: dict[str, str] = {}
    for compute_unit in cim.compute_units:
        path = f"aws-migration/storage/{compute_unit.name}_disks.tf"
        output[path] = _render_compute_unit_storage(compute_unit, tags)

    return output


def _render_compute_unit_storage(compute_unit: ComputeUnit, tags: dict[str, str]) -> str:
    instance_ref = _terraform_identifier(compute_unit.name)

    lines: list[str] = []
    volumes = list(compute_unit.storage_volumes)

    if not volumes:
        lines.append(f"# ComputeUnit {compute_unit.name} has no StorageVolume entries.")
        return "\n".join(lines) + "\n"

    for index, volume in enumerate(volumes, start=1):
        volume_ref = _volume_ref(compute_unit, volume, index)

        lines.extend(_render_ebs_volume(volume_ref, volume, compute_unit, tags))
        lines.append("")
        lines.extend(_render_volume_attachment(volume_ref, instance_ref, index))
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _render_ebs_volume(
    volume_ref: str,
    volume: StorageVolume,
    compute_unit: ComputeUnit,
    tags: dict[str, str],
) -> list[str]:
    lines: list[str] = []

    # Always enforce encryption-at-rest for generated storage resources.
    lines.append(f'resource "aws_ebs_volume" "{volume_ref}" {{')
    lines.append('  availability_zone = var.default_availability_zone')
    lines.append(f"  size              = {int(volume.size_gb)}")
    lines.append('  encrypted         = true')

    if volume.thin_provisioned is not None:
        volume_type = "gp3" if volume.thin_provisioned else "gp2"
        lines.append(f'  type              = "{volume_type}"')

    if volume.is_shared:
        lines.append('  # TODO: StorageVolume marked shared; validate multi-attach requirements manually.')

    if volume.is_rdm:
        lines.append('  # TODO: StorageVolume marked RDM-derived; manual validation required.')

    lines.append('')
    lines.append('  tags = {')
    lines.append(f'    Name = "{compute_unit.name}-{volume_ref}"')
    lines.append(f'    Datastore = "{(volume.datastore or "").strip()}"')
    lines.append('    StorageEncrypted = "true"')
    for key, value in tags.items():
        lines.append(f'    {key} = "{value}"')
    lines.append('  }')
    lines.append('}')

    return lines


def _render_volume_attachment(volume_ref: str, instance_ref: str, index: int) -> list[str]:
    device_name = _device_name(index)

    return [
        f'resource "aws_volume_attachment" "{volume_ref}_attach" {{',
        f"  device_name = \"{device_name}\"",
        f"  volume_id   = aws_ebs_volume.{volume_ref}.id",
        f"  instance_id = aws_instance.{instance_ref}.id",
        "}",
    ]


def _device_name(index: int) -> str:
    # Keep deterministic Linux device ordering starting at /dev/sdf.
    base_letter_ord = ord("f") + max(index - 1, 0)
    if base_letter_ord > ord("z"):
        base_letter_ord = ord("z")
    return f"/dev/sd{chr(base_letter_ord)}"


def _volume_ref(compute_unit: ComputeUnit, volume: StorageVolume, index: int) -> str:
    parts = [compute_unit.name, volume.id, str(index)]
    joined = "_".join(part for part in parts if part)
    return _terraform_identifier(joined)


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
