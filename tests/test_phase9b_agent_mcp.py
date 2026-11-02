"""Phase 9B: deterministic AutoGen/MCP integration contract tests.

Run: python -m pytest tests/test_phase9b_agent_mcp.py -v
No OpenAI calls, MCP subprocesses, or database mutations.
"""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import backend.agents.team as team_module


class FakeAgent:
    def __init__(self, name, model_client, tools=None, system_message=""):
        self.name = name
        self.tools = {tool.__name__: tool for tool in (tools or [])}
        self.system_message = system_message


class FakeTeam:
    def __init__(self, agents, termination_condition):
        self.agents = {agent.name: agent for agent in agents}


@pytest.fixture
def setup_team(monkeypatch):
    fake_client = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(team_module, "OpenAIChatCompletionClient", lambda **kwargs: fake_client)
    monkeypatch.setattr(team_module, "AssistantAgent", FakeAgent)
    monkeypatch.setattr(team_module, "RoundRobinGroupChat", FakeTeam)
    # Termination is not under test; avoid constructing real conditions.
    monkeypatch.setattr(team_module, "TextMentionTermination", lambda *args: 1)
    monkeypatch.setattr(team_module, "MaxMessageTermination", lambda *args: 2)

    evidence = {
        "id": "POLICY-1",
        "visibility": "customer",
        "source": "approved_policy",
        "text": "Policy evidence",
    }
    retriever = SimpleNamespace(search=Mock(return_value=[evidence]))
    persist = Mock(return_value="PERSISTED")
    team, client = team_module.make_team(retriever, "job-1", "ticket-1", persist)
    return team.agents, retriever, persist, fake_client


def run(coro):
    """Run an agent coroutine on its own event loop, isolated from pytest."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=30)


def test_tools_registered_and_policy_search_preserved(setup_team):
    agents, retriever, _, _ = setup_team
    assert list(agents) == ["QueryAgent", "KnowledgeAgent", "ResolutionAgent", "LogAgent"]
    assert set(agents["KnowledgeAgent"].tools) == {
        "policy_search", "get_product_information", "check_refund_review_threshold","public_web_research",
    }
    assert "persist_outcome" in agents["LogAgent"].tools
    result = json.loads(run(agents["KnowledgeAgent"].tools["policy_search"]("refund policy")))
    assert result[0]["id"] == "POLICY-1"
    retriever.search.assert_called_once_with("refund policy")


def test_agent_calls_allowlisted_mcp_tool(setup_team, monkeypatch):
    agents, _, _, _ = setup_team
    call = AsyncMock(return_value={"ok": True, "data": {"found": True, "demo_data": True}})
    monkeypatch.setattr(team_module, "call_novacart_tool", call)
    result = json.loads(run(agents["KnowledgeAgent"].tools["get_product_information"]("MOUSE-01")))
    assert result["data"]["demo_data"] is True
    call.assert_awaited_once_with("get_product_information", {"product_id": "MOUSE-01"})


@pytest.mark.parametrize("amount,review", [(499, False), (500, True), (501, True)])
def test_agent_threshold_mcp_call(setup_team, monkeypatch, amount, review):
    agents, _, _, _ = setup_team
    call = AsyncMock(return_value={"ok": True, "data": {
        "requires_human_review": review,
        "decision": "NOT_MADE",
        "execution_status": "NOT_CONFIGURED",
    }})
    monkeypatch.setattr(team_module, "call_novacart_tool", call)
    result = json.loads(run(agents["KnowledgeAgent"].tools["check_refund_review_threshold"](amount)))
    assert result["data"]["requires_human_review"] is review
    assert result["data"]["execution_status"] == "NOT_CONFIGURED"
    call.assert_awaited_once_with("check_refund_review_threshold", {"amount_inr": amount})


@pytest.mark.parametrize("amount", [-1, 1_000_001, float("nan"), float("inf"), True])
def test_invalid_amount_never_reaches_mcp(setup_team, monkeypatch, amount):
    agents, _, _, _ = setup_team
    call = AsyncMock()
    monkeypatch.setattr(team_module, "call_novacart_tool", call)
    result = json.loads(run(agents["KnowledgeAgent"].tools["check_refund_review_threshold"](amount)))
    assert result == {"ok": False, "error": "INVALID_ARGUMENTS"}
    call.assert_not_awaited()


def test_mcp_failure_is_returned_as_failure_not_fabricated_data(setup_team, monkeypatch):
    agents, _, _, _ = setup_team
    monkeypatch.setattr(team_module, "call_novacart_tool", AsyncMock(return_value={
        "ok": False, "error": "MCP_TIMEOUT"
    }))
    result = json.loads(run(agents["KnowledgeAgent"].tools["get_product_information"]("MOUSE-01")))
    assert result == {"ok": False, "error": "MCP_TIMEOUT"}


def test_mcp_result_cannot_be_used_as_policy_citation(setup_team, monkeypatch):
    agents, _, persist, _ = setup_team
    monkeypatch.setattr(team_module, "call_novacart_tool", AsyncMock(return_value={
        "ok": True, "data": {"id": "MCP-FAKE-POLICY", "found": True}
    }))
    run(agents["KnowledgeAgent"].tools["get_product_information"]("MOUSE-01"))
    result = run(agents["LogAgent"].tools["persist_outcome"](
        "summary", "Unsafe response", "MCP-FAKE-POLICY", False
    ))
    assert result == "LOGGED"
    args = persist.call_args.args
    assert args[4] == []  # Reject untrusted MCP ID before persistence
    assert args[5] is True
    assert "verified evidence is insufficient" in args[3]
    assert args[6] == {}


def test_real_policy_citation_preserved(setup_team):
    agents, _, persist, _ = setup_team
    run(agents["KnowledgeAgent"].tools["policy_search"]("policy"))
    result = run(agents["LogAgent"].tools["persist_outcome"](
        "summary", "Supported response", "POLICY-1", False
    ))
    assert result == "LOGGED"
    args = persist.call_args.args
    assert args[4] == ["POLICY-1"]
    assert args[5] is False
    assert "POLICY-1" in args[6]
