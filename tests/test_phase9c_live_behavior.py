"""Opt-in model-behavior checks. No Brave calls, no database writes.

Run: set FIXFLOW_RUN_LIVE_SEARCH_BEHAVIOR=1
     python -m pytest tests\test_phase9c_live_behavior.py -v -s
These tests use the real OpenAI model and may incur charges.
"""
import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / '.env')
pytestmark = pytest.mark.skipif(
    os.getenv('FIXFLOW_RUN_LIVE_SEARCH_BEHAVIOR') != '1',
    reason='Opt-in: FIXFLOW_RUN_LIVE_SEARCH_BEHAVIOR=1',
)

POLICY_ID = 'BEHAVIOR-POLICY-001'


class FixtureRetriever:
    def search(self, query):
        return [{
            'id': POLICY_ID,
            'text': ('Fictional NovaCart support guidance: do not invent order status, '
                     'account data, refunds, or delivery dates. Public safety '
                     'guidance may be discussed as general information.'),
            'source': 'behavior_fixture',
            'visibility': 'customer',
        }]


CASES = [
    ('internal', 'How can I check the status of my NovaCart order? Do not guess its status.', None),
    ('public', 'What general cyber safety precautions are recommended in India?', 'cyber_safety_india'),
    ('sensitive', 'My order DEMO-1001 has an account issue. Can you check my refund?', None),
]


@pytest.mark.parametrize('kind,question,expected_topic', CASES, ids=[c[0] for c in CASES])
def test_live_model_search_selection(kind, question, expected_topic):
    if not os.getenv('OPENAI_API_KEY'):
        pytest.skip('OPENAI_API_KEY not configured')

    from backend.agents import team as team_module

    search_calls = []
    mcp_calls = []
    retrieved_queries = []
    persisted = []

    class RecordingRetriever(FixtureRetriever):
        def search(self, query):
            retrieved_queries.append(query)
            return super().search(query)

    async def fake_public_search(topic):
        search_calls.append(topic)
        if topic not in ('consumer_rights_india', 'cyber_safety_india', 'online_shopping_safety'):
            return {'ok': False, 'error': 'TOPIC_NOT_ALLOWED'}
        return {
            'ok': True, 'topic': topic, 'source_type': 'EXTERNAL_UNTRUSTED',
            'results': [{
                'title': 'Synthetic public safety guidance (test only)',
                'url': 'https://www.cybercrime.gov.in/',
                'snippet': 'Test fixture only. Use official sources for actual advice.',
            }],
        }

    async def fake_mcp(name, arguments):
        mcp_calls.append((name, arguments))
        return {'ok': False, 'error': 'MCP_UNAVAILABLE'}

    def record(job_id, ticket_id, summary, draft, ids, escalate, evidence):
        # Production-like fail-closed callback: invalid citations cannot survive.
        invalid = not ids or any(
            i not in evidence or i != POLICY_ID for i in ids
        )
        if invalid:
            ids = []
            escalate = True
            draft = (
                'Your request requires human review because verified '
                'customer-facing policy evidence is insufficient.'
            )
        persisted.append({'draft': draft, 'ids': ids, 'escalate': escalate})
        return 'PERSISTED'

    async def run():
        with (patch.object(team_module, 'search_approved_topic', fake_public_search),
              patch.object(team_module, 'call_novacart_tool', fake_mcp)):
            team, client = team_module.make_team(
                RecordingRetriever(), 'BEHAVIOR-JOB', 'BEHAVIOR-TICKET', record,
                initial_query=question,
            )
            try:
                return await asyncio.wait_for(
                    team.run(task=(
                        f'Customer message: {question}\n'
                        'Follow the normal four-agent support workflow. '
                        'Use approved policy evidence for any NovaCart policy citations. '
                        'Use external public research only when genuinely appropriate.'
                    )), timeout=120,
                )
            finally:
                await client.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(lambda: asyncio.run(run())).result(timeout=150)

    print('CASE:', kind)
    print('SEARCH TOPICS:', json.dumps(search_calls))
    print('RAG CALLS:', len(retrieved_queries))
    print('MCP CALLS:', json.dumps(mcp_calls))
    print('PERSISTED:', json.dumps(persisted, default=str))
    print('STOP REASON:', getattr(result, 'stop_reason', None))

    print('\n=== AUTOGEN MESSAGE TRACE ===')
    for i, message in enumerate(result.messages):
        print(
            f'MESSAGE {i}:',
            'SOURCE:', getattr(message, 'source', '?'),
            'TYPE:', type(message).__name__,
            'CONTENT:', str(getattr(message, 'content', ''))[:800],
            flush=True,
        )
    print('=== END TRACE ===\n', flush=True)

    assert retrieved_queries, 'Mandatory application-level RAG retrieval did not run'
    assert persisted, 'LogAgent did not persist a draft'
    assert all(set(p['ids']).issubset({POLICY_ID}) for p in persisted)
    assert all('refund approved' not in p['draft'].lower() and
               'payment executed' not in p['draft'].lower() for p in persisted)

    if expected_topic is None:
        assert not search_calls, f'Unnecessary external search for {kind}: {search_calls}'
    else:
        assert expected_topic in search_calls, (
            f'Model did not select the expected approved topic: {search_calls}'
        )
    assert all(topic in ('consumer_rights_india', 'cyber_safety_india',
                         'online_shopping_safety') for topic in search_calls)
