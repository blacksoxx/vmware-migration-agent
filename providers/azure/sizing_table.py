from __future__ import annotations

from bisect import bisect_left

# Deterministic sizing lookup table: vCPU -> sorted list of (ram_mb, sku)
_AZURE_SIZING_TABLE: dict[int, list[tuple[int, str]]] = {
    1: [
        (1024, "Standard_B1s"),
        (2048, "Standard_B1ms"),
    ],
    2: [
        (4096, "Standard_B2s"),
        (8192, "Standard_D2s_v5"),
        (16384, "Standard_D2as_v5"),
    ],
    4: [
        (8192, "Standard_D4s_v5"),
        (16384, "Standard_D4s_v5"),
        (32768, "Standard_D4as_v5"),
    ],
    8: [
        (16384, "Standard_D8s_v5"),
        (32768, "Standard_D8s_v5"),
        (65536, "Standard_D8as_v5"),
    ],
    16: [
        (32768, "Standard_D16s_v5"),
        (65536, "Standard_D16s_v5"),
        (131072, "Standard_D16as_v5"),
    ],
}


def get_instance_type(vcpus: int, ram_mb: int) -> str:
    """Return Azure VM size using exact-vCPU and closest-ceiling RAM lookup."""
    if vcpus <= 0:
        raise ValueError("vcpus must be > 0")
    if ram_mb <= 0:
        raise ValueError("ram_mb must be > 0")

    selected_vcpus = _select_vcpu_bucket(vcpus)
    options = _AZURE_SIZING_TABLE[selected_vcpus]

    ram_values = [entry[0] for entry in options]
    index = bisect_left(ram_values, ram_mb)

    if index < len(options):
        return options[index][1]

    return options[-1][1]


def _select_vcpu_bucket(requested_vcpus: int) -> int:
    if requested_vcpus in _AZURE_SIZING_TABLE:
        return requested_vcpus

    available = sorted(_AZURE_SIZING_TABLE)
    for candidate in available:
        if candidate >= requested_vcpus:
            return candidate

    return available[-1]
