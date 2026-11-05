"""Consolidated agents prompts tests.

Merged without behaviour changes from:
- test_agent_citation_handoff.py: Deterministic AutoGen team/citation contract tests; no API calls or DB writes.
- test_citation_handoff_regression.py: Offline citation handoff regression tests.
- test_emotion_prompt_contract.py: regression tests
- test_supported_claims_prompt_contract.py: Prompt contract: agents that draft policy text must stay within the cited wording.
- test_shipping_response_prompt_contract.py: Deterministic contract tests for shipping-specific AutoGen prompt guidance.
"""
import asyncio
import importlib
from types import SimpleNamespace
import pytest
from unittest.mock import patch
from backend.agents import team as module
from pathlib import Path
import ast


# ======================================================================
# From test_agent_citation_handoff.py
# ======================================================================

@pytest.fixture
def team_harness(monkeypatch):
    module = importlib.import_module('backend.agents.team')
    agents = []

    class FakeAgent:
        def __init__(self, name, *, model_client, system_message, tools=None, **_options):
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


def record(harness, ids, response='The SOP refers to an order-specific promised EDD.', review=False):
    """ResolutionAgent's structured handoff tool (authoritative for LogAgent)."""
    return asyncio.run(harness.tools['record_resolution_handoff'](
        customer_draft=response, supporting_policy_ids=ids, needs_human_review=review))


def persist(harness, ids, response='The SOP refers to an order-specific promised EDD.',
            escalate=False, handoff=True):
    """Mirror production order: ResolutionAgent records a structured handoff,
    then LogAgent persists it. handoff=False reproduces a missing handoff."""
    if handoff:
        record(harness, ids, response, escalate)
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


def test_missing_structured_handoff_fails_closed(team_harness):
    # LogAgent supplies a valid retrieved ID, but ResolutionAgent recorded nothing.
    assert persist(team_harness, 'shipping-edd-001', handoff=False) == 'LOGGED'
    args = team_harness.calls[-1]
    assert args[4] == [] and args[5] is True
    assert 'human review' in args[3].lower()


def test_rejected_handoff_does_not_leave_earlier_handoff_in_place(team_harness):
    assert record(team_harness, 'shipping-edd-001') == 'HANDOFF_RECORDED'
    assert record(team_harness, 'invented-policy-id').startswith('HANDOFF_REJECTED')
    persist(team_harness, 'shipping-edd-001', handoff=False)
    args = team_harness.calls[-1]
    assert args[4] == [] and args[5] is True


def test_malformed_handoff_arguments_fail_closed_and_clear_earlier_handoff(team_harness):
    assert record(team_harness, 'shipping-edd-001') == 'HANDOFF_RECORDED'
    assert record(team_harness, 'shipping-edd-001', response='   ') == 'HANDOFF_REJECTED: EMPTY_DRAFT'
    persist(team_harness, 'shipping-edd-001', handoff=False)
    assert team_harness.calls[-1][4] == [] and team_harness.calls[-1][5] is True


def test_log_agent_cannot_override_recorded_handoff(team_harness):
    extra = {'id': 'shipping-extra-002', 'visibility': 'customer',
             'source': '01_Shipping_and_Delivery_SOP.pdf', 'text': 'Additional policy detail.'}
    team_harness.retriever.results = [extra]
    asyncio.run(team_harness.tools['policy_search']('additional shipping policy'))
    assert record(team_harness, 'shipping-edd-001') == 'HANDOFF_RECORDED'
    # LogAgent tries to substitute a different (retrieved) ID.
    asyncio.run(team_harness.tools['persist_outcome'](
        summary='s', proposed_response='ignored', cited_ids='shipping-extra-002', escalate=False))
    assert team_harness.calls[-1][4] == ['shipping-edd-001']


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


# ======================================================================
# From test_citation_handoff_regression.py
# ======================================================================

