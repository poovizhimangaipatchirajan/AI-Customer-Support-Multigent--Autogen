"""Deterministic AutoGen team/citation contract tests; no API calls or DB writes."""
import asyncio
import importlib
from types import SimpleNamespace

import pytest


@pytest.fixture
def team_harness(monkeypatch):
    module = importlib.import_module('backend.agents.team')
    agents = []

    class FakeAgent:
        def __init__(self, name, *, model_client, system_message, tools=None):
            self.name = name
            self.system_message = system_message
            self.tools = tools or []
            agents.append(self)

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeCondition:
        def __init__(self, *args):
            self.args = args
        def __or__(self, other):
            return ('OR', self, other)

    class FakeTeam:
        def __init__(self, members, termination_condition):
            self.members = members
            self.termination_condition = termination_condition

    monkeypatch.setattr(module, 'AssistantAgent', FakeAgent)
    monkeypatch.setattr(module, 'OpenAIChatCompletionClient', FakeClient)
    monkeypatch.setattr(module, 'TextMentionTermination', FakeCondition)
    monkeypatch.setattr(module, 'MaxMessageTermination', FakeCondition)
    monkeypatch.setattr(module, 'RoundRobinGroupChat', FakeTeam)
    # Explicitly control the filtered retrieval boundary; visibility filtering has separate tests.
    monkeypatch.setattr(module, 'customer_safe_passages', lambda passages: [p for p in passages if p.get('visibility') == 'customer'])

    passage = {
        'id': 'shipping-edd-001',
        'source': '01_Shipping_and_Delivery_SOP.pdf',
        'page': 1,
        'visibility': 'customer',
        'text': 'Shipping policy references the promised Estimated Delivery Date (EDD) for an order.'
    }

    class Retriever:
        def __init__(self):
            self.results = [passage]
            self.queries = []
        def search(self, query):
            self.queries.append(query)
            return self.results

    retriever = Retriever()
    calls = []

    def callback(*args):
        calls.append(args)
        return 'PERSISTED'

    question = "What is NovaCart's standard delivery timeframe, and does it vary by location?"
    team, client = module.make_team(retriever, 'job-1', 'ticket-1', callback, initial_query=question)
    tools = {tool.__name__: tool for agent in agents for tool in agent.tools}
    return SimpleNamespace(team=team, agents=agents, tools=tools, calls=calls,
                           retriever=retriever, passage=passage, question=question)


def persist(harness, ids, response='The SOP refers to an order-specific promised EDD.', escalate=False):
    return asyncio.run(harness.tools['persist_outcome'](
        summary='Routine shipping question', proposed_response=response,
        cited_ids=ids, escalate=escalate))


def test_four_agent_order_and_termination(team_harness):
    assert [a.name for a in team_harness.team.members] == [
        'QueryAgent', 'KnowledgeAgent', 'ResolutionAgent', 'LogAgent']
    assert team_harness.team.termination_condition[0] == 'OR'


def test_long_shipping_question_preloads_citation_for_resolution_and_logger(team_harness):
    assert team_harness.retriever.queries == [team_harness.question]
    for name in ('ResolutionAgent', 'LogAgent'):
        prompt = next(a.system_message for a in team_harness.agents if a.name == name)
        assert team_harness.passage['id'] in prompt
        assert '48-business-hour' in prompt


def test_valid_citation_preserved_without_escalation(team_harness):
    assert persist(team_harness, 'shipping-edd-001') == 'LOGGED'
    job, ticket, summary, draft, ids, escalate, evidence = team_harness.calls[-1]
    assert (job, ticket) == ('job-1', 'ticket-1')
    assert ids == ['shipping-edd-001']
    assert escalate is False
    assert evidence['shipping-edd-001']['source'] == '01_Shipping_and_Delivery_SOP.pdf'


@pytest.mark.parametrize('ids', ['', 'invented-policy-id', 'shipping-edd-001, invented-policy-id'])
def test_missing_or_fabricated_citations_fail_closed(team_harness, ids):
    assert persist(team_harness, ids) == 'LOGGED'
    args = team_harness.calls[-1]
    assert args[4] == []
    assert args[5] is True
    assert 'human review' in args[3].lower()


def test_duplicate_valid_citations_normalized(team_harness):
    persist(team_harness, 'shipping-edd-001, shipping-edd-001')
    assert team_harness.calls[-1][4] == ['shipping-edd-001']


def test_explicit_human_review_is_not_overridden(team_harness):
    persist(team_harness, 'shipping-edd-001', escalate=True)
    assert team_harness.calls[-1][5] is True


def test_no_automatic_citation_from_retrieved_evidence(team_harness):
    persist(team_harness, '')
    assert team_harness.passage['id'] in team_harness.calls[-1][6]
    assert team_harness.calls[-1][4] == []


