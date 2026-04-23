from __future__ import annotations

import json
from pathlib import Path
import re
from typing import cast

from loguru import logger

from cim.schema import CanonicalInfrastructureModel
from agent.llm_client import LLMClient
from agent.prompts import build_hcl_system_prompt, build_hcl_user_prompt
from agent.state import MigrationState
from validation.opa_client import evaluate_policies
from validation.tf_runner import validate_hcl


class HCLGenerationError(Exception):
    """Raised when HCL generation fails or returns invalid output."""


def hcl_generator(state: MigrationState) -> MigrationState:
    """Generate HCL with an internal deterministic validation feedback loop."""
    next_state = cast(MigrationState, dict(state))

    sized_cim = next_state.get("sized_cim")
    if sized_cim is None:
        raise HCLGenerationError("hcl_generator: sized_cim is required before generation")

    if _is_empty_workload(sized_cim):
        messages = list(next_state.get("messages", []))
        warning_message = "hcl_generator: skipped generation because CIM workload is empty"
        messages.append(warning_message)

        next_state["retry_count"] = 0
        next_state["hcl_output"] = {}
        next_state["validation_result"] = {
            "passed": True,
            "terraform_validate_passed": True,
            "tflint_passed": True,
            "opa_passed": True,
            "errors": [],
            "warnings": [warning_message],
        }
        next_state["messages"] = messages
        next_state["status"] = "running"

        logger.info("hcl_generator: skipped generation because CIM workload is empty")
        return next_state

    config = next_state.get("config", {})
    if not isinstance(config, dict):
        config = {}

    provider = str(sized_cim.target_provider.value)
    documentation_context = str(next_state.get("mcp_context") or "")
    if not documentation_context.strip():
        raise HCLGenerationError("hcl_generator: MCP documentation context is required")

    max_retries = _get_max_retries(config)
    validation_feedback = _build_validation_feedback(next_state)

    llm_client = LLMClient(config=config)

    messages = list(next_state.get("messages", []))
    last_candidate: dict[str, str] = {}
    last_validation = {
        "passed": False,
        "terraform_validate_passed": False,
        "tflint_passed": False,
        "opa_passed": False,
        "errors": ["hcl_generator: generation did not run"],
        "warnings": [],
    }

    for attempt in range(0, max_retries + 1):
        prompt = build_hcl_user_prompt(
            provider=provider,
            cim_json=sized_cim.model_dump_json(indent=2),
            documentation_context=documentation_context,
            validation_feedback=validation_feedback,
            retry_count=attempt,
            required_tags=_required_tags(config),
            source_vcenter=sized_cim.source_vcenter,
        )

        raw_response = llm_client.generate_hcl(
            prompt=prompt,
            system_prompt=build_hcl_system_prompt(provider),
        )

        try:
            hcl_output = _parse_hcl_output(raw_response)
            _validate_output_paths(provider=provider, hcl_output=hcl_output)
            _enforce_security_guards(hcl_output)
            hcl_output = _synthesize_root_main_tf(provider=provider, hcl_output=hcl_output)
            hcl_output = _synthesize_provider_module_layout(
                provider=provider,
                hcl_output=hcl_output,
                required_tags=_required_tags(config),
            )
            hcl_output = _normalize_provider_specific_hcl(provider=provider, hcl_output=hcl_output)
            _ensure_runnable_hcl(provider=provider, hcl_output=hcl_output)
        except HCLGenerationError as exc:
            validation_feedback = f"errors:\n- {str(exc)}"
            last_validation = {
                "passed": False,
                "terraform_validate_passed": False,
                "tflint_passed": False,
                "opa_passed": False,
                "errors": [str(exc)],
                "warnings": [],
            }
            logger.warning(
                "hcl_generator: attempt {} pre-validation parse/guard failure for provider {}: {}",
                attempt,
                provider,
                str(exc),
            )
            messages.append(
                "hcl_generator: attempt={} failed pre-validation parse/guard checks: {}".format(
                    attempt,
                    str(exc),
                )
            )
            continue

        last_candidate = hcl_output
        deterministic_validation = _deterministic_validate_hcl(
            config=config,
            hcl_output=hcl_output,
        )
        last_validation = deterministic_validation

        if deterministic_validation["passed"]:
            messages.append(
                "hcl_generator: generation loop passed attempt={} files={} provider={}".format(
                    attempt,
                    len(hcl_output),
                    provider,
                )
            )
            next_state["retry_count"] = attempt
            next_state["hcl_output"] = hcl_output
            next_state["validation_result"] = cast(dict[str, object], deterministic_validation)
            next_state["messages"] = messages
            next_state["status"] = "running"

            hcl_output = _synthesize_provider_runtime_scaffold(
                provider=provider,
                hcl_output=hcl_output,
                required_tags=_required_tags(config),
            )

            next_state["hcl_output"] = hcl_output

            logger.info(
                "hcl_generator: generated {} HCL files for provider {} on attempt {}",
                len(hcl_output),
                provider,
                attempt,
            )
            return next_state

        validation_feedback = _feedback_from_validation(deterministic_validation)
        messages.append(
            "hcl_generator: attempt={} deterministic validation failed errors={}".format(
                attempt,
                len(deterministic_validation["errors"]),
            )
        )

    fallback_output = _build_provider_fallback_hcl(
        provider=provider,
        sized_cim=sized_cim,
        required_tags=_required_tags(config),
    )

    if fallback_output:
        messages.append(
            "hcl_generator: attempting deterministic fallback generation for provider={}".format(
                provider
            )
        )
        try:
            _validate_output_paths(provider=provider, hcl_output=fallback_output)
            _enforce_security_guards(fallback_output)
            fallback_output = _normalize_provider_specific_hcl(
                provider=provider,
                hcl_output=fallback_output,
            )
            _ensure_runnable_hcl(provider=provider, hcl_output=fallback_output)
        except HCLGenerationError as exc:
            messages.append(
                "hcl_generator: deterministic fallback pre-validation failed: {}".format(str(exc))
            )
            logger.warning(
                "hcl_generator: deterministic fallback pre-validation failed for provider {}: {}",
                provider,
                str(exc),
            )
        else:
            fallback_validation = _deterministic_validate_hcl(
                config=config,
                hcl_output=fallback_output,
            )
            last_candidate = fallback_output
            last_validation = fallback_validation

            if fallback_validation["passed"]:
                next_state["retry_count"] = max_retries
                next_state["hcl_output"] = fallback_output
                next_state["validation_result"] = cast(dict[str, object], fallback_validation)
                next_state["messages"] = messages
                next_state["status"] = "running"

                logger.info(
                    "hcl_generator: deterministic fallback generated {} HCL files for provider {}",
                    len(fallback_output),
                    provider,
                )
                return next_state

            messages.append(
                "hcl_generator: deterministic fallback validation failed errors={}".format(
                    len(fallback_validation["errors"])
                )
            )

    next_state["retry_count"] = max_retries
    next_state["hcl_output"] = last_candidate
    next_state["validation_result"] = cast(dict[str, object], last_validation)
    next_state["messages"] = messages
    next_state["status"] = "running"

    logger.error(
        "hcl_generator: generation loop exhausted retries for provider {} (attempts={})",
        provider,
        max_retries + 1,
    )
    return next_state


