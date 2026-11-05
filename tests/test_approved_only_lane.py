"""Consolidated approved only lane tests.

Merged without behaviour changes from:
- test_customer_citation_isolation_2e5.py: regression tests
- test_phase2e4_runtime_integration.py: Offline structural and approval-boundary regression tests; no API calls.
- test_phase2e5_agent_worker_offline.py: Phase 2E.5: approved-only AutoGen handoff and worker persistence, offline.
- test_phase2e5_approved_only_offline.py: Phase 2E.5 offline, customer-approved index security tests.
- test_phase2e5_diagnostic_independence.py: Approved-only, offline, no DB or network; diagnostics never authorize action.
- test_retriever_reuse_and_citation_labels.py: Offline process-cache and citation-label regression tests.
"""
from backend.rag.customer_policy import approved_passages
from backend.rag.customer_index import verify_index
from backend.rag.customer_policy import is_approved_customer_passage
from backend.services.citation_catalog import resolve
from pathlib import Path
import ast
import pytest
from backend.rag.customer_runtime import customer_mode
import asyncio
import json
import time
import uuid
from backend.agents import team as agent_module
from backend.db import store
from backend.rag.customer_index import DEFAULT_INDEX
from backend.worker import main as worker
from unittest.mock import patch
from backend.rag.customer_index import CustomerHybridRetriever
from backend.rag.customer_runtime import create_customer_retriever
from backend.services import citation_catalog, help_center
from backend.services.response_diagnostics import inspect_response
from backend.rag import customer_runtime as runtime


# ======================================================================
# From test_customer_citation_isolation_2e5.py
# ======================================================================

def test_approved_only_citation_isolation(monkeypatch):
    monkeypatch.setenv(
        "FIXFLOW_CUSTOMER_INDEX_MODE",
        "approved_only",
    )

    manifest, index = verify_index("local_customer_index")

    assert index.ntotal == len(manifest["chunks"]) == len(approved_passages())

    approved = manifest["chunks"][0]
    assert is_approved_customer_passage(approved)

    citations = resolve([
        approved["id"],
        "unauthorized-internal-id",
    ])

    assert len(citations) == 1

    citation = citations[0]

    assert citation["id"] == approved["id"]
    assert citation["source"] == approved["source"]
    assert citation["page"] == approved["page"]
    assert citation["visibility"] == "customer"
    assert "text" not in citation


# ======================================================================
# From test_phase2e4_runtime_integration.py
# ======================================================================

ROOT = Path(__file__).resolve().parents[1]


def test_approved_identity_and_tampering():
    docs = approved_passages()
    assert len(docs) == len({d["id"] for d in docs}) == len(approved_passages())
    for doc in docs:
        assert is_approved_customer_passage(doc)
        for field in ('source', 'page', 'text', 'version', 'visibility', 'approval'):
            bad = dict(doc)
            bad[field] = 'forged'
            assert not is_approved_customer_passage(bad)


def test_runtime_mode_fail_closed(monkeypatch):
    monkeypatch.setenv('FIXFLOW_CUSTOMER_INDEX_MODE', 'unknown')
    with pytest.raises(ValueError):
        customer_mode()
    monkeypatch.setenv('FIXFLOW_CUSTOMER_INDEX_MODE', 'approved_only')
    assert customer_mode() == 'approved_only'


def test_runtime_entrypoints_connected():
    for path in ('backend/services/help_center.py', 'backend/worker/main.py'):
        source = (ROOT / path).read_text(encoding='utf-8')
        assert 'create_customer_retriever()' in source
        ast.parse(source)
    catalog = (ROOT / 'backend/services/citation_catalog.py').read_text(encoding='utf-8')
    assert 'verify_index(DEFAULT_INDEX)' in catalog
    assert 'customer_mode() == "approved_only"' in catalog


def test_guard_retains_exact_approved_safety_excerpt():
    source = (ROOT / 'backend/services/grounding_guard.py').read_text(encoding='utf-8')
    assert 'if is_approved_customer_passage(passage):' in source
    assert 'safe.append(dict(passage))' in source


def test_no_implicit_index_build_or_activation():
    runtime = (ROOT / 'backend/rag/customer_runtime.py').read_text(encoding='utf-8')
    assert 'build_staging(' not in runtime
    assert 'activate(' not in runtime
    assert 'rollback(' not in runtime


