from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Mapping, cast

import yaml
from loguru import logger

ALLOWED_TARGET_CLOUDS = {"aws", "azure", "gcp", "openstack"}
ALLOWED_LLM_PROVIDERS = {"anthropic", "openai", "google"}
ALLOWED_LOG_LEVELS = {"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigLoadError(Exception):
    """Raised when configuration cannot be loaded or validated."""


def load_config(
    config_path: str | Path = "config.yaml",
    overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Load runtime configuration from YAML, apply overrides, and validate."""
    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise ConfigLoadError(f"Config file not found: {path}")

    try:
        raw_data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigLoadError(f"Failed to parse config YAML: {path}") from exc

    if raw_data is None:
        config: dict[str, object] = {}
    elif isinstance(raw_data, dict):
        config = cast(dict[str, object], raw_data)
    else:
        raise ConfigLoadError("Config root must be a mapping object")

    merged_config = copy.deepcopy(config)

    if overrides:
        _deep_merge(merged_config, overrides)

    _apply_environment_overrides(merged_config)
    _configure_logging(merged_config)
    _validate_config(merged_config)

    logger.info("config_loader: loaded config from {}", path)
    return merged_config


def _configure_logging(config: dict[str, object]) -> None:
    logging_cfg = _ensure_mapping(config, "logging")

    level = str(logging_cfg.get("level", "INFO")).strip().upper() or "INFO"
    if level not in ALLOWED_LOG_LEVELS:
        level = "INFO"

    output_format = str(logging_cfg.get("format", "console")).strip().lower() or "console"

    detailed_log_level = str(logging_cfg.get("detailed_log_level", "TRACE")).strip().upper() or "TRACE"
    if detailed_log_level not in ALLOWED_LOG_LEVELS:
        detailed_log_level = "TRACE"

    detailed_log_file = logging_cfg.get("detailed_log_file")

    logger.remove()
    if output_format == "json":
        logger.add(
            sys.stdout,
            level=level,
            serialize=True,
            backtrace=True,
            diagnose=False,
        )
    else:
        logger.add(
            sys.stderr,
            level=level,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}:{function}:{line}</cyan> | "
                "<level>{message}</level>"
            ),
            backtrace=True,
            diagnose=False,
        )

    if isinstance(detailed_log_file, str) and detailed_log_file.strip():
        detailed_path = Path(detailed_log_file.strip()).expanduser()
        detailed_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(detailed_path),
            level=detailed_log_level,
            serialize=True,
            backtrace=True,
            diagnose=True,
            enqueue=True,
            encoding="utf-8",
        )


def _apply_environment_overrides(config: dict[str, object]) -> None:
    target_cloud = os.getenv("VMWARE_MIGRATION_AGENT_TARGET_CLOUD")
    llm_provider = os.getenv("VMWARE_MIGRATION_AGENT_LLM_PROVIDER")
    llm_model = os.getenv("VMWARE_MIGRATION_AGENT_LLM_MODEL")
    llm_base_url = os.getenv("VMWARE_MIGRATION_AGENT_LLM_BASE_URL")

    if target_cloud and target_cloud.strip():
        config["target_cloud"] = target_cloud.strip().lower()

    llm_section = _ensure_mapping(config, "llm")

    if llm_provider and llm_provider.strip():
        llm_section["provider"] = llm_provider.strip().lower()

    if llm_model and llm_model.strip():
        llm_section["model"] = llm_model.strip()

    if llm_base_url and llm_base_url.strip():
        llm_section["base_url"] = llm_base_url.strip()

    mcp_tool_timeout = (
        os.getenv("VMWARE_MIGRATION_AGENT_MCP_TOOL_TIMEOUT_SECONDS")
        or os.getenv("VMWARE_MIGRATION_AGENT_MCP_TIMEOUT_SECONDS")
    )

    mcp_section = _ensure_mapping(config, "mcp")

    if mcp_tool_timeout and mcp_tool_timeout.strip():
        mcp_section["tool_timeout_seconds"] = mcp_tool_timeout.strip()


def _validate_config(config: dict[str, object]) -> None:
    target_cloud = str(config.get("target_cloud", "")).strip().lower()
    if target_cloud not in ALLOWED_TARGET_CLOUDS:
        raise ConfigLoadError(
            "target_cloud must be one of: aws, azure, gcp, openstack"
        )

    llm = _ensure_mapping(config, "llm")
    provider = str(llm.get("provider", "")).strip().lower()
    if provider not in ALLOWED_LLM_PROVIDERS:
        raise ConfigLoadError("llm.provider must be one of: anthropic, openai, google")

    temperature_hcl = _as_float(llm.get("temperature_hcl"), default=0.0)
    temperature_report = _as_float(llm.get("temperature_report"), default=0.2)

    if temperature_hcl > 0.0:
        raise ConfigLoadError("llm.temperature_hcl must be 0.0")
    if temperature_report > 0.2:
        raise ConfigLoadError("llm.temperature_report must be <= 0.2")

    _validate_mcp_config(config=config, target_cloud=target_cloud)


def _validate_mcp_config(config: dict[str, object], target_cloud: str) -> None:
    mcp = _ensure_mapping(config, "mcp")

    enabled = mcp.get("enabled", True)
    if isinstance(enabled, bool) and not enabled:
        raise ConfigLoadError("mcp.enabled must be true (MCP retrieval is mandatory)")
    if isinstance(enabled, str) and enabled.strip().lower() in {"false", "0", "no", "off"}:
        raise ConfigLoadError("mcp.enabled must be true (MCP retrieval is mandatory)")

    tool_timeout_seconds = _as_int(mcp.get("tool_timeout_seconds"), default=30)
    if tool_timeout_seconds <= 0:
        raise ConfigLoadError("mcp.tool_timeout_seconds must be greater than 0")

    terraform_cfg = _ensure_mapping(mcp, "terraform")

    image = terraform_cfg.get("image")
    if not isinstance(image, str) or not image.strip():
        raise ConfigLoadError("mcp.terraform.image is required")

    toolsets = terraform_cfg.get("toolsets")
    if not isinstance(toolsets, str) or not toolsets.strip():
        raise ConfigLoadError("mcp.terraform.toolsets is required")
    if toolsets.strip().lower() != "registry":
        raise ConfigLoadError("mcp.terraform.toolsets must be 'registry'")


def _deep_merge(target: dict[str, object], source: Mapping[str, object]) -> None:
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, Mapping):
            _deep_merge(cast(dict[str, object], target[key]), cast(Mapping[str, object], value))
        else:
            target[key] = copy.deepcopy(value)


def _ensure_mapping(container: dict[str, object], key: str) -> dict[str, object]:
    value = container.get(key)
    if isinstance(value, dict):
        return cast(dict[str, object], value)

    mapping: dict[str, object] = {}
    container[key] = mapping
    return mapping


def _as_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _as_int(value: object, default: int) -> int:
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
