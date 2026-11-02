
"""Offline citation handoff regression tests.

Run:
    python -m pytest -q tests/test_citation_handoff_regression.py
"""

from unittest.mock import patch

import pytest

from backend.agents import team as module


class Agent:
    def __init__(
        self,
        name,
        model_client=None,
        system_message="",
        tools=None,
    ):
        self.name = name
        self.system_message = system_message
        self.tools = tools or []


class Condition:
    def __init__(self, *args):
        pass

    def __or__(self, other):
        return self


class Group:
    def __init__(self, agents, termination_condition):
        self.agents = agents


class Retriever:
    def __init__(self, passages):
        self.passages = passages

    def search(self, query):
        return self.passages


def create(passages, callback):
    """Create a mocked agent team without external API calls."""

    with (
        patch.object(module, "AssistantAgent", Agent),
        patch.object(module, "RoundRobinGroupChat", Group),
        patch.object(module, "TextMentionTermination", Condition),
        patch.object(module, "MaxMessageTermination", Condition),
        patch.object(
            module,
            "OpenAIChatCompletionClient",
            return_value=object(),
        ),
    ):
        team, _ = module.make_team(
            Retriever(passages),
            "job",
            "ticket",
            callback,
            initial_query="what is the delivery timeline?",
        )

    return team


PASSAGE = {
    "id": "shipping-verified-1",
    "source": "01_Shipping_and_Delivery_SOP.pdf",
    "visibility": "customer",
    "page": 1,
    "text": (
        "Orders have preparation and in-transit statuses; "
        "no order-specific ETA provided."
    ),
}


def test_evidence_visible_to_resolution_and_log_agents():
    """All relevant agents must receive the retrieved citation."""

    team = create(
        [PASSAGE],
        lambda *args: "PERSISTED",
    )

    agents = {agent.name: agent for agent in team.agents}

    for name in (
        "KnowledgeAgent",
        "ResolutionAgent",
        "LogAgent",
    ):
        assert PASSAGE["id"] in agents[name].system_message

    assert (
        "do not invent a number of days"
        in agents["ResolutionAgent"].system_message
    )


@pytest.mark.asyncio
async def test_valid_citation_is_preserved_without_escalation():
    """A valid citation must be persisted without forced escalation."""

    saved = []

    team = create(
        [PASSAGE],
        lambda *args: saved.append(args) or "PERSISTED",
    )

    persist = team.agents[3].tools[0]

    result = await persist(
        "General shipping process",
        "The policy describes shipment stages.",
        PASSAGE["id"],
        False,
    )

    assert result == "LOGGED"
    assert saved[0][4] == [PASSAGE["id"]]
    assert saved[0][5] is False


@pytest.mark.asyncio
async def test_missing_and_invented_citations_fail_closed():
    """Missing or fabricated citations must trigger human review."""

    for bad in ("", "fabricated-policy-id"):
        saved = []

        team = create(
            [PASSAGE],
            lambda *args: saved.append(args) or "PERSISTED",
        )

        result = await team.agents[3].tools[0](
            "summary",
            "Unsupported ETA: 2 days",
            bad,
            False,
        )

        assert saved[0][4] == []
        assert saved[0][5] is True
        assert "human review" in saved[0][3]


@pytest.mark.asyncio
async def test_explicit_escalation_remains_enforced():
    """An explicit escalation request must never be downgraded."""

    saved = []

    team = create(
        [PASSAGE],
        lambda *args: saved.append(args) or "PERSISTED",
    )

    result = await team.agents[3].tools[0](
        "risk",
        "Human review required",
        PASSAGE["id"],
        True,
    )

    assert saved[0][5] is True
