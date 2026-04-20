from __future__ import annotations

import json


def build_hcl_system_prompt(provider: str) -> str:
    return (
        "You generate Terraform HCL only. "
        "Return exactly one JSON object mapping output file paths to HCL content strings. "
        "No markdown, no explanation. "
        "Target provider: "
        f"{provider}."
    )


def build_hcl_user_prompt(
    provider: str,
    cim_json: str,
    documentation_context: str,
    validation_feedback: str,
    retry_count: int,
    required_tags: dict[str, str],
    source_vcenter: str,
) -> str:
    root_dir = f"{provider}-migration"
    structure = (
        "{root_dir}/main.tf\n"
        "{root_dir}/networking/vpc.tf\n"
        "{root_dir}/networking/subnets.tf\n"
        "{root_dir}/networking/security_groups.tf\n"
        "{root_dir}/placement/placement_groups.tf\n"
        "{root_dir}/compute/{compute_unit_name}.tf\n"
        "{root_dir}/storage/{compute_unit_name}_disks.tf"
    ).replace("{root_dir}", root_dir)

    provider_constraints = _provider_hcl_constraints(provider)

    return (
        "Generate production-ready Terraform HCL for the given CIM.\n\n"
        "Hard requirements:\n"
        "1. Return valid JSON object only: {\"path\": \"hcl\", ...}.\n"
        "2. Paths must follow this structure:\n"
        f"{structure}\n"
        "3. Every resource must include tags: "
        f"{json.dumps(required_tags, ensure_ascii=True)}\n"
        "4. Never generate public S3 ACLs, never generate ingress cidr_blocks with 0.0.0.0/0.\n"
        "5. Never use default VPC; always define explicit network resources.\n"
        "6. Preserve 1:1 NetworkTopology mapping (DistributedSwitch to VPC/VNet/Network, PortGroup to subnet).\n"
        "7. If a ComputeUnit has has_vtpm=true, add TODO comment in HCL for manual review.\n"
        "8. Keep ComputeUnit vcpus and ram_mb unchanged from CIM; use only provided sizing decisions.\n"
        f"9. Source vCenter for tags/context: {source_vcenter}.\n"
        f"10. This is retry attempt index: {retry_count}. Fix prior validation issues if provided.\n\n"
        "11. {root_dir}/main.tf must be a complete runnable root module for terraform init/validate/plan.\n"
        "12. Do not assume files in subdirectories are loaded together as one module.\n"
        "13. If using local.common_tags in a file, declare it in that same file or pass it via module inputs.\n"
        "14. Do not output informational/comment-only Terraform files for compute/networking when ComputeUnits are present.\n"
        "15. Generated HCL must include concrete resource blocks needed to run terraform plan.\n\n"
        f"{provider_constraints}"
        "CIM JSON:\n"
        f"{cim_json}\n\n"
        "MCP Documentation Context:\n"
        f"{documentation_context}\n\n"
        "Validation Feedback:\n"
        f"{validation_feedback}\n"
    )


def _provider_hcl_constraints(provider: str) -> str:
    normalized_provider = provider.strip().lower()
    if normalized_provider != "gcp":
        return ""

    return (
        "Provider-specific constraints (GCP):\n"
        "16. Never use deprecated CentOS 7 image references (for example centos-cloud/centos-7).\n"
        "17. GCP label keys must be lowercase snake_case (for example environment, owner, migrated_from).\n\n"
    )


def build_report_system_prompt() -> str:
    return (
        "Write concise migration review notes in markdown. "
        "Include: summary, blockers/quarantine, validation outcomes, and next actions."
    )


def build_report_user_prompt(summary_payload: dict[str, object]) -> str:
    return (
        "Generate review notes for this vmware-migration-agent run. "
        "Be precise and concise.\n\n"
        "Run data (JSON):\n"
        f"{json.dumps(summary_payload, indent=2, ensure_ascii=True)}"
    )
