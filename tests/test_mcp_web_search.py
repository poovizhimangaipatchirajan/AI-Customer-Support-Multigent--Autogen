"""Consolidated mcp web search tests.

Merged without behaviour changes from:
- test_mcp_adapter.py: Tests for the FixFlow AI MCP adapter.
- test_mcp_protocol.py: regression tests
- test_phase9b_agent_mcp.py: Phase 9B: deterministic AutoGen/MCP integration contract tests.
- test_phase9b_mcp_resilience.py: Phase 9B: MCP adapter failure isolation (no network, model, or real subprocess).
- test_phase9c_agent_search.py: Phase 9C: deterministic agent/search boundary tests (no API calls).
- test_phase9c_public_search_router.py: Offline tests for strict public-search routing (no provider/API key needed).
- test_serper_adapter.py: Offline, zero-credit Serper integration regression tests.
- test_web_search_adapter.py: Offline Phase 9C tests; no search subscription or network needed.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from backend.services.mcp_adapter import call_novacart_tool
import json
import sys
from autogen_ext.tools.mcp import McpWorkbench, StdioServerParams
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
import backend.agents.team as team_module
import backend.services.mcp_adapter as adapter
from backend.services import public_search_router as router
from unittest.mock import patch
from backend.services.public_search_router import PUBLIC_TOPICS, allowed_public_topics, search_approved_topic
from backend.services import web_search_adapter as search


# ======================================================================
# From test_mcp_adapter.py
# ======================================================================

def run_async(coroutine):
    """
    Execute an async operation in a separate thread
    to avoid conflicts with an existing event loop.
    """
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            lambda: asyncio.run(coroutine)
        )
        return future.result(timeout=30)


def test_mcp_allowed_tool():
    result = run_async(
        call_novacart_tool(
            "check_refund_review_threshold",
            {"amount_inr": 500},
        )
    )

    assert result["ok"] is True, result
    assert result["data"]["requires_human_review"] is True
    assert result["data"]["decision"] == "NOT_MADE"
    assert result["data"]["execution_status"] == "NOT_CONFIGURED"


def test_mcp_below_threshold():
    result = run_async(
        call_novacart_tool(
            "check_refund_review_threshold",
            {"amount_inr": 499},
        )
    )

    assert result["ok"] is True, result
    assert result["data"]["requires_human_review"] is False


def test_mcp_above_threshold():
    result = run_async(
        call_novacart_tool(
            "check_refund_review_threshold",
            {"amount_inr": 501},
        )
    )

    assert result["ok"] is True, result
    assert result["data"]["requires_human_review"] is True


def test_mcp_product_information():
    result = run_async(
        call_novacart_tool(
            "get_product_information",
            {"product_id": "MOUSE-01"},
        )
    )

    assert result["ok"] is True, result
    assert result["data"]["found"] is True
    assert result["data"]["demo_data"] is True


def test_mcp_disallowed_tool():
    result = run_async(
        call_novacart_tool(
            "approve_refund",
            {"amount_inr": 500},
        )
    )

    assert result == {
        "ok": False,
        "error": "TOOL_NOT_ALLOWED",
    }


def test_mcp_invalid_arguments():
    result = run_async(
        call_novacart_tool(
            "get_product_information",
            None,
        )
    )

    assert result == {
        "ok": False,
        "error": "INVALID_ARGUMENTS",
    }


# ======================================================================
# From test_mcp_protocol.py
# ======================================================================

def extract_result(response):
    if isinstance(response, dict):
        if response.get("isError") or response.get("is_error"):
            raise AssertionError(f"MCP error: {response}")

        if "structuredContent" in response:
            return extract_result(response["structuredContent"])

        if "result" in response:
            return extract_result(response["result"])

        if "content" in response:
            return extract_result(response["content"])

        return response

    if isinstance(response, list):
        assert response, "Empty MCP response"
        return extract_result(response[0])

    if getattr(response, "is_error", False):
        raise AssertionError(f"MCP error: {response}")

    if hasattr(response, "result"):
        return extract_result(response.result)

    if hasattr(response, "content"):
        return extract_result(response.content)

    if hasattr(response, "text"):
        return extract_result(response.text)

    if isinstance(response, str):
        return json.loads(response)

    raise AssertionError(f"Unexpected MCP response: {response!r}")


async def verify_mcp():
    params = StdioServerParams(
        command=sys.executable,
        args=["-m", "backend.mcp_server.server"],
    )

    async with McpWorkbench(params) as workbench:
        tools = await workbench.list_tools()

        names = {
            tool["name"] if isinstance(tool, dict) else tool.name
            for tool in tools
        }

        assert "get_product_information" in names
        assert "check_refund_review_threshold" in names

        for amount, expected in (
            (499, False),
            (500, True),
            (501, True),
        ):
            response = await workbench.call_tool(
                "check_refund_review_threshold",
                {"amount_inr": amount},
            )

            result = extract_result(response)

            assert result["requires_human_review"] is expected
            assert result["decision"] == "NOT_MADE"
            assert result["execution_status"] == "NOT_CONFIGURED"


def test_mcp_tool_discovery_and_invocation():
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: asyncio.run(verify_mcp()))
        future.result(timeout=30)


# ======================================================================
# From test_phase9b_agent_mcp.py
# ======================================================================

class FakeAgent:
    def __init__(self, name, model_client, tools=None, system_message="", **_options):
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
    # Production order: ResolutionAgent records the structured handoff first.
    assert run(agents["ResolutionAgent"].tools["record_resolution_handoff"](
        "Supported response", "POLICY-1", False)) == "HANDOFF_RECORDED"
    result = run(agents["LogAgent"].tools["persist_outcome"](
        "summary", "Supported response", "POLICY-1", False
    ))
    assert result == "LOGGED"
    args = persist.call_args.args
    assert args[4] == ["POLICY-1"]
    assert args[5] is False
    assert "POLICY-1" in args[6]


# ======================================================================
# From test_phase9b_mcp_resilience.py
# ======================================================================

def run_phase9b_mcp_resilience(coro):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=10)


class FakeWorkbench:
    def __init__(self, params):
        self.params = params

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def list_tools(self):
        return [{"name": "get_product_information"}]

    async def call_tool(self, name, arguments):
        return SimpleNamespace(result=[SimpleNamespace(text='{"found":true,"demo_data":true}')], is_error=False)


def test_mcp_success_with_mock_workbench(monkeypatch):
    monkeypatch.setattr(adapter, "McpWorkbench", FakeWorkbench)
    result = run_phase9b_mcp_resilience(adapter.call_novacart_tool("get_product_information", {"product_id": "MOUSE-01"}))
    assert result == {"ok": True, "data": {"found": True, "demo_data": True}}


def test_mcp_server_start_failure_is_safe(monkeypatch):
    class CrashingWorkbench(FakeWorkbench):
        async def __aenter__(self):
            raise OSError("simulated subprocess startup failure")

    monkeypatch.setattr(adapter, "McpWorkbench", CrashingWorkbench)
    result = run_phase9b_mcp_resilience(adapter.call_novacart_tool("get_product_information", {"product_id": "MOUSE-01"}))
    assert result["ok"] is False
    assert result["error"] == "MCP_UNAVAILABLE"
    assert "data" not in result
    assert "simulated" not in str(result)


def test_mcp_timeout_is_safe(monkeypatch):
    class HangingWorkbench(FakeWorkbench):
        async def list_tools(self):
            await asyncio.sleep(0.1)
            return await super().list_tools()

    monkeypatch.setattr(adapter, "McpWorkbench", HangingWorkbench)
    monkeypatch.setattr(adapter, "MCP_TIMEOUT_SECONDS", 0.01)
    result = run_phase9b_mcp_resilience(adapter.call_novacart_tool("get_product_information", {"product_id": "MOUSE-01"}))
    assert result == {"ok": False, "error": "MCP_TIMEOUT"}


def test_malformed_mcp_response_is_not_treated_as_success(monkeypatch):
    class MalformedWorkbench(FakeWorkbench):
        async def call_tool(self, name, arguments):
            return SimpleNamespace(result=[SimpleNamespace(text="not valid JSON")], is_error=False)

    monkeypatch.setattr(adapter, "McpWorkbench", MalformedWorkbench)
    result = run_phase9b_mcp_resilience(adapter.call_novacart_tool("get_product_information", {"product_id": "MOUSE-01"}))
    assert result["ok"] is False
    assert result["error"] == "MCP_UNAVAILABLE"
    assert "data" not in result


def test_mcp_tool_error_is_not_treated_as_success(monkeypatch):
    class ErrorWorkbench(FakeWorkbench):
        async def call_tool(self, name, arguments):
            return SimpleNamespace(result=[SimpleNamespace(text='{"found":true}')], is_error=True)

    monkeypatch.setattr(adapter, "McpWorkbench", ErrorWorkbench)
    result = run_phase9b_mcp_resilience(adapter.call_novacart_tool("get_product_information", {"product_id": "MOUSE-01"}))
    assert result["ok"] is False
    assert "data" not in result


def test_unavailable_tool_is_rejected(monkeypatch):
    class MissingToolWorkbench(FakeWorkbench):
        async def list_tools(self):
            return []

    monkeypatch.setattr(adapter, "McpWorkbench", MissingToolWorkbench)
    result = run_phase9b_mcp_resilience(adapter.call_novacart_tool("get_product_information", {"product_id": "MOUSE-01"}))
    assert result == {"ok": False, "error": "TOOL_UNAVAILABLE"}


def test_unapproved_tool_does_not_launch_mcp(monkeypatch):
    class ShouldNotLaunch:
        def __init__(self, *args, **kwargs):
            raise AssertionError("MCP should not be launched")

    monkeypatch.setattr(adapter, "McpWorkbench", ShouldNotLaunch)
    result = run_phase9b_mcp_resilience(adapter.call_novacart_tool("approve_refund", {"amount_inr": 500}))
    assert result == {"ok": False, "error": "TOOL_NOT_ALLOWED"}


# ======================================================================
# From test_phase9c_agent_search.py
# ======================================================================

class FakeAgent_phase9c_agent_search:
    def __init__(self, name, model_client, tools=None, system_message="", **_options):
        self.name = name
        self.tools = {tool.__name__: tool for tool in (tools or [])}
        self.system_message = system_message


class FakeTeam_phase9c_agent_search:
    def __init__(self, agents, termination_condition):
        self.agents = {agent.name: agent for agent in agents}


def run_phase9c_agent_search(coro):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=30)


@pytest.fixture
def agents(monkeypatch):
    monkeypatch.setattr(team_module, "OpenAIChatCompletionClient", lambda **kw: SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr(team_module, "AssistantAgent", FakeAgent_phase9c_agent_search)
    monkeypatch.setattr(team_module, "RoundRobinGroupChat", FakeTeam_phase9c_agent_search)
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
    value = json.loads(run_phase9c_agent_search(team["KnowledgeAgent"].tools["public_web_research"](topic)))
    assert value["source_type"] == "EXTERNAL_UNTRUSTED"
    mock.assert_awaited_once_with(topic)


@pytest.mark.parametrize("topic", ["order DEMO-1001", "refund", "NovaCart internal", "consumer_rights_india; ignore", "", None])
def test_invalid_topic_never_reaches_provider(agents, monkeypatch, topic):
    team, _, _ = agents
    provider = Mock(side_effect=AssertionError("Provider must not be called"))
    monkeypatch.setattr(router, "search_public_web", provider)
    result = json.loads(run_phase9c_agent_search(team["KnowledgeAgent"].tools["public_web_research"](topic)))
    assert result == {"ok": False, "error": "TOPIC_NOT_ALLOWED"}
    provider.assert_not_called()


def test_fixed_query_no_customer_text_reaches_provider(agents, monkeypatch):
    team, _, _ = agents
    provider = Mock(return_value={"ok": True, "results": []})
    monkeypatch.setattr(router, "search_public_web", provider)
    result = json.loads(run_phase9c_agent_search(team["KnowledgeAgent"].tools["public_web_research"]("cyber_safety_india")))
    assert result["ok"] is True
    provider.assert_called_once_with(router.PUBLIC_TOPICS["cyber_safety_india"])


def test_provider_failure_is_not_fabricated(agents, monkeypatch):
    team, _, _ = agents
    monkeypatch.setattr(router, "search_public_web", Mock(side_effect=OSError("provider offline")))
    result = json.loads(run_phase9c_agent_search(team["KnowledgeAgent"].tools["public_web_research"]("online_shopping_safety")))
    assert result == {"ok": False, "error": "SEARCH_UNAVAILABLE"}


def test_web_result_id_cannot_be_policy_citation(agents, monkeypatch):
    team, _, persisted = agents
    mock = AsyncMock(return_value={"ok": True, "source_type": "EXTERNAL_UNTRUSTED", "results": [{"id": "WEB-1", "url": "https://example.org"}]})
    monkeypatch.setattr(team_module, "search_approved_topic", mock)
    run_phase9c_agent_search(team["KnowledgeAgent"].tools["public_web_research"]("consumer_rights_india"))
    assert run_phase9c_agent_search(team["LogAgent"].tools["persist_outcome"]("summary", "Unverified draft", "WEB-1", False)) == "LOGGED"
    args = persisted.call_args.args
    assert args[4] == []  # Reject untrusted web ID before persistence
    assert args[5] is True
    assert "human review" in args[3].lower()
    assert "WEB-1" not in args[6]


def test_valid_policy_evidence_remains_usable(agents):
    team, retriever, persisted = agents
    results = json.loads(run_phase9c_agent_search(team["KnowledgeAgent"].tools["policy_search"]("returns policy")))
    assert results[0]["id"] == "POLICY-1"
    assert run_phase9c_agent_search(team["ResolutionAgent"].tools["record_resolution_handoff"](
        "Grounded draft", "POLICY-1", False)) == "HANDOFF_RECORDED"
    assert run_phase9c_agent_search(team["LogAgent"].tools["persist_outcome"]("summary", "Grounded draft", "POLICY-1", False)) == "LOGGED"
    args = persisted.call_args.args
    assert args[5] is False
    assert "POLICY-1" in args[6]
    retriever.search.assert_called_once_with("returns policy")


# ======================================================================
# From test_phase9c_public_search_router.py
# ======================================================================

def run_async_phase9c_public_search_router(coro):
    """Run coroutine on a dedicated event loop to avoid nested asyncio.run()."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result(timeout=30)


