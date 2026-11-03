"""Phase 2E.5: approved-only AutoGen handoff and worker persistence, offline.

Requires the activated local_customer_index. Never invokes model APIs.
"""
import asyncio
import json
import time
import uuid

import pytest

from backend.agents import team as agent_module
from backend.db import store
from backend.rag.customer_index import DEFAULT_INDEX, verify_index
from backend.rag.customer_policy import approved_passages
from backend.worker import main as worker


@pytest.fixture(autouse=True)
def approved_only(monkeypatch):
    monkeypatch.setenv('FIXFLOW_CUSTOMER_INDEX_MODE', 'approved_only')


@pytest.fixture
def approved_docs():
    manifest, index = verify_index(DEFAULT_INDEX)
    assert index.ntotal == len(manifest['chunks']) == 12
    assert manifest['chunks'] == approved_passages()
    return manifest['chunks']


@pytest.fixture
def mock_agents(monkeypatch):
    agents = []
    class FakeAgent:
        def __init__(self, name, *, model_client, system_message, tools=None):
            self.name, self.system_message, self.tools = name, system_message, tools or []
            agents.append(self)
    class FakeClient:
        def __init__(self, **kwargs): self.kwargs = kwargs
    class FakeCondition:
        def __init__(self, *args): self.args = args
        def __or__(self, other): return ('OR', self, other)
    class FakeTeam:
        def __init__(self, members, termination_condition):
            self.members, self.termination_condition = members, termination_condition
    monkeypatch.setattr(agent_module, 'AssistantAgent', FakeAgent)
    monkeypatch.setattr(agent_module, 'OpenAIChatCompletionClient', FakeClient)
    monkeypatch.setattr(agent_module, 'TextMentionTermination', FakeCondition)
    monkeypatch.setattr(agent_module, 'MaxMessageTermination', FakeCondition)
    monkeypatch.setattr(agent_module, 'RoundRobinGroupChat', FakeTeam)
    return agents


class StaticRetriever:
    def __init__(self, docs): self.docs, self.calls = docs, []
    def search(self, question, **kwargs):
        self.calls.append(question)
        return self.docs


def _build(agents, docs, query):
    callbacks = []
    def callback(*args):
        callbacks.append(args)
        return 'PERSISTED'
    retriever = StaticRetriever(docs)
    team, client = agent_module.make_team(
        retriever, 'job-offline', 'ticket-offline', callback, initial_query=query)
    tools = {tool.__name__: tool for agent in agents for tool in agent.tools}
    return team, client, tools, callbacks, retriever


def test_approved_only_four_agent_handoff(approved_docs, mock_agents):
    doc = approved_docs[0]
    team, _, tools, calls, retriever = _build(
        mock_agents, [doc], 'fashion apparel return window')
    assert [a.name for a in team.members] == [
        'QueryAgent', 'KnowledgeAgent', 'ResolutionAgent', 'LogAgent']
    assert retriever.calls == ['fashion apparel return window']
    for name in ('KnowledgeAgent', 'ResolutionAgent', 'LogAgent'):
        prompt = next(a.system_message for a in mock_agents if a.name == name)
        assert doc['id'] in prompt
    assert asyncio.run(tools['persist_outcome'](
        summary='Fashion returns', proposed_response='Fashion and apparel have a 14-day return window.',
        cited_ids=doc['id'], escalate=False)) == 'LOGGED'
    assert calls[-1][4] == [doc['id']]
    assert calls[-1][5] is False


@pytest.mark.parametrize('bad_id', ['', 'fabricated-policy-id'])
def test_agent_rejects_missing_or_invented_citation(approved_docs, mock_agents, bad_id):
    _, _, tools, calls, _ = _build(mock_agents, [approved_docs[0]], 'fashion return window')
    assert asyncio.run(tools['persist_outcome'](
        summary='Unverified', proposed_response='A refund is guaranteed.',
        cited_ids=bad_id, escalate=False)) == 'LOGGED'
    assert calls[-1][4] == []
    assert calls[-1][5] is True
    assert 'human review' in calls[-1][3].lower()


def test_agent_shipping_without_approved_source_fails_closed(approved_docs, mock_agents):
    assert not any(d['source'] == '01_Shipping_and_Delivery_SOP.pdf' for d in approved_docs)
    _, _, tools, calls, retriever = _build(
        mock_agents, [], 'When will my shipment arrive?')
    assert retriever.calls  # mandatory retrieval and possible narrow shipping retry
    assert asyncio.run(tools['persist_outcome'](
        summary='Shipping inquiry', proposed_response='Delivery within 2 days.',
        cited_ids='', escalate=False)) == 'LOGGED'
    assert calls[-1][4] == [] and calls[-1][5] is True


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'phase2e5.sqlite3'))
    store.init()