def _deterministic_validate_hcl(
    config: dict[str, object],
    hcl_output: dict[str, str],
) -> dict[str, object]:
    validation_cfg = _get_mapping(config, "validation")

    run_tflint = _get_bool(validation_cfg, "run_tflint", True)
    run_opa = _get_bool(validation_cfg, "run_opa", True)
    terraform_bin = _get_str(validation_cfg, "terraform_bin", "terraform")
    tflint_bin = _get_str(validation_cfg, "tflint_bin", "tflint")

    tf_result = validate_hcl(
        hcl_output=hcl_output,
        terraform_bin=terraform_bin,
        tflint_bin=tflint_bin,
        run_tflint=run_tflint,
    )

    opa_passed = True
    opa_errors: list[str] = []
    opa_warnings: list[str] = []

    if run_opa:
        opa_mode = _get_str(validation_cfg, "opa_mode", "binary")
        opa_bin = _get_str(validation_cfg, "opa_bin", "opa")
        opa_server_url = _get_str(validation_cfg, "opa_server_url", "")
        policies_dir = Path(_get_str(validation_cfg, "policies_dir", "validation/policies")).resolve()

        opa_result = evaluate_policies(
            hcl_output=hcl_output,
            policies_dir=policies_dir,
            mode=opa_mode,
            opa_bin=opa_bin,
            opa_server_url=opa_server_url,
        )
        opa_passed = opa_result.passed
        opa_errors = list(opa_result.errors)
        opa_warnings = list(opa_result.warnings)

    errors = [*tf_result.errors, *opa_errors]
    warnings = [*tf_result.warnings, *opa_warnings]
    passed = bool(tf_result.passed and opa_passed)

    return {
        "passed": passed,
        "terraform_validate_passed": tf_result.terraform_validate_passed,
        "tflint_passed": tf_result.tflint_passed,
        "opa_passed": opa_passed,
        "errors": errors,
        "warnings": warnings,
    }