def test_temp_directory_activation_and_rollback(monkeypatch, tmp_path):
    from backend.rag import customer_index
    monkeypatch.setattr(customer_index, 'verify_index', lambda _: ({}, None))
    active, stage, backup = (tmp_path / n for n in ('active', 'stage', 'backup'))
    active.mkdir()
    (active / 'marker').write_text('old')
    stage.mkdir()
    (stage / 'marker').write_text('new')
    with pytest.raises(PermissionError):
        customer_index.activate(stage, active_dir=active, backup_dir=backup)
    assert (active / 'marker').read_text() == 'old'
    result = customer_index.activate(stage, active_dir=active, backup_dir=backup, approved=True)
    assert (active / 'marker').read_text() == 'new'
    assert (backup / 'marker').read_text() == 'old'
    with pytest.raises(PermissionError):
        customer_index.rollback(active_dir=active, backup_dir=backup)
    result = customer_index.rollback(active_dir=active, backup_dir=backup, approved=True)
    assert (active / 'marker').read_text() == 'old'
    assert (Path(result['displaced']) / 'marker').read_text() == 'new'


def test_activation_failure_restores_old_index(monkeypatch, tmp_path):
    from backend.rag import customer_index
    active, stage, backup = (tmp_path / n for n in ('active', 'stage', 'backup'))
    active.mkdir()
    (active / 'marker').write_text('old')
    stage.mkdir()
    (stage / 'marker').write_text('new')
    def verify(path):
        if Path(path) == active:
            raise ValueError('simulated verification failure')
        return {}, None
    monkeypatch.setattr(customer_index, 'verify_index', verify)
    with pytest.raises(ValueError, match='simulated'):
        customer_index.activate(stage, active_dir=active, backup_dir=backup, approved=True)
    assert (active / 'marker').read_text() == 'old'


# ======================================================================
# From test_phase2e5_agent_worker_offline.py
# ======================================================================

@pytest.fixture
def approved_only(monkeypatch):
    monkeypatch.setenv('FIXFLOW_CUSTOMER_INDEX_MODE', 'approved_only')


@pytest.fixture
def approved_docs():
    manifest, index = verify_index(DEFAULT_INDEX)
    assert index.ntotal == len(manifest["chunks"]) == len(approved_passages())
    assert manifest['chunks'] == approved_passages()
    return manifest['chunks']


@pytest.fixture
def mock_agents(monkeypatch):
    agents = []
    class FakeAgent:
        def __init__(self, name, *, model_client, system_message, tools=None, **_options):
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


def fashion_return_doc(approved_docs):
    matches = [d for d in approved_docs if 'Fashion and apparel' in d['text'] and '14-day' in d['text']]
    assert len(matches) == 1, 'Expected one approved fashion return-window passage'
    return matches[0]


@pytest.mark.usefixtures('approved_only')
def test_approved_only_four_agent_handoff(approved_docs, mock_agents):
    doc = fashion_return_doc(approved_docs)
    team, _, tools, calls, retriever = _build(
        mock_agents, [doc], 'fashion apparel return window')
    assert [a.name for a in team.members] == [
        'QueryAgent', 'KnowledgeAgent', 'ResolutionAgent', 'LogAgent']
    assert retriever.calls == ['fashion apparel return window']
    for name in ('KnowledgeAgent', 'ResolutionAgent', 'LogAgent'):
        prompt = next(a.system_message for a in mock_agents if a.name == name)
        assert doc['id'] in prompt
    # Production order: ResolutionAgent records the structured handoff first.
    assert asyncio.run(tools['record_resolution_handoff'](
        customer_draft='Fashion and apparel have a 14-day return window.',
        supporting_policy_ids=doc['id'], needs_human_review=False)) == 'HANDOFF_RECORDED'
    assert asyncio.run(tools['persist_outcome'](
        summary='Fashion returns', proposed_response='Fashion and apparel have a 14-day return window.',
        cited_ids=doc['id'], escalate=False)) == 'LOGGED'
    assert calls[-1][4] == [doc['id']]
    assert calls[-1][5] is False