class Agent:
    def __init__(
        self,
        name,
        model_client=None,
        system_message="",
        tools=None,
        **_options,
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

    # Production order: ResolutionAgent records the structured handoff first.
    record = team.agents[2].tools[0]
    assert await record(
        "The policy describes shipment stages.", PASSAGE["id"], False
    ) == "HANDOFF_RECORDED"
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

        # A missing or fabricated ID is rejected at the handoff (or recorded
        # with no IDs); either way persistence must fail closed.
        await team.agents[2].tools[0]("Unsupported ETA: 2 days", bad, False)
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

    await team.agents[2].tools[0]("Human review required", PASSAGE["id"], True)
    result = await team.agents[3].tools[0](
        "risk",
        "Human review required",
        PASSAGE["id"],
        True,
    )

    assert saved[0][5] is True


# ======================================================================
# From test_emotion_prompt_contract.py
# ======================================================================

SOURCE = Path(__file__).resolve().parents[1] / "backend" / "agents" / "team.py"

def test_emotion_labels_and_legacy_sentiment():
    source = SOURCE.read_text(encoding="utf-8")
    assert "Sentiment: <Positive | Neutral | Negative | Angry>" in source
    assert "Emotion: <angry | frustrated | sad | happy | confused | worried | neutral | uncertain>" in source
    assert "Emotion confidence: <low | medium | high>" in source

def test_emotion_cannot_override_safety():
    source = SOURCE.read_text(encoding="utf-8")
    assert "Emotion never overrides" in source
    assert "Emotion must not change eligibility" in source
    assert "do not alter its contract" in source

def test_all_four_agents_unchanged():
    source = SOURCE.read_text(encoding="utf-8")
    for name in ("QueryAgent", "KnowledgeAgent", "ResolutionAgent", "LogAgent"):
        assert name in source


# ======================================================================
# From test_supported_claims_prompt_contract.py
# ======================================================================

@pytest.fixture
def prompts(monkeypatch):
    module = importlib.import_module('backend.agents.team')
    agents = []

    class FakeAgent:
        def __init__(self, name, *, model_client, system_message, tools=None, **_options):
            self.name, self.system_message, self.tools = name, system_message, tools or []
            agents.append(self)

    class FakeCondition:
        def __init__(self, *args):
            pass

        def __or__(self, other):
            return ('OR', self, other)

    class FakeTeam:
        def __init__(self, members, termination_condition):
            self.members = members

    monkeypatch.setattr(module, 'AssistantAgent', FakeAgent)
    monkeypatch.setattr(module, 'OpenAIChatCompletionClient', lambda **kw: object())
    monkeypatch.setattr(module, 'TextMentionTermination', FakeCondition)
    monkeypatch.setattr(module, 'MaxMessageTermination', FakeCondition)
    monkeypatch.setattr(module, 'RoundRobinGroupChat', FakeTeam)
    monkeypatch.setattr(module, 'customer_safe_passages', lambda ps: ps)

    class Retriever:
        def search(self, query, **kwargs):
            return [{'id': 'p1', 'visibility': 'customer',
                     'source': '02_Returns_and_Refunds_SOP.pdf', 'text': 'A policy.'}]

    module.make_team(Retriever(), 'job', 'ticket', lambda *a: 'PERSISTED', initial_query='question')
    return module, {a.name: a.system_message for a in agents}


def test_policy_drafting_agents_receive_the_rule(prompts):
    module, by_name = prompts
    for name in ('KnowledgeAgent', 'ResolutionAgent'):
        assert module.SUPPORTED_CLAIMS_RULES in by_name[name], name


def test_rule_covers_anchor_unlisted_remedies_and_gaps(prompts):
    module, _ = prompts
    rules = module.SUPPORTED_CLAIMS_RULES.lower()
    for fragment in ('stay within the cited text', 'starting point', 'exchange',
                     'stock availability', 'refund fallback', 'does not cover',
                     'needs_human_review', 'industry practice'):
        assert fragment in rules, fragment


def test_rule_is_generic_not_question_specific(prompts):
    module, _ = prompts
    rules = module.SUPPORTED_CLAIMS_RULES.lower()
    for word in ('headphone', 'shoe', 'cable', 'electronics', 'fashion', 'novacart',
                 'wrong size', 'colour', 'color'):
        assert word not in rules, word


def test_rule_does_not_weaken_existing_safeguards(prompts):
    _, by_name = prompts
    resolution = by_name['ResolutionAgent']
    for fragment in ('INR 500', 'record_resolution_handoff', 'Never promise to',
                     'never auto-execute refunds'):
        assert fragment in resolution, fragment
    assert 'Never invent or guess a policy ID' in by_name['KnowledgeAgent']


# ======================================================================
# From test_shipping_response_prompt_contract.py
# ======================================================================

TEAM = Path('backend/agents/team.py')


def _source():
    source = TEAM.read_text(encoding='utf-8')
    ast.parse(source)
    return source


def _agent_prompt(agent_name):
    tree = ast.parse(_source())
    make_team = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'make_team')
    for statement in make_team.body:
        if not isinstance(statement, ast.Assign) or not isinstance(statement.value, ast.Call):
            continue
        call = statement.value
        if not any(k.arg == 'system_message' for k in call.keywords):
            continue
        if not call.args or not isinstance(call.args[0], ast.Constant) or call.args[0].value != agent_name:
            continue
        # Only literal string fragments; exclude dynamic preloaded evidence.
        return ' '.join(n.value for n in ast.walk(next(k.value for k in call.keywords if k.arg == 'system_message'))
                        if isinstance(n, ast.Constant) and isinstance(n.value, str))
    raise AssertionError(f'Agent prompt missing: {agent_name}')


def test_shipping_duration_limits_are_explicit_for_knowledge_and_resolution():
    for agent in ('KnowledgeAgent', 'ResolutionAgent'):
        prompt = _agent_prompt(agent)
        assert 'universal' in prompt
        assert 'location-based' in prompt
        assert 'Estimated Delivery Date (EDD)' in prompt


def test_resolution_omits_internal_shipping_operations():
    prompt = _agent_prompt('ResolutionAgent')
    assert 'Omit merchant dispatch-manifest SLAs' in prompt
    assert 'fulfillment-channel' in prompt
    assert 'Policy citations belong only in Supporting policy IDs' in prompt


def test_logger_keeps_ids_out_of_customer_draft():
    prompt = _agent_prompt('LogAgent')
    assert 'Copy only Customer draft into proposed_response' in prompt
    assert 'Put validated supporting IDs only into cited_ids' in prompt
    assert 'escalate rather than publish' in prompt


def test_existing_citation_validation_is_unchanged():
    src = _source()
    assert 'if not ids or any(item not in evidence for item in ids)' in src
    assert 'escalate = True' in src
    assert 'Never attach an ID automatically solely because it was retrieved' in src


def test_refund_safeguards_still_in_resolution():
    prompt = _agent_prompt('ResolutionAgent')
    assert 'INR 500' in prompt
    assert 'never auto-execute refunds at any amount' in prompt
    assert 'verified order identity and supporting evidence' in prompt
    assert 'payment disputes require escalation' in prompt