def seed_ticket(*, risk=''):
    uid, tid, jid = [str(uuid.uuid4()) for _ in range(3)]
    now = time.time()
    with store.connect() as c:
        c.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                  (uid, f'{uid}@example.test', 'not-a-real-hash', 'customer', 1))
        c.execute('INSERT INTO tickets(id,customer_id,subject,description,claimed_amount,status,risk,created_at,version) '
                  'VALUES (?,?,?,?,?,?,?,?,?)',
                  (tid, uid, 'Fashion return', 'Fashion apparel return window', None, 'QUEUED', risk, now, 0))
        c.execute('INSERT INTO jobs(id,ticket_id,status,created_at,input_version,attempts) '
                  'VALUES (?,?,?,?,?,?)', (jid, tid, 'QUEUED', now, 0, 0))
    return tid, jid


def get_state(tid, jid):
    with store.connect() as c:
        ticket = dict(c.execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone())
        result = c.execute('SELECT * FROM ai_results WHERE job_id=?', (jid,)).fetchone()
        messages = [dict(x) for x in c.execute(
            "SELECT * FROM messages WHERE ticket_id=? AND role='assistant' AND visible=1", (tid,))]
    return ticket, dict(result) if result else None, messages


def test_worker_approved_passage_publishes_once(isolated_db, approved_docs):
    tid, jid = seed_ticket()
    row = worker.claim()
    doc = approved_docs[0]
    draft = 'Fashion and apparel have a 14-day return window if unworn and unwashed.'
    assert worker.persist(jid, tid, 'Fashion return policy', draft, [doc['id']], False,
                          {doc['id']: doc}, lease=row['lease'], input_version=row['version']) == 'PERSISTED'
    worker.finish(row, True)
    ticket, result, messages = get_state(tid, jid)
    assert ticket['status'] == 'ANSWERED'
    assert result['requires_review'] == 0
    assert json.loads(result['citations']) == [doc['id']]
    assert len(messages) == 1
    worker.finish(row, True)
    assert len(get_state(tid, jid)[2]) == 1


def test_worker_rejects_forged_passage(isolated_db, approved_docs):
    tid, jid = seed_ticket()
    row = worker.claim()
    forged = dict(approved_docs[0], text='Unlimited refunds are guaranteed.')
    assert worker.persist(jid, tid, 'Forged', 'Unlimited refunds are guaranteed.',
                          [forged['id']], False, {forged['id']: forged},
                          lease=row['lease'], input_version=row['version']) == 'PERSISTED'
    worker.finish(row, True)
    ticket, result, messages = get_state(tid, jid)
    assert ticket['status'] == 'ESCALATED'
    assert result['requires_review'] == 1
    assert messages == []


def test_worker_missing_citation_escalates(isolated_db):
    tid, jid = seed_ticket()
    row = worker.claim()
    assert worker.persist(jid, tid, 'Unverified', 'We guarantee a refund.',
                          ['made-up-id'], False, {},
                          lease=row['lease'], input_version=row['version']) == 'PERSISTED'
    worker.finish(row, True)
    ticket, result, messages = get_state(tid, jid)
    assert ticket['status'] == 'ESCALATED'
    assert result['requires_review'] == 1
    assert messages == []


def test_worker_high_risk_escalates_even_with_valid_citation(isolated_db, approved_docs):
    tid, jid = seed_ticket(risk='REFUND_GE_500')
    row = worker.claim()
    doc = approved_docs[0]
    assert worker.persist(jid, tid, 'Refund review',
                          'Fashion and apparel have a 14-day return window.',
                          [doc['id']], False, {doc['id']: doc},
                          lease=row['lease'], input_version=row['version']) == 'PERSISTED'
    worker.finish(row, True)
    ticket, result, messages = get_state(tid, jid)
    assert ticket['status'] == 'ESCALATED'
    assert messages == []


def test_worker_rejects_stale_lease(isolated_db, approved_docs):
    tid, jid = seed_ticket()
    row = worker.claim()
    doc = approved_docs[0]
    with pytest.raises(RuntimeError, match='Stale or unauthorized'):
        worker.persist(jid, tid, 'Old job', 'Policy text', [doc['id']], False,
                       {doc['id']: doc}, lease='invalid-lease', input_version=row['version'])
    assert get_state(tid, jid)[1] is None