@pytest.mark.usefixtures('approved_only')
@pytest.mark.parametrize('bad_id', ['', 'fabricated-policy-id'])
def test_agent_rejects_missing_or_invented_citation(approved_docs, mock_agents, bad_id):
    _, _, tools, calls, _ = _build(mock_agents, [approved_docs[0]], 'fashion return window')
    assert asyncio.run(tools['persist_outcome'](
        summary='Unverified', proposed_response='A refund is guaranteed.',
        cited_ids=bad_id, escalate=False)) == 'LOGGED'
    assert calls[-1][4] == []
    assert calls[-1][5] is True
    assert 'human review' in calls[-1][3].lower()


@pytest.mark.usefixtures('approved_only')
def test_agent_shipping_without_approved_source_fails_closed(approved_docs, mock_agents):
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


HOLDING = 'No refund or replacement has been approved or processed.'


def assert_safe_holding_message(messages, *, forbidden=()):
    """An escalated ticket publishes exactly one customer-safe holding message:
    it requests human review, states that no remedy was approved, and never
    carries the rejected draft's claims. (Earlier design published nothing.)"""
    assert len(messages) == 1
    text = messages[0]['content']
    assert 'human review' in text.lower()
    assert HOLDING in text
    for phrase in forbidden:
        assert phrase.lower() not in text.lower()
    return text


@pytest.mark.usefixtures('approved_only')
def test_worker_approved_passage_publishes_once(isolated_db, approved_docs):
    tid, jid = seed_ticket()
    row = worker.claim()
    doc = fashion_return_doc(approved_docs)
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


@pytest.mark.usefixtures('approved_only')
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
    assert json.loads(result['citations']) == []
    assert_safe_holding_message(messages, forbidden=('Unlimited refunds', 'guaranteed'))


@pytest.mark.usefixtures('approved_only')
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
    assert json.loads(result['citations']) == []
    assert_safe_holding_message(messages, forbidden=('We guarantee a refund',))


@pytest.mark.usefixtures('approved_only')
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
    text = assert_safe_holding_message(messages)
    assert 'INR 500 or more' in text


@pytest.mark.usefixtures('approved_only')
def test_worker_rejects_stale_lease(isolated_db, approved_docs):
    tid, jid = seed_ticket()
    row = worker.claim()
    doc = approved_docs[0]
    with pytest.raises(RuntimeError, match='Stale or unauthorized'):
        worker.persist(jid, tid, 'Old job', 'Policy text', [doc['id']], False,
                       {doc['id']: doc}, lease='invalid-lease', input_version=row['version'])
    assert get_state(tid, jid)[1] is None


# ======================================================================
# From test_phase2e5_approved_only_offline.py
# ======================================================================

@pytest.fixture
def approved_only_phase2e5_approved_only_offline(monkeypatch):
    monkeypatch.setenv("FIXFLOW_CUSTOMER_INDEX_MODE", "approved_only")
    assert customer_mode() == "approved_only"


@pytest.fixture(scope="module")
def approved_index():
    if not Path(DEFAULT_INDEX).is_dir():
        pytest.fail("Activated local_customer_index missing: do not fall back to legacy")
    return verify_index(DEFAULT_INDEX)


@pytest.fixture
def offline_retriever(approved_index):
    _, index = approved_index
    calls = []

    def offline_embed(query):
        calls.append(query)
        return index.reconstruct(0).tolist()

    retriever = create_customer_retriever(embed_query=offline_embed, index_dir=DEFAULT_INDEX)
    assert isinstance(retriever, CustomerHybridRetriever)
    return retriever, calls


@pytest.mark.usefixtures('approved_only_phase2e5_approved_only_offline')
def test_manifest_exactly_matches_curated_allowlist(approved_index):
    manifest, index = approved_index
    expected = {doc["id"]: doc for doc in approved_passages()}
    actual = {doc["id"]: doc for doc in manifest["chunks"]}
    assert len(expected) == len(actual) == index.ntotal == len(approved_passages())
    assert actual == expected
    assert all(is_approved_customer_passage(doc) for doc in actual.values())
    assert manifest["kind"] == "approved-customer-only-v1"
    assert manifest["dimension"] == index.d == 1536


