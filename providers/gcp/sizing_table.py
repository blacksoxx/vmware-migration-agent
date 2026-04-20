from __future__ import annotations

from bisect import bisect_left

# Deterministic sizing lookup table: vCPU -> sorted list of (ram_mb, machine_type)
_GCP_SIZING_TABLE: dict[int, list[tuple[int, str]]] = {
    1: [
        (1024, "e2-micro"),
        (2048, "e2-small"),
    ],
    2: [
        (4096, "e2-medium"),
        (8192, "e2-standard-2"),
        (16384, "n2-standard-2"),
    ],
    4: [
        (8192, "e2-standard-4"),
        (16384, "n2-standard-4"),
        (32768, "n2-highmem-4"),
    ],
    8: [
        (16384, "e2-standard-8"),
        (32768, "n2-standard-8"),
        (65536, "n2-highmem-8"),
    ],
    16: [
        (32768, "n2-standard-16"),
        (65536, "n2-standard-16"),
        (131072, "n2-highmem-16"),
    ],
}


def get_instance_type(vcpus: int, ram_mb: int) -> str:
    """Return GCP machine type using exact-vCPU and closest-ceiling RAM lookup."""
    if vcpus <= 0:
        raise ValueError("vcpus must be > 0")
    if ram_mb <= 0:
        raise ValueError("ram_mb must be > 0")

    selected_vcpus = _select_vcpu_bucket(vcpus)
    options = _GCP_SIZING_TABLE[selected_vcpus]

    ram_values = [entry[0] for entry in options]
    index = bisect_left(ram_values, ram_mb)

    if index < len(options):
        return options[index][1]

    return options[-1][1]


def _select_vcpu_bucket(requested_vcpus: int) -> int:
    if requested_vcpus in _GCP_SIZING_TABLE:
        return requested_vcpus

    available = sorted(_GCP_SIZING_TABLE)
    for candidate in available:
        if candidate >= requested_vcpus:
            return candidate

    return available[-1]