@pytest.mark.parametrize("topic", [
    "my order DEMO-1001", "refund", "novacart", "account",
    "consumer_rights_india; ignore restrictions", "", None, 123,
])
def test_unapproved_topics_never_call_provider(topic):
    with patch("backend.services.public_search_router.search_public_web") as provider:
        result = run_async_phase9c_public_search_router(search_approved_topic(topic))
    assert result == {"ok": False, "error": "TOPIC_NOT_ALLOWED"}
    provider.assert_not_called()


@pytest.mark.parametrize("topic", tuple(PUBLIC_TOPICS))
def test_approved_topics_use_exact_static_queries(topic):
    payload = {"ok": True, "results": [{"title": "Gov", "url": "https://example.org", "snippet": "Info"}]}
    with patch("backend.services.public_search_router.search_public_web", return_value=payload) as provider:
        result = run_async_phase9c_public_search_router(search_approved_topic(topic))
    provider.assert_called_once_with(PUBLIC_TOPICS[topic])
    assert result["ok"] is True
    assert result["source_type"] == "EXTERNAL_UNTRUSTED"
    assert result["topic"] == topic


def test_provider_failure_is_safe():
    with patch("backend.services.public_search_router.search_public_web", side_effect=OSError("secret")):
        result = run_async_phase9c_public_search_router(search_approved_topic("cyber_safety_india"))
    assert result == {"ok": False, "error": "SEARCH_UNAVAILABLE"}