@pytest.mark.usefixtures('approved_only_phase2e5_approved_only_offline')
def test_tampered_metadata_and_text_are_rejected(approved_index):
    manifest, _ = approved_index
    source = manifest["chunks"][0]
    for field, value in [("text", "Different policy"), ("approval", "unapproved"),
                         ("visibility", "internal"), ("source", "unknown.pdf"),
                         ("id", "fabricated")]:
        changed = dict(source, **{field: value})
        assert not is_approved_customer_passage(changed), field


@pytest.mark.usefixtures('approved_only_phase2e5_approved_only_offline')
def test_retrieval_uses_approved_documents_only(offline_retriever):
    retriever, calls = offline_retriever
    results = retriever.search("fashion apparel return", k=4)
    assert results, "Known relevant approved topic unexpectedly returned no evidence"
    assert len(results) <= 4
    assert all(is_approved_customer_passage(doc) for doc in results)
    assert calls, "FAISS embedding callback was not exercised"


@pytest.mark.usefixtures('approved_only_phase2e5_approved_only_offline')
def test_unrelated_question_fails_closed_without_embedding(offline_retriever):
    retriever, calls = offline_retriever
    assert retriever.search("spaceship teleportation", k=4) == []
    assert calls == [], "Irrelevant topic should be rejected before embedding"


@pytest.mark.usefixtures('approved_only_phase2e5_approved_only_offline')
def test_internal_retrieval_is_denied(offline_retriever):
    retriever, calls = offline_retriever
    with pytest.raises(PermissionError):
        retriever.search("refund", include_internal=True)
    assert calls == []


@pytest.mark.usefixtures('approved_only_phase2e5_approved_only_offline')
def test_citations_are_approved_display_metadata_only(approved_index):
    manifest, _ = approved_index
    doc = manifest["chunks"][0]
    citations = citation_catalog.resolve([doc["id"], "unauthorized-internal-id"])
    assert len(citations) == 1
    citation = citations[0]
    assert citation["id"] == doc["id"]
    assert citation["source"] == doc["source"]
    assert citation["page"] == doc["page"]
    assert citation["visibility"] == "customer"
    assert "text" not in citation
    assert "approval" not in citation  # Citation metadata is not a full passage.


@pytest.mark.usefixtures('approved_only_phase2e5_approved_only_offline')
def test_citation_resolution_fails_closed_on_invalid_index():
    with patch.object(citation_catalog, "verify_index", side_effect=ValueError("invalid manifest")):
        assert citation_catalog.resolve(["anything"]) == []


class StaticSearch:
    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    def search(self, question, k=4):
        self.calls.append((question, k))
        return self.docs[:k]


@pytest.mark.usefixtures('approved_only_phase2e5_approved_only_offline')
def test_help_center_answers_grounded_approved_question(approved_index):
    manifest, _ = approved_index
    docs = [d for d in manifest["chunks"] if "Fashion and apparel" in d["text"]]
    assert len(docs) == 1
    search = StaticSearch(docs)
    result = help_center.answer_question("fashion apparel return window", search=search)
    assert result["status"] == "answered", result
    assert result["sources"]
    assert {item["chunk_id"] for item in result["sources"]} == {docs[0]["id"]}
    assert search.calls


@pytest.mark.usefixtures('approved_only_phase2e5_approved_only_offline')
def test_help_center_rejects_fabricated_customer_passage(approved_index):
    manifest, _ = approved_index
    forged = dict(manifest["chunks"][0], text="Free lifetime unlimited refunds for everyone.")
    result = help_center.answer_question("free lifetime unlimited refunds", search=StaticSearch([forged]))
    assert result["status"] == "no_evidence", result
    assert result["sources"] == []


@pytest.mark.usefixtures('approved_only_phase2e5_approved_only_offline')
def test_help_center_requires_ticket_for_personal_refund(approved_index):
    manifest, _ = approved_index
    search = StaticSearch(manifest["chunks"])
    result = help_center.answer_question("refund my order 12345", search=search)
    assert result["status"] == "ticket_required", result
    assert result["sources"] == []
    assert not search.calls


# ======================================================================
# From test_phase2e5_diagnostic_independence.py
# ======================================================================

@pytest.fixture
def approved_mode(monkeypatch):
    monkeypatch.setenv('FIXFLOW_CUSTOMER_INDEX_MODE', 'approved_only')


