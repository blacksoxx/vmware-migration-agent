from __future__ import annotations

from bisect import bisect_left

# Deterministic sizing lookup table: vCPU -> sorted list of (ram_mb, flavor)
_OPENSTACK_SIZING_TABLE: dict[int, list[tuple[int, str]]] = {
    1: [
        (1024, "m1.tiny"),
        (2048, "m1.small"),
    ],
    2: [
        (4096, "m1.small"),
        (8192, "m1.medium"),
        (16384, "m1.large"),
    ],
    4: [
        (8192, "m1.medium"),
        (16384, "m1.large"),
        (32768, "m1.xlarge"),
    ],
    8: [
        (16384, "m1.large"),
        (32768, "m1.xlarge"),
        (65536, "m1.2xlarge"),
    ],
    16: [
        (32768, "m1.xlarge"),
        (65536, "m1.2xlarge"),
        (131072, "m1.4xlarge"),
    ],
}


def get_instance_type(vcpus: int, ram_mb: int) -> str:
    """Return OpenStack flavor using exact-vCPU and closest-ceiling RAM lookup."""
    if vcpus <= 0:
        raise ValueError("vcpus must be > 0")
    if ram_mb <= 0:
        raise ValueError("ram_mb must be > 0")

    selected_vcpus = _select_vcpu_bucket(vcpus)
    options = _OPENSTACK_SIZING_TABLE[selected_vcpus]

    ram_values = [entry[0] for entry in options]
    index = bisect_left(ram_values, ram_mb)

    if index < len(options):
        return options[index][1]

    return options[-1][1]


def _select_vcpu_bucket(requested_vcpus: int) -> int:
    if requested_vcpus in _OPENSTACK_SIZING_TABLE:
        return requested_vcpus

    available = sorted(_OPENSTACK_SIZING_TABLE)
    for candidate in available:
        if candidate >= requested_vcpus:
            return candidate

    return available[-1]
