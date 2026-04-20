from __future__ import annotations

from agent.llm_client import LLMClient


def test_llm_client_reuses_cached_instance_for_same_effective_config(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    LLMClient.clear_cache()

    config = {
        "llm": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-5",
            "temperature_hcl": 0.0,
            "temperature_report": 0.2,
        }
    }

    first = LLMClient(config=config)
    second = LLMClient(config=config)

    assert first is second


def test_llm_client_creates_distinct_instances_for_distinct_configs(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    LLMClient.clear_cache()

    config = {
        "llm": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-5",
        }
    }

    first = LLMClient(config=config)
    second = LLMClient(config=config, model_override="claude-3-5-sonnet-latest")

    assert first is not second
