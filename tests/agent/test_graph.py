from __future__ import annotations

from agent.graph import _validator_route, build_graph


def test_validator_route_passed_goes_to_reporter() -> None:
    route = _validator_route({"validation_result": {"passed": True}})  # type: ignore[arg-type]
    assert route == "reporter"


def test_validator_route_failed_goes_to_end() -> None:
    route = _validator_route({"validation_result": {"passed": False}})  # type: ignore[arg-type]
    assert route == "__end__"


def test_build_graph_compiles() -> None:
    compiled = build_graph()
    assert compiled is not None
