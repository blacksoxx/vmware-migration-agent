from __future__ import annotations

import re

from cim.schema import CanonicalInfrastructureModel, ComputeUnit, StorageVolume


def render_storage(
    cim: CanonicalInfrastructureModel,
    required_tags: dict[str, str] | None = None,
) -> dict[str, str]:
    """Render GCP storage HCL files for ComputeUnit StorageVolume resources."""
    labels = _build_required_labels(cim.source_vcenter, required_tags)

    output: dict[str, str] = {}
    for compute_unit in cim.compute_units:
        path = f"gcp-migration/storage/{compute_unit.name}_disks.tf"
        output[path] = _render_compute_unit_storage(compute_unit, labels)

    return output


def _render_compute_unit_storage(compute_unit: ComputeUnit, labels: dict[str, str]) -> str:
    vm_ref = _terraform_identifier(compute_unit.name)
    lines: list[str] = []

    volumes = list(compute_unit.storage_volumes)
    if not volumes:
        lines.append(f"# ComputeUnit {compute_unit.name} has no StorageVolume entries.")
        return "\n".join(lines) + "\n"

    for index, volume in enumerate(volumes, start=1):
        disk_ref = _disk_ref(compute_unit, volume, index)
        attach_ref = f"{disk_ref}_attach"

        lines.extend(_render_persistent_disk(disk_ref, volume, compute_unit, labels))
        lines.append("")

        lines.append(f'resource "google_compute_attached_disk" "{attach_ref}" {{')
        lines.append(f'  disk     = google_compute_disk.{disk_ref}.id')
        lines.append(f'  instance = google_compute_instance.{vm_ref}.self_link')
        lines.append('  zone     = var.zone')
        lines.append('}')
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _render_persistent_disk(
    disk_ref: str,
    volume: StorageVolume,
    compute_unit: ComputeUnit,
    labels: dict[str, str],
) -> list[str]:
    lines: list[str] = []

    lines.append(f'resource "google_compute_disk" "{disk_ref}" {{')
    lines.append(f'  name    = "{_disk_name(disk_ref)}"')
    lines.append('  project = var.project_id')
    lines.append('  zone    = var.zone')
    lines.append(f'  size    = {int(volume.size_gb)}')
    lines.append('  type    = "pd-balanced"')
    lines.append('')

    # Enforce encryption-at-rest for all generated GCP disks.
    lines.append('  disk_encryption_key {')
    lines.append('    kms_key_self_link = var.default_kms_key_self_link')
    lines.append('  }')

    if volume.is_shared:
        lines.append('')
        lines.append('  # TODO: StorageVolume marked shared; validate multi-writer semantics manually.')

    if volume.is_rdm:
        lines.append('')
        lines.append('  # TODO: StorageVolume marked RDM-derived; manual validation required.')

    lines.append('')
    lines.append('  labels = {')
    lines.append(f'    name = "{_label_value(compute_unit.name)}"')
    lines.append(f'    datastore = "{_label_value((volume.datastore or ""))}"')
    lines.append('    storage_encrypted = "true"')
    for key, value in labels.items():
        lines.append(f'    {key} = "{_label_value(value)}"')
    lines.append('  }')
    lines.append('}')

    return lines


def _disk_ref(compute_unit: ComputeUnit, volume: StorageVolume, index: int) -> str:
    parts = [compute_unit.name, volume.id, str(index)]
    return _terraform_identifier("_".join(part for part in parts if part))


def _disk_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]", "-", value.strip().lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")

    if not normalized:
        return "disk"
    if not normalized[0].isalpha():
        normalized = f"d-{normalized}"

    return normalized[:63]


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
        merged[_label_key(str(key))] = str(value)

    return merged


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

    return normalized[:63]


def _label_value(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", str(value).strip())
    if len(normalized) > 63:
        return normalized[:63]
    return normalized