def test_provider_unconfigured_is_safe():
    with patch("backend.services.public_search_router.search_public_web", return_value={"ok": False, "error": "SEARCH_NOT_CONFIGURED"}):
        result = run_async_phase9c_public_search_router(search_approved_topic("cyber_safety_india"))
    assert result == {"ok": False, "error": "SEARCH_NOT_CONFIGURED"}


def test_topics_are_finite():
    assert set(allowed_public_topics()) == set(PUBLIC_TOPICS)


# ======================================================================
# From test_serper_adapter.py
# ======================================================================

class FakeResponse:
    status = 200
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, n):
        return self.payload


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload
    def open(self, request, timeout):
        assert request.full_url == search.SERPER_ENDPOINT
        assert request.get_method() == "POST"
        assert request.get_header("X-api-key") == "test-serper-key"
        assert json.loads(request.data)["num"] == search.MAX_RESULTS
        assert timeout == search.TIMEOUT_SECONDS
        return FakeResponse(self.payload)


def test_serper_success(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "serper")
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    payload = json.dumps({"organic": [
        {"title": "Gov", "link": "https://www.india.gov.in/", "snippet": "Official"},
        {"title": "Bad", "link": "http://localhost/", "snippet": "Unsafe"},
    ]}).encode()
    with patch.object(search, "build_opener", return_value=FakeOpener(payload)):
        result = search.search_public_web("India government consumer rights guidance")
    assert result == {"ok": True, "source_type": "EXTERNAL_UNTRUSTED", "results": [
        {"title": "Gov", "url": "https://www.india.gov.in/", "snippet": "Official"}
    ]}