def test_policy_search_adds_new_exact_evidence_ids(team_harness):
    extra = {'id': 'shipping-extra-002', 'visibility': 'customer',
             'source': '01_Shipping_and_Delivery_SOP.pdf', 'text': 'Additional policy detail.'}
    team_harness.retriever.results = [extra]
    result = asyncio.run(team_harness.tools['policy_search']('additional shipping policy'))
    assert 'shipping-extra-002' in result
    persist(team_harness, 'shipping-extra-002')
    assert team_harness.calls[-1][4] == ['shipping-extra-002']

# Opt-in live integration checks. These exercise real AutoGen and hybrid RAG,
# but replace the persistence callback with an in-memory capture.
LIVE_SHIPPING_QUESTIONS = (
    "What is NovaCart's standard delivery timeframe?",
    "What is NovaCart's standard delivery timeframe, and does it vary by location?",
)


@pytest.mark.parametrize('question', LIVE_SHIPPING_QUESTIONS, ids=['standard-timeframe', 'location-variation'])
def test_live_shipping_agent_evidence_handoff(question):
    """Real LLM + RAG, with no ticket creation or database writes.

    Run explicitly using FIXFLOW_RUN_LIVE_AGENT_TESTS=1. This is an integration
    acceptance test, not a deterministic unit test; API usage may incur charges.
    """
    import os

    if os.getenv('FIXFLOW_RUN_LIVE_AGENT_TESTS') != '1':
        pytest.skip('Live AutoGen test is opt-in; set FIXFLOW_RUN_LIVE_AGENT_TESTS=1')
    if not os.getenv('OPENAI_API_KEY'):
        pytest.skip('OPENAI_API_KEY is required for the opt-in live integration test')

    from backend.rag.hybrid import HybridRetriever
    from backend.agents.team import make_team
    from backend.services.grounding_guard import (
        customer_safe_passages, check_response_grounding, check_customer_visibility,
    )

    retriever = HybridRetriever()
    retrieved = customer_safe_passages(retriever.search(question))
    shipping = [p for p in retrieved if 'Shipping_and_Delivery' in p.get('source', '')]
    assert shipping, 'No customer-visible shipping policy evidence retrieved'

    recorded = []

    def capture(job_id, ticket_id, summary, draft, ids, escalate, evidence):
        recorded.append(dict(job_id=job_id, ticket_id=ticket_id, summary=summary,
                             draft=draft, ids=list(ids), escalate=escalate,
                             evidence=dict(evidence)))
        return 'PERSISTED'

    async def run_live():
        team, client = make_team(
            retriever, 'integration-job', 'integration-ticket', capture,
            initial_query=question,
        )
        try:
            result = await team.run(task=(
                'Customer question: ' + question + '\n'
                'Follow the four-agent workflow. Use customer-visible NovaCart policy '
                'evidence and exact citations. Do not invent delivery durations, '
                'location-specific promises, live order status, or refund actions.'
            ))
            return result
        finally:
            await client.close()

    result = asyncio.run(run_live())
    assert recorded, 'LogAgent did not call persist_outcome'
    assert len(recorded) == 1, 'Unexpected duplicate persistence callbacks'
    outcome = recorded[0]

    # Fail if the model omitted IDs or chose unrelated passages; do not repair
    # or auto-attach citations in the test itself.
    assert outcome['escalate'] is False, 'Routine supported shipping answer escalated'
    assert outcome['ids'], 'LogAgent omitted citations'
    assert all(i in outcome['evidence'] for i in outcome['ids'])
    assert any('Shipping_and_Delivery' in outcome['evidence'][i].get('source', '')
               for i in outcome['ids']), 'No shipping SOP citation supplied'
    assert check_response_grounding(
        outcome['draft'], [outcome['evidence'][i] for i in outcome['ids']]
    ) is None, 'Draft failed deterministic grounding validation'
    assert check_customer_visibility(outcome['draft']) is None, 'Draft exposed internal operations'

    print("\n--- LIVE SHIPPING RESPONSE ---")
    print("Question:", question)
    print("Response:", outcome["draft"])
    print("Citation IDs:", outcome["ids"])
    print("--- END RESPONSE ---\n")
    draft = outcome['draft'].lower()
    assert ('delivery' in draft or 'edd' in draft), 'Response omitted delivery context'
    assert any(term in draft for term in ('not specify', 'does not specify', 'no standard',
                                          'not provide', 'no universal', 'not stated',
                                          'not defined', 'no fixed', 'not list')), \
        'Response did not explain the policy lacks a universal delivery duration'
    if 'vary by location' in question:
        assert any(term in draft for term in ('location', 'region', 'area', 'address')), \
            'Response omitted the location aspect of the question'
    assert all(i not in outcome['draft'] for i in outcome['ids']), \
        'Raw policy chunk IDs leaked into customer-facing response'
    assert not any(token in draft for token in ('2-3 days', '3-5 days', '5-7 days')), \
        'Response invented a common delivery range'
