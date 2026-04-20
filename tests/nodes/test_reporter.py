from __future__ import annotations

from pathlib import Path

import pytest

import agent.nodes.reporter as reporter_module
from agent.nodes.reporter import reporter


class _FakeLLMClient:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config

    def generate_review_notes(self, prompt: str, system_prompt: str | None = None) -> str:
        return "# Notes\n\nGenerated"


def test_reporter_writes_hcl_under_configured_output_base_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(reporter_module, "LLMClient", _FakeLLMClient)

    output_dir = tmp_path / "artifacts"
    state = {
        "config": {
            "output": {
                "base_dir": str(output_dir),
                "write_review_notes": True,
                "write_quarantine_report": True,
            }
        },
        "hcl_output": {
            "aws-migration/main.tf": 'resource "aws_vpc" "main" { cidr_block = "10.0.0.0/16" }'
        },
        "messages": [],
        "quarantine_queue": [],
        "status": "running",
    }

    updated = reporter(state)  # type: ignore[arg-type]

    assert updated["status"] == "succeeded"
    assert (output_dir / "aws-migration" / "main.tf").exists()
    assert (output_dir / "review_notes.md").exists()
    assert (output_dir / "quarantine_report.json").exists()


def test_reporter_rejects_output_base_dir_when_file_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(reporter_module, "LLMClient", _FakeLLMClient)

    output_file = tmp_path / "not_a_dir"
    output_file.write_text("x", encoding="utf-8")

    state = {
        "config": {
            "output": {
                "base_dir": str(output_file),
                "write_review_notes": False,
                "write_quarantine_report": False,
            }
        },
        "hcl_output": {
            "aws-migration/main.tf": 'resource "aws_vpc" "main" { cidr_block = "10.0.0.0/16" }'
        },
        "messages": [],
        "quarantine_queue": [],
        "status": "running",
    }

    with pytest.raises(ValueError, match="output base_dir must be a directory"):
        reporter(state)  # type: ignore[arg-type]


def test_reporter_overwrite_cleans_stale_provider_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(reporter_module, "LLMClient", _FakeLLMClient)

    output_dir = tmp_path / "artifacts"
    stale_file = output_dir / "aws-migration" / "compute" / "old.tf"
    stale_file.parent.mkdir(parents=True, exist_ok=True)
    stale_file.write_text("stale", encoding="utf-8")

    state = {
        "config": {
            "output": {
                "base_dir": str(output_dir),
                "overwrite": True,
                "write_review_notes": False,
                "write_quarantine_report": False,
            }
        },
        "hcl_output": {
            "aws-migration/main.tf": 'resource "aws_vpc" "main" { cidr_block = "10.0.0.0/16" }'
        },
        "messages": [],
        "quarantine_queue": [],
        "status": "running",
    }

    reporter(state)  # type: ignore[arg-type]

    assert not stale_file.exists()
    assert (output_dir / "aws-migration" / "main.tf").exists()


def test_reporter_overwrite_preserves_terraform_state_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(reporter_module, "LLMClient", _FakeLLMClient)

    output_dir = tmp_path / "artifacts"
    provider_root = output_dir / "aws-migration"

    stale_tf = provider_root / "compute" / "old.tf"
    stale_tf.parent.mkdir(parents=True, exist_ok=True)
    stale_tf.write_text("stale", encoding="utf-8")

    tfstate = provider_root / "terraform.tfstate"
    tfstate.write_text('{"version": 4}', encoding="utf-8")

    tfstate_backup = provider_root / "terraform.tfstate.backup"
    tfstate_backup.write_text('{"version": 4}', encoding="utf-8")

    lock_file = provider_root / ".terraform.lock.hcl"
    lock_file.write_text("# lock", encoding="utf-8")

    plugin_marker = provider_root / ".terraform" / "plugins" / "marker.txt"
    plugin_marker.parent.mkdir(parents=True, exist_ok=True)
    plugin_marker.write_text("plugin", encoding="utf-8")

    state = {
        "config": {
            "output": {
                "base_dir": str(output_dir),
                "overwrite": True,
                "write_review_notes": False,
                "write_quarantine_report": False,
            }
        },
        "hcl_output": {
            "aws-migration/main.tf": 'resource "aws_vpc" "main" { cidr_block = "10.0.0.0/16" }'
        },
        "messages": [],
        "quarantine_queue": [],
        "status": "running",
    }

    reporter(state)  # type: ignore[arg-type]

    assert not stale_tf.exists()
    assert (provider_root / "main.tf").exists()
    assert tfstate.exists()
    assert tfstate_backup.exists()
    assert lock_file.exists()
    assert plugin_marker.exists()