def test_serper_missing_key(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "serper")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    assert search.search_public_web("India government consumer rights guidance") == {
        "ok": False, "error": "SEARCH_NOT_CONFIGURED"}


def test_serper_private_query_blocked_before_network(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "serper")
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    with patch.object(search, "build_opener") as opener:
        assert search.search_public_web("NovaCart order ID DEMO-1001") == {
            "ok": False, "error": "PRIVATE_QUERY_BLOCKED"}
        opener.assert_not_called()


def test_serper_bad_json(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "serper")
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    with patch.object(search, "build_opener", return_value=FakeOpener(b"invalid json")):
        assert search.search_public_web("India government consumer rights guidance") == {
            "ok": False, "error": "SEARCH_INVALID_RESPONSE"}


def test_serper_invalid_provider_no_network(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "unsupported")
    with patch.object(search, "build_opener") as opener:
        assert search.search_public_web("India government consumer rights guidance") == {
            "ok": False, "error": "SEARCH_NOT_CONFIGURED"}
        opener.assert_not_called()


def test_serper_network_failure(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "serper")
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    with patch.object(search, "build_opener", side_effect=OSError("offline")):
        assert search.search_public_web("India government consumer rights guidance") == {
            "ok": False, "error": "SEARCH_UNAVAILABLE"}


