"""Opt-in FixFlow Phase 4 live integration tests.

Run from project root:
  set FIXFLOW_RUN_LIVE_AI=1
  python -m pytest tests\test_phase4_live_agents_rag.py -v -s

Requires OPENAI_API_KEY in project .env; incurs OpenAI usage charges.
Does not connect to or mutate FixFlow SQLite databases.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')

pytestmark = pytest.mark.skipif(
    os.getenv('FIXFLOW_RUN_LIVE_AI') != '1',
    reason='Live AI tests are opt-in: set FIXFLOW_RUN_LIVE_AI=1',
)


def require_key():
    if not os.getenv('OPENAI_API_KEY'):
        pytest.skip('OPENAI_API_KEY is not configured')


def test_real_hybrid_index_retrieval_and_internal_filter():
    """Uses real FAISS/BM25 index and OpenAI query embedding, but no customer data."""
    require_key()
    from backend.rag.hybrid import HybridRetriever, INDEX, APPROVED_POLICIES, INTERNAL_ONLY

    assert (INDEX / 'manifest.json').is_file(), 'Missing index: run python -m scripts.build_index first'
    assert (INDEX / 'dense.faiss').is_file(), 'Missing dense index: run python -m scripts.build_index first'
    retriever = HybridRetriever()
    assert {d['source'] for d in retriever.docs} == set(APPROVED_POLICIES)
    assert all(d['id'] and d['text'] and d['page'] >= 1 for d in retriever.docs)
    assert any(d['source'] in INTERNAL_ONLY for d in retriever.docs)
    results = retriever.search('What is the standard shipping and delivery policy?', k=4)
    assert results, 'Hybrid retrieval returned no evidence'
    assert all(d['source'] not in INTERNAL_ONLY for d in results)
    assert all(d['visibility'] == 'customer' for d in results)
    assert len({d['id'] for d in results}) == len(results)
    assert all(d['id'] in {x['id'] for x in retriever.docs} for d in results)
    print('\nRetrieved approved SOP evidence:')
    for d in results:
        print(f"  {d['id']} | {d['source']} | page {d['page']}")


def test_live_four_agent_workflow_with_synthetic_policy_evidence():
    """Real AutoGen model/tool orchestration; synthetic policy avoids claims about real SOPs.

    No database writes. Callback validates IDs against evidence observed by retrieval.
    Agent outcomes are probabilistic; failures need investigation, not automatic code edits.
    """
    require_key()
    from backend.agents.team import make_team
    from backend.worker.main import logged_by_agent

    policy = {
        'id': 'synthetic-shipping-001',
        'source': 'FICTIONAL_TEST_POLICY.pdf',
        'page': 1,
        'chunk': 1,
        'version': 'test-only',
        'visibility': 'customer',
        'text': ('Fictional testing policy: Customers may contact support about a '
                 'missing parcel. The support team must verify the order before '
                 'providing a delivery estimate. No tracking or delivery date is available.'),
    }
    class RecordingRetriever:
        def __init__(self):
            self.queries = []
        def search(self, query, k=4, include_internal=False):
            self.queries.append(query)
            return [policy]

    retriever = RecordingRetriever()
    saved = []

    def record_outcome(job_id, ticket_id, summary, proposed_response, cited_ids, escalate, evidence):
        assert job_id and ticket_id
        assert cited_ids and set(cited_ids) <= set(evidence), 'Unretrieved citation ID'
        assert all(evidence[i]['visibility'] == 'customer' for i in cited_ids)
        assert proposed_response.strip(), 'Empty proposed response'
        saved.append({'summary': summary, 'draft': proposed_response,
                      'ids': cited_ids, 'escalate': escalate})
        return 'PERSISTED'

    async def execute():
        team, client = make_team(retriever, str(uuid.uuid4()), str(uuid.uuid4()), record_outcome)
        try:
            return await asyncio.wait_for(team.run(task=(
                'This is a fictional local integration test, not a real customer order. '
                'A customer asks: My parcel has not arrived; can you confirm its delivery date? '
                'Use the policy_search tool, cite only the retrieved policy ID, '
                'do not invent an ETA, and log the response using persist_outcome.'
            )), timeout=180)
        finally:
            await client.close()

    # Run the async AutoGen workflow in a dedicated thread so this sync pytest
    # test also works when another pytest plugin has an active event loop.
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(lambda: asyncio.run(execute())).result(timeout=210)
    sources = [getattr(m, 'source', '') for m in result.messages]
    print('\nAgent sources:', sources)
    print('Termination:', getattr(result, 'stop_reason', None))
    assert retriever.queries, 'KnowledgeAgent did not call policy_search'
    assert saved, 'LogAgent did not persist a draft'
    assert logged_by_agent(result.messages), 'LogAgent did not acknowledge LOGGED'
    for agent in ('QueryAgent', 'KnowledgeAgent', 'ResolutionAgent', 'LogAgent'):
        assert agent in sources, f'{agent} missing from conversation'
    first = [sources.index(agent) for agent in
             ('QueryAgent', 'KnowledgeAgent', 'ResolutionAgent', 'LogAgent')]
    assert first == sorted(first), 'Unexpected first-turn agent order'
    assert saved[0]['ids'] == [policy['id']]
    draft = saved[0]['draft'].lower()
    assert 'delivery' in draft or 'parcel' in draft or 'order' in draft
    print('Saved citation IDs:', saved[0]['ids'])
    print('Draft preview:', saved[0]['draft'][:500])