def _feedback_from_validation(validation_result: dict[str, object]) -> str:
    errors = validation_result.get("errors", [])
    warnings = validation_result.get("warnings", [])

    lines: list[str] = []

    if isinstance(errors, list) and errors:
        lines.append("errors:")
        lines.extend(f"- {str(item)}" for item in errors)

    if isinstance(warnings, list) and warnings:
        lines.append("warnings:")
        lines.extend(f"- {str(item)}" for item in warnings)

    if not lines:
        return "none"

    return "\n".join(lines)


def _get_max_retries(config: dict[str, object]) -> int:
    validation_cfg = _get_mapping(config, "validation")
    pipeline_cfg = _get_mapping(config, "pipeline")

    retries = _get_int(validation_cfg, "max_retries", 0)
    if retries <= 0:
        retries = _get_int(pipeline_cfg, "max_retries", 3)
    if retries <= 0:
        retries = 3
    return retries


def _build_validation_feedback(state: MigrationState) -> str:
    validation_result = state.get("validation_result", {})
    if not isinstance(validation_result, dict):
        validation_result = {}

    errors = validation_result.get("errors", [])
    warnings = validation_result.get("warnings", [])

    lines: list[str] = []
    if isinstance(errors, list) and errors:
        lines.append("errors:")
        lines.extend(f"- {str(item)}" for item in errors)

    if isinstance(warnings, list) and warnings:
        lines.append("warnings:")
        lines.extend(f"- {str(item)}" for item in warnings)

    if not lines:
        return "none"

    return "\n".join(lines)


def _get_mapping(source: dict[str, object], key: str) -> dict[str, object]:
    value = source.get(key)
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def _get_bool(source: dict[str, object], key: str, default: bool) -> bool:
    value = source.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    return default


def _get_int(source: dict[str, object], key: str, default: int) -> int:
    value = source.get(key)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _get_str(source: dict[str, object], key: str, default: str) -> str:
    value = source.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _required_tags(config: dict[str, object]) -> dict[str, str]:
    tags = config.get("required_tags", {})
    if not isinstance(tags, dict):
        return {
            "Environment": "{environment}",
            "Owner": "{owner}",
            "MigratedFrom": "vmware-vcenter",
        }

    normalized: dict[str, str] = {}
    for key, value in tags.items():
        normalized[str(key)] = str(value)

    for required_key, required_value in {
        "Environment": "{environment}",
        "Owner": "{owner}",
        "MigratedFrom": "vmware-vcenter",
    }.items():
        normalized.setdefault(required_key, required_value)

    return normalized


def _parse_hcl_output(raw_response: str) -> dict[str, str]:
    payload = raw_response.strip()
    if not payload:
        raise HCLGenerationError("hcl_generator: empty LLM response")

    parsed: object
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        extracted = _extract_first_json_object(payload)
        if extracted is None:
            raise HCLGenerationError("hcl_generator: response is not valid JSON")
        parsed = extracted

    if not isinstance(parsed, dict):
        raise HCLGenerationError("hcl_generator: top-level JSON must be an object")

    output: dict[str, str] = {}
    for key, value in parsed.items():
        path = str(key).strip().replace("\\", "/")
        content = str(value).strip()
        if path and content:
            output[path] = content

    if not output:
        raise HCLGenerationError("hcl_generator: JSON response did not contain HCL files")

    return output


def _extract_first_json_object(text: str) -> object | None:
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : index + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None

    return None


def _validate_output_paths(provider: str, hcl_output: dict[str, str]) -> None:
    root_dir = _provider_migration_root(provider)
    allowed_roots = {
        f"{root_dir}/networking/",
        f"{root_dir}/compute/",
        f"{root_dir}/storage/",
        f"{root_dir}/placement/",
        f"{root_dir}/modules/",
    }
    allowed_files = {
        f"{root_dir}/main.tf",
        f"{root_dir}/providers.tf",
        f"{root_dir}/locals.tf",
        f"{root_dir}/variables.tf",
    }

    for raw_path in hcl_output:
        path = raw_path.replace("\\", "/")
        if path not in allowed_files and not any(path.startswith(root) for root in allowed_roots):
            raise HCLGenerationError(
                "hcl_generator: invalid output path outside required structure: "
                f"{raw_path}"
            )

        suffix = Path(path).suffix.lower()
        if suffix != ".tf":
            raise HCLGenerationError(
                f"hcl_generator: generated file must use .tf suffix, got {raw_path}"
            )