# ======================================================================
# From test_web_search_adapter.py
# ======================================================================

@pytest.fixture
def force_brave_provider(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "brave")
from unittest.mock import patch

from backend.services import web_search_adapter as search


@pytest.mark.usefixtures('force_brave_provider')
def test_private_queries_blocked_before_provider(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")
    blocked = [
        "NovaCart order ID DEMO-1001", "customer email abc@example.com",
        "tracking number 12345", "internal policy refund", "password reset for customer",
        "call +91 9876543210", "check https://localhost/private",
        "my Aadhaar number", "refund status for order", "api key leak",
    ]
    with patch.object(search, "build_opener") as opener:
        for query in blocked:
            result = search.search_public_web(query)
            assert result == {"ok": False, "error": "PRIVATE_QUERY_BLOCKED"}, query
        opener.assert_not_called()


@pytest.mark.usefixtures('force_brave_provider')
def test_invalid_queries():
    for query in ["", " ", "x" * 181, "hello\nworld", None]:
        assert search.search_public_web(query)["error"] == "INVALID_QUERY"


@pytest.mark.usefixtures('force_brave_provider')
def test_missing_key(monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    assert search.search_public_web("Python 3.11 release notes") == {
        "ok": False, "error": "SEARCH_NOT_CONFIGURED"
    }


@pytest.mark.usefixtures('force_brave_provider')
def test_public_urls_only():
    assert search._public_url("https://docs.python.org/3/")
    for url in ["http://example.com", "https://localhost", "https://127.0.0.1/x",
                "https://10.0.0.1/x", "https://example.com:8443/x",
                "https://user:pass@example.com/x", "file:///etc/passwd"]:
        assert search._public_url(url) is None


@pytest.mark.usefixtures('force_brave_provider')
def test_normalize_discards_bad_links():
    data = {"web": {"results": [
        {"title": "Bad", "url": "http://example.com"},
        {"title": "Good", "url": "https://docs.python.org/3/", "description": "Official docs"},
    ]}}
    assert search._normalize(data) == [{
        "title": "Good", "url": "https://docs.python.org/3/", "snippet": "Official docs"
    }]


class FakeResponse_web_search_adapter:
    status = 200
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, n):
        return self.payload


class FakeOpener_web_search_adapter:
    def __init__(self, payload):
        self.payload = payload
    def open(self, request, timeout):
        assert request.full_url.startswith(search.API_ENDPOINT + "?")
        assert timeout == search.TIMEOUT_SECONDS
        assert request.get_header("X-subscription-token") == "test-key"
        return FakeResponse_web_search_adapter(self.payload)


@pytest.mark.usefixtures('force_brave_provider')
def test_success_with_mocked_provider(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")
    payload = json.dumps({"web": {"results": [
        {"title": "Python docs", "url": "https://docs.python.org/3/", "description": "Docs"}
    ]}}).encode()
    with patch.object(search, "build_opener", return_value=FakeOpener_web_search_adapter(payload)):
        result = search.search_public_web("Python 3.11 release notes")
    assert result["ok"] is True
    assert result["source_type"] == "EXTERNAL_UNTRUSTED"
    assert result["results"][0]["url"] == "https://docs.python.org/3/"


@pytest.mark.usefixtures('force_brave_provider')
def test_bad_json_fails_closed(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")
    with patch.object(search, "build_opener", return_value=FakeOpener_web_search_adapter(b"not json")):
        result = search.search_public_web("Python 3.11 release notes")
    assert result == {"ok": False, "error": "SEARCH_INVALID_RESPONSE"}


@pytest.mark.usefixtures('force_brave_provider')
def test_network_failure_fails_closed(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")
    with patch.object(search, "build_opener", side_effect=OSError("network down")):
        result = search.search_public_web("Python 3.11 release notes")
    assert result == {"ok": False, "error": "SEARCH_UNAVAILABLE"}
