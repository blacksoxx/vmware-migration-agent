from __future__ import annotations

import json

import pytest

import agent.nodes.hcl_generator as hcl_module
from cim.schema import (
    CanonicalInfrastructureModel,
    ComputeUnit,
    DistributedSwitch,
    MigrationStatus,
    NIC,
    NetworkTopology,
    PortGroup,
    TargetProvider,
)


class _FakeLLMClient:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config

    def generate_hcl(self, prompt: str, system_prompt: str | None = None) -> str:
        payload = {
            "aws-migration/networking/vpc.tf": 'resource "aws_vpc" "main" { cidr_block = "10.0.0.0/16" }\nresource "aws_subnet" "main" { vpc_id = aws_vpc.main.id cidr_block = "10.0.1.0/24" }\nresource "aws_security_group" "main" { name = "main" vpc_id = aws_vpc.main.id }',
            "aws-migration/compute/app.tf": 'resource "aws_instance" "app" { ami = "ami-123" instance_type = "t3.medium" subnet_id = aws_subnet.main.id vpc_security_group_ids = [aws_security_group.main.id] }',
        }
        return json.dumps(payload)


def _sample_cim() -> CanonicalInfrastructureModel:
    return CanonicalInfrastructureModel(
        source_vcenter="vc01.local",
        target_provider=TargetProvider.AWS,
        network_topology=NetworkTopology(
            distributed_switches=[
                DistributedSwitch(
                    name="dvs-main",
                    port_groups=[PortGroup(name="pg-app", vlan_id="100")],
                )
            ]
        ),
        compute_units=[
            ComputeUnit(
                id="cu-1",
                name="app-1",
                vcpus=2,
                ram_mb=4096,
                migration_status=MigrationStatus.READY,
            )
        ],
    )


def _empty_cim() -> CanonicalInfrastructureModel:
    return CanonicalInfrastructureModel(
        source_vcenter="vc01.local",
        target_provider=TargetProvider.AWS,
        network_topology=NetworkTopology(distributed_switches=[]),
        compute_units=[],
    )


def _base_state() -> dict[str, object]:
    return {
        "sized_cim": _sample_cim(),
        "config": {
            "pipeline": {"max_retries": 2},
            "validation": {"run_tflint": False, "run_opa": False},
            "required_tags": {
                "Environment": "dev",
                "Owner": "platform",
                "MigratedFrom": "vmware-vcenter",
            },
        },
        "mcp_context": "provider docs",
        "validation_result": {},
        "messages": [],
        "retry_count": 0,
        "status": "pending",
    }


def test_hcl_generator_retries_until_deterministic_validation_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"count": 0}

    def _fake_validate(config: dict[str, object], hcl_output: dict[str, str]) -> dict[str, object]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return {
                "passed": False,
                "terraform_validate_passed": False,
                "tflint_passed": True,
                "opa_passed": True,
                "errors": ["terraform validate failed"],
                "warnings": [],
            }

        return {
            "passed": True,
            "terraform_validate_passed": True,
            "tflint_passed": True,
            "opa_passed": True,
            "errors": [],
            "warnings": [],
        }

    monkeypatch.setattr(hcl_module, "LLMClient", _FakeLLMClient)
    monkeypatch.setattr(hcl_module, "_deterministic_validate_hcl", _fake_validate)

    updated = hcl_module.hcl_generator(_base_state())  # type: ignore[arg-type]

    assert updated["retry_count"] == 1
    assert updated["validation_result"]["passed"] is True
    assert "aws-migration/modules/networking/main.tf" in updated["hcl_output"]
    assert "aws-migration/networking/vpc.tf" not in updated["hcl_output"]


def test_hcl_generator_exhausts_retries_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BadLLMClient:
        def __init__(self, config: dict[str, object]) -> None:
            self.config = config

        def generate_hcl(self, prompt: str, system_prompt: str | None = None) -> str:
            return "not-json"

    monkeypatch.setattr(hcl_module, "LLMClient", _BadLLMClient)

    updated = hcl_module.hcl_generator(_base_state())  # type: ignore[arg-type]

    assert updated["retry_count"] == 2
    assert updated["validation_result"]["passed"] is False
    assert updated["hcl_output"] == {}


