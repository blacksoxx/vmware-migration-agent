from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph

from agent.nodes.blocker_parser import blocker_parser
from agent.nodes.cim_mapper import cim_mapper
from agent.nodes.enricher import enricher
from agent.nodes.hcl_generator import hcl_generator
from agent.nodes.ingest import ingest
from agent.nodes.mcp_client import mcp_client
from agent.nodes.reporter import reporter
from agent.nodes.sizer import sizer
from agent.nodes.validator import validator
from agent.state import MigrationState


def _validator_route(state: MigrationState) -> Literal["reporter", "__end__"]:
    validation_result = state.get("validation_result", {})
    passed = bool(validation_result.get("passed")) if isinstance(validation_result, dict) else False

    if passed:
        return "reporter"

    return "__end__"


def build_graph() -> StateGraph:
    """Create and compile the LangGraph for the vmware-migration-agent pipeline."""
    workflow = StateGraph(MigrationState)

    workflow.add_node("ingest", ingest)
    workflow.add_node("blocker_parser", blocker_parser)
    workflow.add_node("enricher", enricher)
    workflow.add_node("cim_mapper", cim_mapper)
    workflow.add_node("sizer", sizer)
    workflow.add_node("mcp_client", mcp_client)
    workflow.add_node("hcl_generator", hcl_generator)
    workflow.add_node("validator", validator)
    workflow.add_node("reporter", reporter)

    workflow.add_edge(START, "ingest")
    workflow.add_edge("ingest", "blocker_parser")
    workflow.add_edge("blocker_parser", "enricher")
    workflow.add_edge("enricher", "cim_mapper")
    workflow.add_edge("cim_mapper", "sizer")
    workflow.add_edge("sizer", "mcp_client")
    workflow.add_edge("mcp_client", "hcl_generator")
    workflow.add_edge("hcl_generator", "validator")

    workflow.add_conditional_edges(
        "validator",
        _validator_route,
        {
            "reporter": "reporter",
            "__end__": END,
        },
    )

    workflow.add_edge("reporter", END)

    return workflow.compile()
