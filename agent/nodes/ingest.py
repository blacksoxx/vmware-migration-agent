from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from loguru import logger

from agent.state import JSONValue, MigrationState


def ingest(state: MigrationState) -> MigrationState:
    """Load discovery data into MigrationState for downstream deterministic nodes."""
    next_state = cast(MigrationState, dict(state))

    existing_data = next_state.get("discovery_data")
    if _is_valid_discovery_payload(existing_data):
        discovery_data = existing_data
        logger.info("ingest: using discovery data already present in state")
    else:
        discovery_path = _resolve_discovery_path(next_state)
        discovery_data = _read_discovery_file(discovery_path)
        logger.info("ingest: loaded discovery data from {}", discovery_path)

    messages = list(next_state.get("messages", []))
    messages.append("ingest: discovery data ready")

    next_state["discovery_data"] = discovery_data
    next_state["messages"] = messages
    next_state["status"] = "running"

    return next_state


def _resolve_discovery_path(state: MigrationState) -> Path:
    config = state.get("config", {})
    for key in ("discovery_json_path", "discovery_path", "input_path"):
        value = config.get(key) if isinstance(config, dict) else None
        if isinstance(value, str) and value.strip():
            return Path(value).expanduser().resolve()

    return Path("discovery.json").resolve()


def _read_discovery_file(path: Path) -> dict[str, JSONValue]:
    if not path.exists():
        raise FileNotFoundError(f"Discovery JSON file not found: {path}")

    with path.open("r", encoding="utf-8") as file_handle:
        raw_payload = json.load(file_handle)

    payload = _coerce_json_mapping(raw_payload)
    if not _is_valid_discovery_payload(payload):
        raise ValueError("Discovery JSON must include a 'virtual_machines' list")

    return payload


def _coerce_json_mapping(value: object) -> dict[str, JSONValue]:
    if not isinstance(value, dict):
        raise ValueError("Discovery JSON root must be an object")
    return cast(dict[str, JSONValue], value)


def _is_valid_discovery_payload(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return isinstance(value.get("virtual_machines"), list)