def test_hcl_generator_skips_for_empty_workload(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ShouldNotBeCalledLLMClient:
        def __init__(self, config: dict[str, object]) -> None:
            self.config = config

        def generate_hcl(self, prompt: str, system_prompt: str | None = None) -> str:
            raise AssertionError("LLMClient.generate_hcl should not be called for empty workload")

    monkeypatch.setattr(hcl_module, "LLMClient", _ShouldNotBeCalledLLMClient)

    state = _base_state()
    state["sized_cim"] = _empty_cim()  # type: ignore[index]
    state["mcp_context"] = ""

    updated = hcl_module.hcl_generator(state)  # type: ignore[arg-type]

    assert updated["validation_result"]["passed"] is True
    assert updated["hcl_output"] == {}
    assert updated["retry_count"] == 0


def test_hcl_generator_synthesizes_provider_module_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    class _RootlessLLMClient:
        def __init__(self, config: dict[str, object]) -> None:
            self.config = config

        def generate_hcl(self, prompt: str, system_prompt: str | None = None) -> str:
            payload = {
                "aws-migration/networking/vpc.tf": 'resource "aws_vpc" "main" { cidr_block = "10.0.0.0/16" }\nresource "aws_subnet" "main" { vpc_id = aws_vpc.main.id cidr_block = "10.0.1.0/24" }\nresource "aws_security_group" "main" { name = "main" vpc_id = aws_vpc.main.id }',
                "aws-migration/compute/app.tf": 'resource "aws_instance" "app" { ami = "ami-123" instance_type = "t3.medium" subnet_id = aws_subnet.main.id vpc_security_group_ids = [aws_security_group.main.id] }',
            }
            return json.dumps(payload)

    def _always_pass(config: dict[str, object], hcl_output: dict[str, str]) -> dict[str, object]:
        return {
            "passed": True,
            "terraform_validate_passed": True,
            "tflint_passed": True,
            "opa_passed": True,
            "errors": [],
            "warnings": [],
        }

    monkeypatch.setattr(hcl_module, "LLMClient", _RootlessLLMClient)
    monkeypatch.setattr(hcl_module, "_deterministic_validate_hcl", _always_pass)

    updated = hcl_module.hcl_generator(_base_state())  # type: ignore[arg-type]

    assert "aws-migration/main.tf" in updated["hcl_output"]
    main_tf = updated["hcl_output"]["aws-migration/main.tf"]
    assert "module \"networking\"" in main_tf
    assert "module \"compute\"" in main_tf
    assert "ami_id             = var.compute_ami_id" in main_tf
    assert "aws-migration/modules/networking/main.tf" in updated["hcl_output"]
    assert "aws-migration/modules/compute/main.tf" in updated["hcl_output"]

    compute_main = updated["hcl_output"]["aws-migration/modules/compute/main.tf"]
    assert 'data "aws_ssm_parameter" "default_ami"' in compute_main
    assert "trimspace(var.ami_id) != \"\"" in compute_main

    compute_vars = updated["hcl_output"]["aws-migration/modules/compute/variables.tf"]
    assert 'variable "ami_id"' in compute_vars


def test_ensure_runnable_hcl_accepts_azure_minimum_resource_groups() -> None:
    hcl_output = {
        "azure-migration/main.tf": "\n".join(
            [
                'resource "azurerm_virtual_network" "vnet" {}',
                'resource "azurerm_subnet" "subnet" {}',
                'resource "azurerm_network_security_group" "nsg" {}',
                'resource "azurerm_linux_virtual_machine" "vm" {}',
            ]
        )
    }

    hcl_module._ensure_runnable_hcl(provider="azure", hcl_output=hcl_output)


def test_ensure_runnable_hcl_rejects_openstack_missing_security_group() -> None:
    hcl_output = {
        "openstack-migration/main.tf": "\n".join(
            [
                'resource "openstack_networking_network_v2" "net" {}',
                'resource "openstack_networking_subnet_v2" "subnet" {}',
                'resource "openstack_compute_instance_v2" "vm" {}',
            ]
        )
    }

    with pytest.raises(hcl_module.HCLGenerationError, match="missing required resource groups"):
        hcl_module._ensure_runnable_hcl(provider="openstack", hcl_output=hcl_output)


def test_ensure_runnable_hcl_accepts_gcp_without_firewall() -> None:
    hcl_output = {
        "gcp-migration/main.tf": "\n".join(
            [
                'resource "google_compute_network" "vpc" {}',
                'resource "google_compute_subnetwork" "subnet" {}',
                'resource "google_compute_instance" "vm" {}',
            ]
        )
    }

    hcl_module._ensure_runnable_hcl(provider="gcp", hcl_output=hcl_output)


def test_sanitize_supporting_module_removes_duplicate_declarations() -> None:
    content = "\n".join(
        [
            'terraform { required_version = \">= 1.5.0\" }',
            'provider "aws" { region = "us-east-1" }',
            'variable "common_tags" { type = map(string) }',
            'output "x" { value = 1 }',
            'resource "aws_security_group" "sg" {',
            '  name = "sg"',
            '  vpc_id = "vpc-123"',
            '  tags = merge(local.common_tags, { Name = "sg" })',
            '}',
        ]
    )

    sanitized = hcl_module._sanitize_supporting_module(content)

    assert 'variable "common_tags"' not in sanitized
    assert 'provider "aws"' not in sanitized
    assert 'terraform {' not in sanitized
    assert 'output "x"' not in sanitized
    assert 'resource "aws_security_group" "sg"' in sanitized
    assert "merge(var.common_tags," in sanitized


def test_normalize_provider_specific_hcl_strips_unsupported_azure_argument() -> None:
    azure_storage = "\n".join(
        [
            'resource "azurerm_managed_disk" "disk" {',
            '  name                 = "disk1"',
            '  location             = "westeurope"',
            '  resource_group_name  = "rg"',
            '  storage_account_type = "StandardSSD_LRS"',
            '  create_option        = "Empty"',
            '  disk_size_gb         = 64',
            '  encryption_settings_enabled = true',
            '}',
        ]
    )

    updated = hcl_module._normalize_provider_specific_hcl(
        provider="azure",
        hcl_output={"azure-migration/storage/disks.tf": azure_storage},
    )

    assert "encryption_settings_enabled" not in updated["azure-migration/storage/disks.tf"]


def test_normalize_provider_specific_hcl_is_noop_for_aws() -> None:
    hcl_output = {
        "aws-migration/storage/disks.tf": 'resource "aws_ebs_volume" "disk" { encrypted = true }'
    }

    updated = hcl_module._normalize_provider_specific_hcl(provider="aws", hcl_output=hcl_output)

    assert updated == hcl_output


def test_normalize_provider_specific_hcl_normalizes_gcp_image_and_label_keys() -> None:
    gcp_main = "\n".join(
        [
            'resource "google_compute_disk" "centos_boot" {',
            '  image = "centos-cloud/centos-7"',
            '}',
            '',
            'locals {',
            '  common_tags = {',
            '    Environment = "dev"',
            '    Owner = "platform-team"',
            '    MigratedFrom = "vmware-vcenter"',
            '  }',
            '}',
            '',
            'resource "google_compute_instance" "centos" {',
            '  labels = {',
            '    SourceVCenter = "vc01"',
            '  }',
            '}',
        ]
    )

    updated = hcl_module._normalize_provider_specific_hcl(
        provider="gcp",
        hcl_output={"gcp-migration/main.tf": gcp_main},
    )

    normalized = updated["gcp-migration/main.tf"]
    assert "centos-cloud/centos-7" not in normalized
    assert "rocky-linux-9-optimized-gcp" in normalized
    assert "environment = \"dev\"" in normalized
    assert "owner = \"platform-team\"" in normalized
    assert "migrated_from = \"vmware-vcenter\"" in normalized
    assert "source_v_center = \"vc01\"" in normalized


def test_build_provider_fallback_hcl_for_gcp_emits_deterministic_files() -> None:
    cim = CanonicalInfrastructureModel(
        source_vcenter="vc01.local",
        target_provider=TargetProvider.GCP,
        network_topology=NetworkTopology(
            distributed_switches=[
                DistributedSwitch(
                    name="dvs-main",
                    port_groups=[PortGroup(name="pg-app", vlan_id="100")],
                )
            ]
        ),
        compute_units=[
            ComputeUnit(
                id="cu-1",
                name="app-1",
                vcpus=2,
                ram_mb=4096,
                migration_status=MigrationStatus.READY,
                nics=[NIC(name="nic0", port_group_ref="pg-app")],
            )
        ],
    )

    fallback = hcl_module._build_provider_fallback_hcl(
        provider="gcp",
        sized_cim=cim,
        required_tags={
            "Environment": "dev",
            "Owner": "platform-team",
            "MigratedFrom": "vmware-vcenter",
        },
    )

    assert "gcp-migration/main.tf" in fallback
    assert "gcp-migration/providers.tf" in fallback
    assert "gcp-migration/variables.tf" in fallback
    assert "gcp-migration/networking/vpc.tf" in fallback
    assert "gcp-migration/networking/subnets.tf" in fallback
    assert "gcp-migration/compute/app-1.tf" in fallback

    vpc_tf = fallback["gcp-migration/networking/vpc.tf"]
    assert "google_compute_network_peering_routes_config" not in vpc_tf

    compute_tf = fallback["gcp-migration/compute/app-1.tf"]
    assert "labels = {" in compute_tf
    assert "environment = \"dev\"" in compute_tf
    assert "owner = \"platform-team\"" in compute_tf
    assert "migratedfrom" in compute_tf or "migrated_from" in compute_tf


def test_build_provider_fallback_hcl_is_empty_for_non_gcp() -> None:
    fallback = hcl_module._build_provider_fallback_hcl(
        provider="aws",
        sized_cim=_sample_cim(),
        required_tags={
            "Environment": "dev",
            "Owner": "platform",
            "MigratedFrom": "vmware-vcenter",
        },
    )

    assert fallback == {}


def test_build_provider_fallback_hcl_returns_openstack_files() -> None:
    cim = CanonicalInfrastructureModel(
        source_vcenter="vc01.local",
        target_provider=TargetProvider.OPENSTACK,
        network_topology=NetworkTopology(
            distributed_switches=[
                DistributedSwitch(
                    name="dvs-main",
                    port_groups=[PortGroup(name="pg-app", vlan_id="100")],
                )
            ]
        ),
        compute_units=[
            ComputeUnit(
                id="cu-1",
                name="app-1",
                vcpus=2,
                ram_mb=4096,
                migration_status=MigrationStatus.READY,
                nics=[NIC(name="nic0", port_group_ref="pg-app")],
            )
        ],
    )

    fallback = hcl_module._build_provider_fallback_hcl(
        provider="openstack",
        sized_cim=cim,
        required_tags={
            "Environment": "prod",
            "Owner": "platform-team",
            "MigratedFrom": "vmware-vcenter",
        },
    )

    assert "openstack-migration/networking/vpc.tf" in fallback
    assert "openstack-migration/networking/subnets.tf" in fallback
    assert "openstack-migration/networking/security_groups.tf" in fallback
    assert "openstack-migration/compute/app-1.tf" in fallback
    assert "openstack-migration/storage/app-1_disks.tf" in fallback


def test_synthesize_provider_runtime_scaffold_for_openstack_adds_provider_and_variables() -> None:
    hcl_output = {
        "openstack-migration/main.tf": '\n'.join(
            [
                "terraform {",
                "  required_providers {",
                "    openstack = { source = \"hashicorp/openstack\" }",
                "  }",
                "}",
                "",
                'provider "openstack" {',
                '  auth_url = "https://example.invalid"',
                "}",
                "",
                'resource "openstack_networking_network_v2" "net" {}',
                'resource "openstack_networking_subnet_v2" "subnet" {}',
                'resource "openstack_networking_secgroup_v2" "sg" {}',
                'resource "openstack_compute_instance_v2" "vm" {}',
            ]
        )
        + "\n",
        "openstack-migration/compute/main.tf": 'resource "openstack_compute_instance_v2" "vm" {}\n',
    }

    updated = hcl_module._synthesize_provider_runtime_scaffold(
        provider="openstack",
        hcl_output=hcl_output,
        required_tags={
            "Environment": "prod",
            "Owner": "platform-team",
        },
    )

    assert "openstack-migration/providers.tf" in updated
    providers_tf = updated["openstack-migration/providers.tf"]
    assert 'source  = "terraform-provider-openstack/openstack"' in providers_tf
    assert 'provider "openstack" {' in providers_tf

    main_tf = updated["openstack-migration/main.tf"]
    assert "required_providers" not in main_tf
    assert 'provider "openstack"' not in main_tf

    assert "openstack-migration/compute/providers.tf" in updated
    compute_providers_tf = updated["openstack-migration/compute/providers.tf"]
    assert 'source  = "terraform-provider-openstack/openstack"' in compute_providers_tf

    assert "openstack-migration/variables.tf" in updated
    variables_tf = updated["openstack-migration/variables.tf"]
    assert 'variable "auth_url" {' in variables_tf
    assert 'variable "external_network_id" {' in variables_tf
    assert 'default = "prod"' in variables_tf
    assert 'default = "platform-team"' in variables_tf