def _provider_migration_root(provider: str) -> str:
    normalized = provider.strip().lower()
    if not normalized:
        raise HCLGenerationError("hcl_generator: provider is required for output path validation")
    return f"{normalized}-migration"


def _enforce_security_guards(hcl_output: dict[str, str]) -> None:
    forbidden_patterns = [
        # AWS
        'acl = "public-read"',
        'acl = "public-read-write"',
        'cidr_blocks = ["0.0.0.0/0"]',
        'associate_public_ip_address = true',
        # Azure
        'source_address_prefix = "*"',
        'source_address_prefix = "0.0.0.0/0"',
        'resource "azurerm_public_ip"',
        # GCP
        'source_ranges = ["0.0.0.0/0"]',
        'access_config {',
        # OpenStack
        'remote_ip_prefix = "0.0.0.0/0"',
        'resource "openstack_networking_floatingip_v2"',
        # Default network usage guardrails
        'resource "aws_default_vpc"',
        'resource "google_compute_network" "default"',
    ]

    for path, content in hcl_output.items():
        lowered_content = content.lower()
        for pattern in forbidden_patterns:
            if pattern.lower() in lowered_content:
                raise HCLGenerationError(
                    f"hcl_generator: generated unsafe HCL pattern '{pattern}' in {path}"
                )


def _synthesize_root_main_tf(provider: str, hcl_output: dict[str, str]) -> dict[str, str]:
    root_dir = _provider_migration_root(provider)
    root_main_path = f"{root_dir}/main.tf"

    if root_main_path in hcl_output:
        return hcl_output

    source_paths = [
        path
        for path in sorted(hcl_output.keys())
        if path.startswith(f"{root_dir}/")
        and path.endswith(".tf")
        and path != root_main_path
    ]

    if not source_paths:
        return hcl_output

    preferred_order = ["networking", "placement", "storage", "compute"]

    def _sort_key(path: str) -> tuple[int, str]:
        segments = path.split("/")
        resource_type = segments[1] if len(segments) > 1 else ""
        try:
            bucket_index = preferred_order.index(resource_type)
        except ValueError:
            bucket_index = len(preferred_order)
        return (bucket_index, path)

    ordered_paths = sorted(source_paths, key=_sort_key)

    lines: list[str] = [
        "# Consolidated root module generated by vmware-migration-agent.",
        "# Use this file when running terraform commands from the provider root directory.",
        "",
    ]

    for path in ordered_paths:
        lines.append(f"# ----- BEGIN {path} -----")
        lines.append(hcl_output[path].rstrip())
        lines.append(f"# ----- END {path} -----")
        lines.append("")

    synthesized = dict(hcl_output)
    synthesized[root_main_path] = "\n".join(lines).rstrip() + "\n"
    return synthesized