@pytest.mark.usefixtures('approved_mode')
def test_fabricated_citation_cannot_hide_claimed_refund():
    evidence = {'fake': {'id': 'fake', 'visibility': 'customer', 'text': 'Refunds are available.'}}
    result = inspect_response('Refund?', 'We have issued your refund.', ['fake'], evidence)
    assert 'UNSAFE_OR_EMPTY_CITATION' in result.codes
    assert 'UNVERIFIED_COMPLETED_ACTION' in result.codes


@pytest.mark.usefixtures('approved_mode')
def test_fabricated_citation_cannot_hide_delivery_promise():
    evidence = {'fake': {'id': 'fake', 'visibility': 'customer', 'text': 'Shipping details.'}}
    result = inspect_response('Delivery?', 'Delivery takes 3 days.', ['fake'], evidence)
    assert 'UNSAFE_OR_EMPTY_CITATION' in result.codes
    assert 'UNSUPPORTED_DELIVERY_TIME' in result.codes


@pytest.mark.usefixtures('approved_mode')
def test_invalid_citation_never_counts_as_approved():
    result = inspect_response('Refund?', 'Please request human review.', ['invented'], {})
    assert 'UNKNOWN_CITATION' in result.codes
    assert 'UNVERIFIED_COMPLETED_ACTION' not in result.codes


# ======================================================================
# From test_retriever_reuse_and_citation_labels.py
# ======================================================================

@pytest.fixture
def clean_cache():
    runtime.clear_retriever_cache()
    yield
    runtime.clear_retriever_cache()


@pytest.mark.usefixtures('clean_cache')
def test_production_approved_retriever_constructed_once(monkeypatch):
    monkeypatch.setenv('FIXFLOW_CUSTOMER_INDEX_MODE', 'approved_only')
    created = []
    def factory(**kwargs):
        obj = object()
        created.append(obj)
        return obj
    monkeypatch.setattr(runtime, '_new_approved_retriever', factory)
    assert runtime.create_customer_retriever() is runtime.create_customer_retriever()
    assert len(created) == 1


@pytest.mark.usefixtures('clean_cache')
def test_injected_retrievers_are_not_cached(monkeypatch):
    monkeypatch.setenv('FIXFLOW_CUSTOMER_INDEX_MODE', 'approved_only')
    monkeypatch.setattr(runtime, '_new_approved_retriever', lambda **kwargs: object())
    provider = lambda text: [1.0]
    assert runtime.create_customer_retriever(embed_query=provider) is not runtime.create_customer_retriever(embed_query=provider)


@pytest.mark.usefixtures('clean_cache')
def test_invalid_mode_cannot_reuse_cached_retriever(monkeypatch):
    monkeypatch.setenv('FIXFLOW_CUSTOMER_INDEX_MODE', 'approved_only')
    monkeypatch.setattr(runtime, '_new_approved_retriever', lambda **kwargs: object())
    runtime.create_customer_retriever()
    monkeypatch.setenv('FIXFLOW_CUSTOMER_INDEX_MODE', 'invalid')
    with pytest.raises(ValueError):
        runtime.create_customer_retriever()


@pytest.mark.usefixtures('clean_cache')
def test_cache_clear_reloads(monkeypatch):
    monkeypatch.setenv('FIXFLOW_CUSTOMER_INDEX_MODE', 'approved_only')
    monkeypatch.setattr(runtime, '_new_approved_retriever', lambda **kwargs: object())
    old = runtime.create_customer_retriever()
    runtime.clear_retriever_cache()
    assert runtime.create_customer_retriever() is not old


@pytest.mark.usefixtures('clean_cache')
def test_citation_metadata_preserves_document_page_department():
    pytest.importorskip('faiss')
    from backend.rag.hybrid import citation_metadata
    meta = citation_metadata({'id': 'test', 'source': '03_Product_and_Warranty_SOP.pdf',
                              'page': 2, 'chunk': 4, 'visibility': 'customer'})
    assert meta['source'] == '03_Product_and_Warranty_SOP.pdf'
    assert meta['page'] == 2 and meta['chunk'] == 4
    assert meta['department'] == 'Product & Warranty'
    assert meta['scheme'] == 'NovaCart Support SOP'
    assert meta['citation_label'] == '03_Product_and_Warranty_SOP.pdf, page 2'
