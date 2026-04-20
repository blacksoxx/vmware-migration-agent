from __future__ import annotations

from bisect import bisect_left

# Deterministic sizing lookup table: vCPU -> sorted list of (ram_mb, instance_type)
_AWS_SIZING_TABLE: dict[int, list[tuple[int, str]]] = {
    1: [
        (1024, "t3.micro"),
        (2048, "t3.small"),
    ],
    2: [
        (4096, "t3.medium"),
        (8192, "t3.large"),
        (16384, "m5.large"),
    ],
    4: [
        (8192, "m5.xlarge"),
        (16384, "m5.xlarge"),
        (32768, "m5.2xlarge"),
    ],
    8: [
        (16384, "m5.2xlarge"),
        (32768, "m5.2xlarge"),
        (65536, "m5.4xlarge"),
    ],
    16: [
        (32768, "m5.4xlarge"),
        (65536, "m5.4xlarge"),
        (131072, "m5.8xlarge"),
    ],
}


def get_instance_type(vcpus: int, ram_mb: int) -> str:
    """Return AWS instance type using exact-vCPU and closest-ceiling RAM lookup."""
    if vcpus <= 0:
        raise ValueError("vcpus must be > 0")
    if ram_mb <= 0:
        raise ValueError("ram_mb must be > 0")

    selected_vcpus = _select_vcpu_bucket(vcpus)
    options = _AWS_SIZING_TABLE[selected_vcpus]

    ram_values = [entry[0] for entry in options]
    index = bisect_left(ram_values, ram_mb)

    if index < len(options):
        return options[index][1]

    # If requested RAM exceeds the largest entry, use the largest known shape in bucket.
    return options[-1][1]


def _select_vcpu_bucket(requested_vcpus: int) -> int:
    if requested_vcpus in _AWS_SIZING_TABLE:
        return requested_vcpus

    available = sorted(_AWS_SIZING_TABLE)

    # Prefer the next larger vCPU bucket; fallback to the largest available bucket.
    for candidate in available:
        if candidate >= requested_vcpus:
            return candidate

    return available[-1]
