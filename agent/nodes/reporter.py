from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from loguru import logger

from agent.llm_client import LLMClient
from agent.prompts import build_report_system_prompt, build_report_user_prompt
from agent.state import MigrationState


def reporter(state: MigrationState) -> MigrationState:
    """Generate review notes and write runtime artifacts."""
    next_state = cast(MigrationState, dict(state))

    config = next_state.get("config", {})
    if not isinstance(config, dict):
        config = {}

    output_cfg = _get_mapping(config, "output")
    base_dir = Path(_get_str(output_cfg, "base_dir", "output")).resolve()
    write_review_notes = _get_bool(output_cfg, "write_review_notes", True)
    write_quarantine_report = _get_bool(output_cfg, "write_quarantine_report", True)
    overwrite_output = _get_bool(output_cfg, "overwrite", True)
    review_notes_filename = _get_str(output_cfg, "review_notes_filename", "review_notes.md")
    quarantine_filename = _get_str(output_cfg, "quarantine_report_filename", "quarantine_report.json")

    hcl_output = _normalize_hcl_output(next_state.get("hcl_output", {}))
    _prepare_hcl_output_base_dir(base_dir=base_dir, hcl_output=hcl_output, overwrite=overwrite_output)
    _write_hcl_output(base_dir=base_dir, hcl_output=hcl_output)

    review_notes = _generate_review_notes(next_state, config)

    if write_review_notes:
        notes_path = base_dir / review_notes_filename
        notes_path.parent.mkdir(parents=True, exist_ok=True)
        notes_path.write_text(review_notes, encoding="utf-8")

    quarantine_queue = list(next_state.get("quarantine_queue", []))
    if write_quarantine_report:
        quarantine_path = base_dir / quarantine_filename
        quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        quarantine_path.write_text(
            json.dumps(quarantine_queue, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )

    messages = list(next_state.get("messages", []))
    messages.append(
        "reporter: wrote_hcl_files={} wrote_review_notes={} wrote_quarantine_report={}".format(
            len(hcl_output),
            write_review_notes,
            write_quarantine_report,
        )
    )

    next_state["review_notes"] = review_notes
    next_state["messages"] = messages

    if next_state.get("status") != "failed":
        next_state["status"] = "succeeded"

    logger.info(
        "reporter: completed artifact write with {} HCL files and {} quarantine items",
        len(hcl_output),
        len(quarantine_queue),
    )

    return next_state


def _generate_review_notes(state: MigrationState, config: dict[str, object]) -> str:
    cim = state.get("sized_cim") or state.get("cim")

    summary_payload = {
        "status": state.get("status", "unknown"),
        "compute_units": len(cim.compute_units) if cim is not None else 0,
        "clusters": len(cim.clusters) if cim is not None else 0,
        "network_switches": len(cim.network_topology.distributed_switches) if cim is not None else 0,
        "generated_hcl_files": len(_normalize_hcl_output(state.get("hcl_output", {}))),
        "quarantine_items": len(state.get("quarantine_queue", [])),
        "retry_count": int(state.get("retry_count", 0)),
        "validation_result": state.get("validation_result", {}),
        "messages_tail": list(state.get("messages", []))[-20:],
    }

    system_prompt = build_report_system_prompt()
    user_prompt = build_report_user_prompt(summary_payload)

    try:
        client = LLMClient(config=config)
        notes = client.generate_review_notes(prompt=user_prompt, system_prompt=system_prompt)
        if notes.strip():
            return notes.strip()
    except Exception as exc:
        logger.warning("reporter: LLM review note generation failed: {}", str(exc))

    return _fallback_review_notes(summary_payload)


def _fallback_review_notes(summary_payload: dict[str, object]) -> str:
    return "\n".join(
        [
            "# Migration Review Notes",
            "",
            f"- Status: {summary_payload.get('status', 'unknown')}",
            f"- ComputeUnits processed: {summary_payload.get('compute_units', 0)}",
            f"- Clusters discovered: {summary_payload.get('clusters', 0)}",
            f"- Generated HCL files: {summary_payload.get('generated_hcl_files', 0)}",
            f"- Quarantine items: {summary_payload.get('quarantine_items', 0)}",
            f"- Retry count: {summary_payload.get('retry_count', 0)}",
            "",
            "## Validation Result",
            json.dumps(summary_payload.get("validation_result", {}), indent=2, ensure_ascii=True),
            "",
            "## Next Actions",
            "1. Review quarantine_report.json for blocked resources.",
            "2. Review validation errors and warnings, then rerun if needed.",
            "3. Validate generated HCL in target environment before apply.",
        ]
    )


def _normalize_hcl_output(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, str] = {}
    for path, content in raw.items():
        key = str(path).strip().replace("\\", "/")
        value = str(content)
        if key and value:
            normalized[key] = value
    return normalized


def _write_hcl_output(base_dir: Path, hcl_output: dict[str, str]) -> None:
    if base_dir.exists() and not base_dir.is_dir():
        raise ValueError(f"reporter: output base_dir must be a directory: {base_dir}")

    base_dir.mkdir(parents=True, exist_ok=True)

    for raw_path, content in hcl_output.items():
        relative = Path(raw_path)

        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"reporter: unsafe HCL output path: {raw_path}")

        destination = (base_dir / relative).resolve()
        if destination.parent.exists() and not destination.parent.is_dir():
            raise ValueError(
                "reporter: cannot create HCL path because parent exists as a file: "
                f"{destination.parent}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")


def _prepare_hcl_output_base_dir(base_dir: Path, hcl_output: dict[str, str], overwrite: bool) -> None:
    if base_dir.exists() and not base_dir.is_dir():
        raise ValueError(f"reporter: output base_dir must be a directory: {base_dir}")

    base_dir.mkdir(parents=True, exist_ok=True)

    if not overwrite:
        return

    provider_roots: set[str] = set()
    for raw_path in hcl_output.keys():
        relative = Path(raw_path)
        if not relative.parts:
            continue
        provider_roots.add(relative.parts[0])

    for provider_root in provider_roots:
        destination = base_dir / provider_root
        if not destination.exists():
            continue

        if destination.is_dir():
            _clean_generated_hcl_files(destination)
        else:
            destination.unlink()


def _clean_generated_hcl_files(provider_root_dir: Path) -> None:
    # Preserve Terraform state and plugin metadata between runs; only replace generated .tf files.
    for file_path in provider_root_dir.rglob("*.tf"):
        if file_path.is_file():
            file_path.unlink()

    nested_dirs = sorted(
        (path for path in provider_root_dir.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )

    for dir_path in nested_dirs:
        if dir_path.name == ".terraform":
            continue
        try:
            dir_path.rmdir()
        except OSError:
            # Directory is not empty (e.g., contains state, lock files, or plugin metadata).
            continue


def _get_mapping(source: dict[str, object], key: str) -> dict[str, object]:
    value = source.get(key)
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def _get_str(source: dict[str, object], key: str, default: str) -> str:
    value = source.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


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
