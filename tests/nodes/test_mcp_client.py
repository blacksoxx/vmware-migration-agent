from __future__ import annotations

import pytest

from agent.nodes.mcp_client import MCPRetrievalError, _MCPStdioClient, mcp_client
from cim.schema import (
    CanonicalInfrastructureModel,
    ComputeUnit,
    DistributedSwitch,
    MigrationStatus,
    NetworkTopology,
    PortGroup,
    TargetProvider,
)


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
        "messages": [],
        "status": "pending",
        "config": {
            "mcp": {
                "enabled": True,
                "tool_timeout_seconds": 5,
                "terraform": {
                    "image": "hashicorp/terraform-mcp-server:latest",
                    "toolsets": "registry",
                },
            }
        },
    }


def test_mcp_client_populates_mcp_context(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_call_tool(self: _MCPStdioClient, tool_name: str, arguments: dict[str, object]) -> object:
        if tool_name == "search_providers":
            service_slug = str(arguments.get("service_slug", "unknown"))
            return {"provider_doc_id": f"doc::{service_slug}"}
        if tool_name == "get_provider_details":
            doc_id = str(arguments.get("provider_doc_id", "unknown"))
            return {"documents": [{"content": f"details for {doc_id}"}]}
        raise AssertionError(f"unexpected tool call: {tool_name}")

    monkeypatch.setattr(_MCPStdioClient, "call_tool", _fake_call_tool)

    state = _base_state()
    updated = mcp_client(state)  # type: ignore[arg-type]

    assert updated["status"] == "running"
    assert "mcp_context" in updated
    assert "mcp://terraform-mcp-server/registry" in str(updated["mcp_context"])
    assert "aws_instance" in str(updated["mcp_context"])


def test_mcp_client_raises_when_toolsets_not_registry() -> None:
    state = _base_state()
    state["config"]["mcp"]["terraform"]["toolsets"] = "workspace"  # type: ignore[index]

    with pytest.raises(MCPRetrievalError):
        mcp_client(state)  # type: ignore[arg-type]


def test_mcp_client_calls_registry_tools_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _fake_stdio_call(self: _MCPStdioClient, tool_name: str, arguments: dict[str, object]) -> object:
        calls.append(tool_name)
        if tool_name == "search_providers":
            service_slug = str(arguments.get("service_slug", "unknown"))
            return {"provider_doc_id": f"doc::{service_slug}"}
        if tool_name == "get_provider_details":
            doc_id = str(arguments.get("provider_doc_id", "unknown"))
            return {"content": f"content for {doc_id}"}
        raise AssertionError(f"unexpected tool call: {tool_name}")

    monkeypatch.setattr(_MCPStdioClient, "call_tool", _fake_stdio_call)

    state = _base_state()
    updated = mcp_client(state)  # type: ignore[arg-type]

    assert "search_providers" in calls
    assert "get_provider_details" in calls
    assert "searchProviderDocs" not in calls
    assert "RunCheckovScan" not in calls
    assert "mcp://terraform-mcp-server/registry" in str(updated["mcp_context"])


def test_mcp_client_skips_empty_workload(monkeypatch: pytest.MonkeyPatch) -> None:
    def _should_not_call_tool(self: _MCPStdioClient, tool_name: str, arguments: dict[str, object]) -> object:
        raise AssertionError("MCP tool calls should be skipped when CIM workload is empty")

    monkeypatch.setattr(_MCPStdioClient, "call_tool", _should_not_call_tool)

    state = _base_state()
    state["sized_cim"] = _empty_cim()  # type: ignore[index]

    updated = mcp_client(state)  # type: ignore[arg-type]

    assert updated["status"] == "running"
    assert updated["mcp_context"] == ""