def _synthesize_provider_module_layout(
    provider: str,
    hcl_output: dict[str, str],
    required_tags: dict[str, str],
) -> dict[str, str]:
    if provider.strip().lower() != "aws":
        return hcl_output

    root_dir = _provider_migration_root(provider)
    networking_paths = _sorted_tf_paths(hcl_output, f"{root_dir}/networking/")
    compute_paths = _sorted_tf_paths(hcl_output, f"{root_dir}/compute/")
    storage_paths = _sorted_tf_paths(hcl_output, f"{root_dir}/storage/")
    placement_paths = _sorted_tf_paths(hcl_output, f"{root_dir}/placement/")

    if not networking_paths or not compute_paths:
        return hcl_output

    networking_main_raw = _concat_files_with_headers(hcl_output, networking_paths)
    compute_main_raw = _concat_files_with_headers(hcl_output, compute_paths)
    storage_main_raw = _concat_files_with_headers(hcl_output, storage_paths)
    placement_main_raw = _concat_files_with_headers(hcl_output, placement_paths)

    networking_main = _sanitize_supporting_module(networking_main_raw)
    compute_main = _sanitize_compute_module(compute_main_raw)
    storage_main = _sanitize_supporting_module(storage_main_raw)
    placement_main = _sanitize_supporting_module(placement_main_raw)

    subnet_names = _find_resource_names(networking_main, "aws_subnet")
    sg_names = _find_resource_names(networking_main, "aws_security_group")

    primary_subnet_expr = "null"
    if subnet_names:
        primary_subnet_expr = f"aws_subnet.{subnet_names[0]}.id"

    sg_ids_expr = "[]"
    if sg_names:
        sg_ids_expr = "[{}]".format(
            ", ".join(f"aws_security_group.{name}.id" for name in sg_names)
        )

    locals_lines = ["locals {", "  common_tags = {"]
    for key in sorted(required_tags.keys()):
        value = required_tags[key]
        locals_lines.append(f'    {key} = {json.dumps(str(value), ensure_ascii=True)}')
    locals_lines.extend(["  }", "}"])

    synthesized: dict[str, str] = {}

    synthesized[f"{root_dir}/providers.tf"] = "\n".join(
        [
            "terraform {",
            "  required_version = \">= 1.5.0\"",
            "  required_providers {",
            "    aws = {",
            "      source  = \"hashicorp/aws\"",
            "      version = \"~> 6.0\"",
            "    }",
            "  }",
            "}",
            "",
            "provider \"aws\" {",
            "  region = var.aws_region",
            "}",
            "",
            "variable \"aws_region\" {",
            "  type    = string",
            "  default = \"us-east-1\"",
            "}",
            "",
            "variable \"compute_ami_id\" {",
            "  type    = string",
            "  default = \"\"",
            "}",
            "",
        ]
    )

    synthesized[f"{root_dir}/locals.tf"] = "\n".join(locals_lines) + "\n"

    synthesized[f"{root_dir}/main.tf"] = "\n".join(
        [
            "module \"networking\" {",
            "  source = \"./modules/networking\"",
            "  common_tags = local.common_tags",
            "}",
            "",
            "module \"compute\" {",
            "  source             = \"./modules/compute\"",
            "  subnet_id          = module.networking.primary_subnet_id",
            "  security_group_ids = module.networking.security_group_ids",
            "  ami_id             = var.compute_ami_id",
            "  common_tags        = local.common_tags",
            "}",
            "",
            "module \"storage\" {",
            "  source      = \"./modules/storage\"",
            "  common_tags = local.common_tags",
            "}",
            "",
            "module \"placement\" {",
            "  source      = \"./modules/placement\"",
            "  common_tags = local.common_tags",
            "}",
            "",
        ]
    )

    synthesized[f"{root_dir}/modules/networking/main.tf"] = _non_empty_module(
        networking_main,
        "# Networking module generated with no concrete resources.",
    )
    synthesized[f"{root_dir}/modules/networking/outputs.tf"] = "\n".join(
        [
            "output \"primary_subnet_id\" {",
            f"  value = {primary_subnet_expr}",
            "}",
            "",
            "output \"security_group_ids\" {",
            f"  value = {sg_ids_expr}",
            "}",
            "",
        ]
    )
    synthesized[f"{root_dir}/modules/networking/variables.tf"] = "\n".join(
        [
            "variable \"common_tags\" {",
            "  type    = map(string)",
            "  default = {}",
            "}",
            "",
        ]
    )

    synthesized[f"{root_dir}/modules/compute/main.tf"] = _non_empty_module(
        compute_main,
        "# Compute module generated with no concrete resources.",
    )
    synthesized[f"{root_dir}/modules/compute/variables.tf"] = "\n".join(
        [
            "variable \"subnet_id\" {",
            "  type = string",
            "}",
            "",
            "variable \"security_group_ids\" {",
            "  type = list(string)",
            "}",
            "",
            "variable \"ami_id\" {",
            "  type    = string",
            "  default = \"\"",
            "}",
            "",
            "variable \"common_tags\" {",
            "  type    = map(string)",
            "  default = {}",
            "}",
            "",
        ]
    )

    synthesized[f"{root_dir}/modules/storage/main.tf"] = _non_empty_module(
        storage_main,
        "# Storage module generated with no concrete resources.",
    )
    synthesized[f"{root_dir}/modules/storage/variables.tf"] = "\n".join(
        [
            "variable \"common_tags\" {",
            "  type    = map(string)",
            "  default = {}",
            "}",
            "",
        ]
    )

    synthesized[f"{root_dir}/modules/placement/main.tf"] = _non_empty_module(
        placement_main,
        "# Placement module generated with no concrete resources.",
    )
    synthesized[f"{root_dir}/modules/placement/variables.tf"] = "\n".join(
        [
            "variable \"common_tags\" {",
            "  type    = map(string)",
            "  default = {}",
            "}",
            "",
        ]
    )

    return synthesized


def _sorted_tf_paths(hcl_output: dict[str, str], prefix: str) -> list[str]:
    return sorted(
        path
        for path in hcl_output.keys()
        if path.startswith(prefix) and path.endswith(".tf")
    )


