from __future__ import annotations

from abc import ABC, abstractmethod

from cim.schema import CanonicalInfrastructureModel, ComputeUnit


class ProviderModule(ABC):
    """Abstract contract implemented by each cloud ProviderModule."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Canonical provider key: aws, azure, gcp, or openstack."""

    @abstractmethod
    def get_instance_type(self, vcpus: int, ram_mb: int) -> str:
        """Return provider instance type using deterministic sizing lookup."""

    @abstractmethod
    def render_networking(self, cim: CanonicalInfrastructureModel) -> dict[str, str]:
        """Render networking HCL files for NetworkTopology."""

    @abstractmethod
    def render_compute(self, cim: CanonicalInfrastructureModel) -> dict[str, str]:
        """Render one compute HCL file per ComputeUnit."""

    @abstractmethod
    def render_storage(self, cim: CanonicalInfrastructureModel) -> dict[str, str]:
        """Render storage HCL files for StorageVolumes."""

    @abstractmethod
    def render_placement(self, cim: CanonicalInfrastructureModel) -> dict[str, str]:
        """Render placement HCL files from ClusterSemantics."""

    def render_all(self, cim: CanonicalInfrastructureModel) -> dict[str, str]:
        """Compose all provider HCL outputs into a single path->content mapping."""
        output: dict[str, str] = {}
        output.update(self.render_networking(cim))
        output.update(self.render_compute(cim))
        output.update(self.render_storage(cim))
        output.update(self.render_placement(cim))
        return output

    @staticmethod
    def compute_file_name(compute_unit: ComputeUnit) -> str:
        return f"{compute_unit.name}.tf"

    @staticmethod
    def storage_file_name(compute_unit: ComputeUnit) -> str:
        return f"{compute_unit.name}_disks.tf"
