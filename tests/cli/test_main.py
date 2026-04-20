from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

import cli.main as main_module
from cli.main import cli


def test_terragen_requires_input_option() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "terragen",
            "--cloud",
            "aws",
            "--llm-provider",
            "openai",
            "--environment",
            "dev",
            "--owner",
            "platform",
        ],
    )

    assert result.exit_code != 0
    assert "--input" in result.output


def test_terragen_accepts_custom_json_input(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class _FakeGraph:
        def invoke(self, state: dict[str, object]) -> dict[str, object]:
            captured.update(state)
            return {"status": "succeeded"}

    def _fake_build_graph() -> _FakeGraph:
        return _FakeGraph()

    def _fake_load_config(config_path: str | Path = "config.yaml") -> dict[str, object]:
        return {
            "llm": {"provider": "openai", "model": "gpt-4o"},
            "output": {"base_dir": "output"},
            "required_tags": {},
            "validation": {},
        }

    monkeypatch.setattr("agent.graph.build_graph", _fake_build_graph)
    monkeypatch.setattr("agent.config_loader.load_config", _fake_load_config)

    input_path = tmp_path / "sample-any-name.json"
    payload = {"vcenter": "vc01", "compute_units": []}
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "terragen",
            "--input",
            str(input_path),
            "--cloud",
            "aws",
            "--llm-provider",
            "openai",
            "--environment",
            "dev",
            "--owner",
            "platform",
        ],
    )

    assert result.exit_code == 0
    assert captured["discovery_data"] == payload
    assert captured["mcp_context"] == ""


def test_mcp_check_success(monkeypatch) -> None:
    def _fake_load_config(config_path: str | Path = "config.yaml") -> dict[str, object]:
        return {
            "target_cloud": "aws",
            "llm": {
                "provider": "openai",
                "temperature_hcl": 0.0,
                "temperature_report": 0.2,
            },
            "mcp": {
                "enabled": True,
                "tool_timeout_seconds": 30,
                "terraform": {
                    "image": "hashicorp/terraform-mcp-server:latest",
                    "toolsets": "registry",
                },
            },
        }

    def _fake_probe(*args, **kwargs):
        return {"server": args[0] if args else kwargs.get("server_name"), "ok": True, "detail": "reachable"}

    monkeypatch.setattr("agent.config_loader.load_config", _fake_load_config)
    monkeypatch.setattr(main_module, "_probe_mcp_server", _fake_probe)

    runner = CliRunner()
    result = runner.invoke(cli, ["mcp-check"])

    assert result.exit_code == 0
    assert "MCP connectivity check" in result.output
    assert "[OK]" in result.output


def test_mcp_check_failure_returns_non_zero(monkeypatch) -> None:
    def _fake_load_config(config_path: str | Path = "config.yaml") -> dict[str, object]:
        return {
            "target_cloud": "aws",
            "llm": {
                "provider": "openai",
                "temperature_hcl": 0.0,
                "temperature_report": 0.2,
            },
            "mcp": {
                "enabled": True,
                "tool_timeout_seconds": 30,
                "terraform": {
                    "image": "hashicorp/terraform-mcp-server:latest",
                    "toolsets": "registry",
                },
            },
        }

    def _fake_probe(*args, **kwargs):
        server_name = args[0] if args else kwargs.get("server_name")
        return {
            "server": server_name,
            "ok": server_name != "terraform",
            "detail": "reachable" if server_name != "terraform" else "connection failed",
        }

    monkeypatch.setattr("agent.config_loader.load_config", _fake_load_config)
    monkeypatch.setattr(main_module, "_probe_mcp_server", _fake_probe)

    runner = CliRunner()
    result = runner.invoke(cli, ["mcp-check"])

    assert result.exit_code != 0
    assert "terraform [FAIL]" in result.output


def test_probe_mcp_server_stdio_transport(monkeypatch) -> None:
    def _fake_stdio_call(self, tool_name: str, arguments: dict[str, object]) -> object:
        if tool_name in {"get_latest_provider_version", "getLatestProviderVersion"}:
            return {"version": "6.41.0"}
        raise AssertionError(f"unexpected tool: {tool_name}")

    monkeypatch.setattr(main_module._MCPStdioClient, "call_tool", _fake_stdio_call)

    result = main_module._probe_mcp_server(
        server_name="terraform",
        servers_cfg={
            "terraform": {
                "command": "docker",
                "args": [
                    "run",
                    "-i",
                    "--rm",
                    "hashicorp/terraform-mcp-server:latest",
                    "stdio",
                    "--toolsets",
                    "registry",
                ],
                "provider": "hashicorp/aws",
                "resource": "aws_instance",
            }
        },
        top_k=1,
        timeout_seconds=5,
    )

    assert result["ok"] is True


def test_mcp_check_servers_filter(monkeypatch) -> None:
    def _fake_load_config(config_path: str | Path = "config.yaml") -> dict[str, object]:
        return {
            "target_cloud": "aws",
            "llm": {
                "provider": "openai",
                "temperature_hcl": 0.0,
                "temperature_report": 0.2,
            },
            "mcp": {
                "enabled": True,
                "tool_timeout_seconds": 30,
                "terraform": {
                    "image": "hashicorp/terraform-mcp-server:latest",
                    "toolsets": "registry",
                },
            },
        }

    def _fake_probe(*args, **kwargs):
        return {
            "server": args[0] if args else kwargs.get("server_name"),
            "ok": True,
            "detail": "reachable",
        }

    monkeypatch.setattr("agent.config_loader.load_config", _fake_load_config)
    monkeypatch.setattr(main_module, "_probe_mcp_server", _fake_probe)

    runner = CliRunner()
    result = runner.invoke(cli, ["mcp-check", "--servers", "terraform"])

    assert result.exit_code == 0
    assert "terraform [OK]" in result.output


def test_terragen_skip_opa_disables_only_opa(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class _FakeGraph:
        def invoke(self, state: dict[str, object]) -> dict[str, object]:
            captured.update(state)
            return {"status": "succeeded"}

    def _fake_build_graph() -> _FakeGraph:
        return _FakeGraph()

    def _fake_load_config(config_path: str | Path = "config.yaml") -> dict[str, object]:
        return {
            "llm": {"provider": "openai", "model": "gpt-4o"},
            "output": {"base_dir": "output"},
            "required_tags": {},
            "validation": {
                "run_terraform_validate": True,
                "run_tflint": True,
                "run_opa": True,
            },
        }

    monkeypatch.setattr("agent.graph.build_graph", _fake_build_graph)
    monkeypatch.setattr("agent.config_loader.load_config", _fake_load_config)

    input_path = tmp_path / "sample-any-name.json"
    input_path.write_text(json.dumps({"virtual_machines": []}), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "terragen",
            "--input",
            str(input_path),
            "--cloud",
            "aws",
            "--llm-provider",
            "openai",
            "--environment",
            "dev",
            "--owner",
            "platform",
            "--skip-opa",
        ],
    )

    assert result.exit_code == 0
    validation_cfg = captured["config"]["validation"]
    assert validation_cfg["run_terraform_validate"] is True
    assert validation_cfg["run_tflint"] is True
    assert validation_cfg["run_opa"] is False