def _concat_files_with_headers(hcl_output: dict[str, str], paths: list[str]) -> str:
    if not paths:
        return ""

    lines: list[str] = []
    for path in paths:
        lines.append(f"# ----- BEGIN {path} -----")
        lines.append(hcl_output[path].rstrip())
        lines.append(f"# ----- END {path} -----")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _rewrite_compute_to_module_inputs(content: str) -> str:
    rewritten = re.sub(
        r"subnet_id\s*=\s*aws_subnet\.[A-Za-z0-9_]+\.id",
        "subnet_id                   = var.subnet_id",
        content,
    )

    rewritten = re.sub(
        r"vpc_security_group_ids\s*=\s*\[[^\n]*\]",
        "vpc_security_group_ids      = var.security_group_ids",
        rewritten,
    )

    rewritten = _rewrite_common_tags_to_var(rewritten)
    rewritten = re.sub(r"\[\s*var\.security_group_id\s*\]", "var.security_group_ids", rewritten)
    rewritten = re.sub(r"\bvar\.security_group_id\b", "var.security_group_ids[0]", rewritten)
    return rewritten


def _sanitize_compute_module(content: str) -> str:
    sanitized = _strip_top_level_blocks(
        content,
        block_types={"variable", "output", "terraform", "provider", "locals", "data"},
    )
    rewritten = _rewrite_compute_to_module_inputs(sanitized)
    return _normalize_compute_ami_resolution(rewritten)


def _sanitize_supporting_module(content: str) -> str:
    sanitized = _strip_top_level_blocks(
        content,
        block_types={"variable", "output", "terraform", "provider"},
    )
    return _rewrite_common_tags_to_var(sanitized)


def _normalize_compute_ami_resolution(content: str) -> str:
    ami_assignment_pattern = re.compile(r"ami\s*=\s*[^\n]+")
    ami_assignment = (
        "ami           = trimspace(var.ami_id) != \"\" "
        "? var.ami_id : data.aws_ssm_parameter.default_ami.value"
    )

    if ami_assignment_pattern.search(content):
        normalized = ami_assignment_pattern.sub(ami_assignment, content, count=1)
    else:
        normalized = content

    if 'data "aws_ssm_parameter" "default_ami"' in normalized:
        return normalized

    ssm_data_block = "\n".join(
        [
            'data "aws_ssm_parameter" "default_ami" {',
            '  # Public AWS parameter for latest AL2023 x86_64 AMI.',
            '  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"',
            '}',
            "",
        ]
    )

    return ssm_data_block + normalized


def _rewrite_common_tags_to_var(content: str) -> str:
    rewritten = re.sub(r"merge\(\s*local\.common_tags\s*,", "merge(var.common_tags,", content)

    def _inject_required_tag_literals(match: re.Match[str]) -> str:
        return (
            "merge(var.common_tags, {\n"
            "    Environment = lookup(var.common_tags, \"Environment\", \"\")\n"
            "    Owner = lookup(var.common_tags, \"Owner\", \"\")\n"
            "    MigratedFrom = lookup(var.common_tags, \"MigratedFrom\", \"\")"
        )

    return re.sub(
        r"merge\(\s*var\.common_tags\s*,\s*\{",
        _inject_required_tag_literals,
        rewritten,
    )


