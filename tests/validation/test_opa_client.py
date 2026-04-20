from __future__ import annotations

from pathlib import Path

import validation.opa_client as opa_module
from validation.opa_client import OPAClient, _extract_deny_messages


def test_extract_deny_messages_returns_empty_when_no_deny_present() -> None:
    payload = {
        "result": [
            {
                "expressions": [
                    {
                        "value": {
                            "vmwaremigration": {
                                "requires_metadata_block": False,
                                "some_other_flag": True,
                            }
                        }
                    }
                ]
            }
        ]
    }

    assert _extract_deny_messages(payload) == []


def test_extract_deny_messages_collects_all_deny_strings() -> None:
    payload = {
        "result": [
            {
                "expressions": [
                    {
                        "value": {
                            "vmwaremigration": {
                                "deny": [
                                    "tags_required: Environment tag missing",
                                    "no_open_ingress: open ingress is forbidden",
                                ]
                            }
                        }
                    }
                ]
            }
        ]
    }

    assert _extract_deny_messages(payload) == [
        "tags_required: Environment tag missing",
        "no_open_ingress: open ingress is forbidden",
    ]


def test_opa_binary_retries_without_v0_compatible_flag(monkeypatch, tmp_path: Path) -> None:
    policies_dir = tmp_path / "policies"
    policies_dir.mkdir(parents=True, exist_ok=True)
    (policies_dir / "dummy.rego").write_text("package vmwaremigration\n", encoding="utf-8")

    calls: list[list[str]] = []

    def _fake_run_command(command: list[str], timeout_seconds: int) -> opa_module._CommandResult:
        calls.append(command)
        if "--v0-compatible" in command:
            return opa_module._CommandResult(
                returncode=1,
                stdout="",
                stderr="unknown flag: --v0-compatible",
            )

        return opa_module._CommandResult(
            returncode=0,
            stdout='{"result":[{"expressions":[{"value":{"vmwaremigration":{"deny":[]}}}]}]}',
            stderr="",
        )

    monkeypatch.setattr(opa_module, "_run_command", _fake_run_command)

    client = OPAClient(mode="binary", opa_bin="opa", timeout_seconds=30)
    result = client.evaluate(input_data={"generated_files": {}}, policies_dir=policies_dir)

    assert result.passed is True
    assert len(calls) == 2
    assert "--v0-compatible" in calls[0]
    assert "--v0-compatible" not in calls[1]
