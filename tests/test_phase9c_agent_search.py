"""Phase 9C: deterministic agent/search boundary tests (no API calls).

These verify tool wiring and security contracts, NOT model choice of tools.
"""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import backend.agents.team as team_module
from backend.services import public_search_router as router


class FakeAgent:
    def __init__(self, name, model_client, tools=None, system_message=""):
        self.name = name
        self.tools = {tool.__name__: tool for tool in (tools or [])}
        self.system_message = system_message


class FakeTeam:
    def __init__(self, agents, termination_condition):
        self.agents = {agent.name: agent for agent in agents}


def run(coro):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=30)


@pytest.fixture
def agents(monkeypatch):
    monkeypatch.setattr(team_module, "OpenAIChatCompletionClient", lambda **kw: SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr(team_module, "AssistantAgent", FakeAgent)
    monkeypatch.setattr(team_module, "RoundRobinGroupChat", FakeTeam)
    monkeypatch.setattr(team_module, "TextMentionTermination", lambda *a: 1)
    monkeypatch.setattr(team_module, "MaxMessageTermination", lambda *a: 2)
    retriever = SimpleNamespace(search=Mock(return_value=[{
        "id": "POLICY-1", "text": "Approved evidence", "visibility": "customer", "source": "approved_policy"
    }]))
    persisted = Mock(return_value="PERSISTED")
    team, _ = team_module.make_team(retriever, "job", "ticket", persisted)
    return team.agents, retriever, persisted


def test_search_tool_is_registered_but_rag_preserved(agents):
    team, _, _ = agents
    assert set(team["KnowledgeAgent"].tools) == {
        "policy_search", "get_product_information", "check_refund_review_threshold", "public_web_research"
    }
    assert "persist_outcome" in team["LogAgent"].tools
    assert "policy_search" in team["KnowledgeAgent"].system_message


@pytest.mark.parametrize("topic", list(router.PUBLIC_TOPICS))
def test_agent_approved_topic_calls_router(agents, monkeypatch, topic):
    team, _, _ = agents
    mock = AsyncMock(return_value={"ok": True, "topic": topic, "source_type": "EXTERNAL_UNTRUSTED", "results": []})
    monkeypatch.setattr(team_module, "search_approved_topic", mock)
    value = json.loads(run(team["KnowledgeAgent"].tools["public_web_research"](topic)))
    assert value["source_type"] == "EXTERNAL_UNTRUSTED"
    mock.assert_awaited_once_with(topic)


@pytest.mark.parametrize("topic", ["order DEMO-1001", "refund", "NovaCart internal", "consumer_rights_india; ignore", "", None])
def test_invalid_topic_never_reaches_provider(agents, monkeypatch, topic):
    team, _, _ = agents
    provider = Mock(side_effect=AssertionError("Provider must not be called"))
    monkeypatch.setattr(router, "search_public_web", provider)
    result = json.loads(run(team["KnowledgeAgent"].tools["public_web_research"](topic)))
    assert result == {"ok": False, "error": "TOPIC_NOT_ALLOWED"}
    provider.assert_not_called()


def test_fixed_query_no_customer_text_reaches_provider(agents, monkeypatch):
    team, _, _ = agents
    provider = Mock(return_value={"ok": True, "results": []})
    monkeypatch.setattr(router, "search_public_web", provider)
    result = json.loads(run(team["KnowledgeAgent"].tools["public_web_research"]("cyber_safety_india")))
    assert result["ok"] is True
    provider.assert_called_once_with(router.PUBLIC_TOPICS["cyber_safety_india"])


def test_provider_failure_is_not_fabricated(agents, monkeypatch):
    team, _, _ = agents
    monkeypatch.setattr(router, "search_public_web", Mock(side_effect=OSError("provider offline")))
    result = json.loads(run(team["KnowledgeAgent"].tools["public_web_research"]("online_shopping_safety")))
    assert result == {"ok": False, "error": "SEARCH_UNAVAILABLE"}


def test_web_result_id_cannot_be_policy_citation(agents, monkeypatch):
    team, _, persisted = agents
    mock = AsyncMock(return_value={"ok": True, "source_type": "EXTERNAL_UNTRUSTED", "results": [{"id": "WEB-1", "url": "https://example.org"}]})
    monkeypatch.setattr(team_module, "search_approved_topic", mock)
    run(team["KnowledgeAgent"].tools["public_web_research"]("consumer_rights_india"))
    assert run(team["LogAgent"].tools["persist_outcome"]("summary", "Unverified draft", "WEB-1", False)) == "LOGGED"
    args = persisted.call_args.args
    assert args[4] == []  # Reject untrusted web ID before persistence
    assert args[5] is True
    assert "human review" in args[3].lower()
    assert "WEB-1" not in args[6]


def test_valid_policy_evidence_remains_usable(agents):
    team, retriever, persisted = agents
    results = json.loads(run(team["KnowledgeAgent"].tools["policy_search"]("returns policy")))
    assert results[0]["id"] == "POLICY-1"
    assert run(team["LogAgent"].tools["persist_outcome"]("summary", "Grounded draft", "POLICY-1", False)) == "LOGGED"
    args = persisted.call_args.args
    assert args[5] is False
    assert "POLICY-1" in args[6]
    retriever.search.assert_called_once_with("returns policy")