def _strip_top_level_blocks(content: str, block_types: set[str]) -> str:
    lines = content.splitlines()
    kept: list[str] = []
    skip_depth = 0

    for line in lines:
        if skip_depth > 0:
            skip_depth += line.count("{") - line.count("}")
            continue

        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\b.*\{", line)
        if match and match.group(1) in block_types:
            skip_depth = line.count("{") - line.count("}")
            if skip_depth < 0:
                skip_depth = 0
            continue

        kept.append(line)

    rendered = "\n".join(kept).strip()
    if rendered:
        return rendered + "\n"
    return ""


def _non_empty_module(content: str, fallback_comment: str) -> str:
    stripped = content.strip()
    if stripped:
        return stripped + "\n"
    return fallback_comment + "\n"


def _find_resource_names(content: str, resource_type: str) -> list[str]:
    pattern = re.compile(
        rf'resource\s+"{re.escape(resource_type)}"\s+"([A-Za-z0-9_]+)"'
    )
    return pattern.findall(content)


def _is_empty_workload(cim_doc: object) -> bool:
    try:
        compute_units = len(cim_doc.compute_units)  # type: ignore[attr-defined]
        clusters = len(cim_doc.clusters)  # type: ignore[attr-defined]
        distributed_switches = len(cim_doc.network_topology.distributed_switches)  # type: ignore[attr-defined]
    except Exception:
        return False

    return compute_units == 0 and clusters == 0 and distributed_switches == 0


def _ensure_runnable_hcl(provider: str, hcl_output: dict[str, str]) -> None:
    merged_content = "\n".join(hcl_output.values())
    normalized_provider = provider.strip().lower()

    required_pattern_groups: dict[str, list[tuple[str, list[str]]]] = {
        "aws": [
            ("compute", ['resource "aws_instance"']),
            ("network", ['resource "aws_vpc"']),
            ("subnet", ['resource "aws_subnet"']),
            ("security", ['resource "aws_security_group"']),
        ],
        "azure": [
            (
                "compute",
                [
                    'resource "azurerm_linux_virtual_machine"',
                    'resource "azurerm_windows_virtual_machine"',
                    'resource "azurerm_virtual_machine"',
                ],
            ),
            ("network", ['resource "azurerm_virtual_network"']),
            ("subnet", ['resource "azurerm_subnet"']),
            ("security", ['resource "azurerm_network_security_group"']),
        ],
        "gcp": [
            ("compute", ['resource "google_compute_instance"']),
            ("network", ['resource "google_compute_network"']),
            ("subnet", ['resource "google_compute_subnetwork"']),
        ],
        "openstack": [
            ("compute", ['resource "openstack_compute_instance_v2"']),
            ("network", ['resource "openstack_networking_network_v2"']),
            ("subnet", ['resource "openstack_networking_subnet_v2"']),
            (
                "security",
                [
                    'resource "openstack_networking_secgroup_v2"',
                    'resource "openstack_networking_secgroup_rule_v2"',
                ],
            ),
        ],
    }

    pattern_groups = required_pattern_groups.get(normalized_provider)
    if not pattern_groups:
        return

    missing_groups: list[str] = []
    for group_name, patterns in pattern_groups:
        if not any(pattern in merged_content for pattern in patterns):
            missing_groups.append(group_name)

    if missing_groups:
        raise HCLGenerationError(
            "hcl_generator: generated HCL is not runnable; missing required resource groups "
            f"for provider {normalized_provider}: {', '.join(missing_groups)}"
        )


def _normalize_provider_specific_hcl(provider: str, hcl_output: dict[str, str]) -> dict[str, str]:
    normalized_provider = provider.strip().lower()
    if normalized_provider not in {"azure", "gcp"}:
        return hcl_output

    normalized: dict[str, str] = {}
    for path, content in hcl_output.items():
        normalized_content = content

        if normalized_provider == "azure" and 'resource "azurerm_managed_disk"' in normalized_content:
            normalized_content = _strip_unsupported_azure_managed_disk_args(normalized_content)

        if normalized_provider == "gcp":
            normalized_content = _normalize_gcp_generated_hcl(normalized_content)

        normalized[path] = normalized_content

    return normalized


def _strip_unsupported_azure_managed_disk_args(content: str) -> str:
    lines = content.splitlines()
    rendered: list[str] = []

    in_managed_disk = False
    depth = 0

    for line in lines:
        if not in_managed_disk:
            if re.match(r'^\s*resource\s+"azurerm_managed_disk"\s+"[^"]+"\s*\{', line):
                in_managed_disk = True
                depth = line.count("{") - line.count("}")
                rendered.append(line)
                continue

            rendered.append(line)
            continue

        current_depth = depth
        stripped = line.strip()

        if re.search(r"\bencryption_settings_enabled\s*=", stripped):
            depth = current_depth + line.count("{") - line.count("}")
            if depth <= 0:
                in_managed_disk = False
                depth = 0
            continue

        rendered.append(line)

        depth = current_depth + line.count("{") - line.count("}")
        if depth <= 0:
            in_managed_disk = False
            depth = 0

    normalized = "\n".join(rendered)
    if content.endswith("\n"):
        return normalized + "\n"
    return normalized


def _normalize_gcp_generated_hcl(content: str) -> str:
    normalized = content

    # CentOS 7 public images are deprecated/removed from common catalogs.
    for legacy_pattern in [
        r'image\s*=\s*"centos-cloud/centos-7"',
        r'image\s*=\s*"projects/centos-cloud/global/images/family/centos-7"',
    ]:
        normalized = re.sub(
            legacy_pattern,
            'image = "projects/rocky-linux-cloud/global/images/family/rocky-linux-9-optimized-gcp"',
            normalized,
        )

    normalized = _normalize_gcp_map_keys(
        content=normalized,
        map_names={"labels", "common_tags", "common_labels"},
    )
    return normalized


def _normalize_gcp_map_keys(content: str, map_names: set[str]) -> str:
    lines = content.splitlines()
    rendered: list[str] = []

    in_target_map = False
    depth = 0

    for line in lines:
        if not in_target_map:
            map_match = re.match(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{\s*$", line)
            if map_match and map_match.group(2) in map_names:
                in_target_map = True
                depth = line.count("{") - line.count("}")
                rendered.append(line)
                continue

            rendered.append(line)
            continue

        current_depth = depth
        key_match = re.match(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$", line)
        if key_match and current_depth == 1:
            indent = key_match.group(1)
            key = key_match.group(2)
            value = key_match.group(3)
            normalized_key = _normalize_gcp_label_key(key)
            line = f"{indent}{normalized_key} = {value}"

        rendered.append(line)

        depth = current_depth + line.count("{") - line.count("}")
        if depth <= 0:
            in_target_map = False
            depth = 0

    normalized = "\n".join(rendered)
    if content.endswith("\n"):
        return normalized + "\n"
    return normalized


def _normalize_gcp_label_key(key: str) -> str:
    normalized = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", key)
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized)
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9_]", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")

    if not normalized:
        return "label"
    if normalized[0].isdigit():
        normalized = f"k_{normalized}"

    return normalized[:63]


def _build_provider_fallback_hcl(
    provider: str,
    sized_cim: CanonicalInfrastructureModel,
    required_tags: dict[str, str],
) -> dict[str, str]:
    normalized_provider = provider.strip().lower()
    if normalized_provider != "gcp":
        return {}

    return _build_deterministic_gcp_fallback_hcl(
        sized_cim=sized_cim,
        required_tags=required_tags,
    )


def _build_deterministic_gcp_fallback_hcl(
    sized_cim: CanonicalInfrastructureModel,
    required_tags: dict[str, str],
) -> dict[str, str]:
    from providers.gcp.compute import render_compute as render_gcp_compute
    from providers.gcp.network import render_networking as render_gcp_networking

    output: dict[str, str] = {}

    networking_output = render_gcp_networking(sized_cim)
    for path, content in networking_output.items():
        output[path] = _strip_gcp_peering_routes_config(content)

    output.update(render_gcp_compute(sized_cim, required_tags=required_tags))
    output.update(_build_gcp_storage_placeholders(sized_cim))

    output["gcp-migration/main.tf"] = "\n".join(
        [
            "# Root module entrypoint for deterministic GCP fallback output.",
            "# Resources are declared in networking/, compute/, and storage/ files.",
            "",
        ]
    )

    output["gcp-migration/providers.tf"] = "\n".join(
        [
            "terraform {",
            "  required_version = \">= 1.5.0\"",
            "  required_providers {",
            "    google = {",
            "      source  = \"hashicorp/google\"",
            "      version = \"~> 6.0\"",
            "    }",
            "  }",
            "}",
            "",
            "provider \"google\" {",
            "  project = var.project_id",
            "  region  = var.region",
            "  zone    = var.zone",
            "}",
            "",
        ]
    )

    output["gcp-migration/variables.tf"] = "\n".join(
        [
            'variable "project_id" {',
            "  type = string",
            "}",
            "",
            'variable "region" {',
            "  type = string",
            "}",
            "",
            'variable "zone" {',
            "  type = string",
            "}",
            "",
            'variable "peering_name" {',
            "  type    = string",
            '  default = "migration-peering"',
            "}",
            "",
            'variable "default_image" {',
            "  type    = string",
            '  default = "projects/debian-cloud/global/images/family/debian-12"',
            "}",
            "",
            'variable "environment" {',
            "  type    = string",
            '  default = "dev"',
            "}",
            "",
            'variable "owner" {',
            "  type    = string",
            '  default = "platform-team"',
            "}",
            "",
        ]
    )

    return output


def _build_gcp_storage_placeholders(sized_cim: CanonicalInfrastructureModel) -> dict[str, str]:
    output: dict[str, str] = {}
    for compute_unit in sized_cim.compute_units:
        output[f"gcp-migration/storage/{compute_unit.name}_disks.tf"] = "\n".join(
            [
                f"# StorageVolume migration for ComputeUnit {compute_unit.name} requires project-specific disk policy.",
                "# Disk resources are intentionally omitted in fallback mode.",
                "",
            ]
        )
    return output


def _strip_gcp_peering_routes_config(content: str) -> str:
    lines = content.splitlines()
    rendered: list[str] = []

    in_block = False
    depth = 0

    for line in lines:
        if not in_block:
            if re.match(
                r'^\s*resource\s+"google_compute_network_peering_routes_config"\s+"[^"]+"\s*\{',
                line,
            ):
                in_block = True
                depth = line.count("{") - line.count("}")
                continue

            rendered.append(line)
            continue

        depth += line.count("{") - line.count("}")
        if depth <= 0:
            in_block = False
            depth = 0

    normalized = "\n".join(rendered).strip()
    if normalized:
        return normalized + "\n"
    return ""
