from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TargetProvider(str, Enum):
    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    OPENSTACK = "openstack"


class MigrationStatus(str, Enum):
    READY = "ready"
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"


class ClusterSemantics(str, Enum):
    HA = "ha"
    ANTI_AFFINITY = "anti_affinity"
    AFFINITY = "affinity"
    LB = "lb"
    UNKNOWN = "unknown"


class PortGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str
    vlan_id: str


class DistributedSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str
    port_groups: list[PortGroup] = Field(default_factory=list)


class NetworkTopology(BaseModel):
    model_config = ConfigDict(extra="forbid")

    distributed_switches: list[DistributedSwitch] = Field(default_factory=list)


class NIC(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str
    port_group_ref: str
    mac_address: str | None = None


class StorageVolume(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str
    size_gb: int = Field(ge=1)
    datastore: str | None = None
    thin_provisioned: bool | None = None
    is_encrypted: bool = False
    is_shared: bool = False
    is_rdm: bool = False


class ComputeCluster(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str
    semantics: ClusterSemantics = ClusterSemantics.UNKNOWN


class ComputeUnit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str
    name: str
    vcpus: int = Field(ge=1)
    ram_mb: int = Field(ge=1)
    migration_status: MigrationStatus = MigrationStatus.READY
    blockers: list[str] = Field(default_factory=list)
    cluster_ref: str | None = None
    is_encrypted: bool = False
    has_vtpm: bool = False
    nics: list[NIC] = Field(default_factory=list)
    storage_volumes: list[StorageVolume] = Field(default_factory=list)


class CanonicalInfrastructureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    cim_schema_version: Literal["1.0"] = "1.0"
    source_vcenter: str
    target_provider: TargetProvider
    network_topology: NetworkTopology
    clusters: list[ComputeCluster] = Field(default_factory=list)
    compute_units: list[ComputeUnit] = Field(default_factory=list)
