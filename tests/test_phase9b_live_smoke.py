"""Opt-in LIVE Phase 9B smoke test. Uses real OpenAI API and local MCP subprocess.

Run: python -m pytest tests/test_phase9b_live_smoke.py -v -s
Requires FIXFLOW_RUN_LIVE_MCP=1 and OPENAI_API_KEY. No database writes.
"""
import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')


class DemoRetriever:
    def search(self, query):
        # Test-only evidence. Never use this mock as proof of real RAG quality.
        return [{
            'id': 'SMOKE-POLICY-001',
            'text': 'Test fixture: product information may be provided without asserting live order status.',
            'source': 'test_fixture',
            'visibility': 'customer',
        }]


@pytest.mark.skipif(
    os.getenv('FIXFLOW_RUN_LIVE_MCP') != '1',
    reason='Opt-in only: set FIXFLOW_RUN_LIVE_MCP=1',
)
def test_real_autogen_calls_mcp_and_persists_audited_draft():
    if not os.getenv('OPENAI_API_KEY'):
        pytest.skip('OPENAI_API_KEY not configured')

    from backend.agents import team as team_module

    original_call = team_module.call_novacart_tool
    mcp_calls = []
    persisted = []

    async def tracked_call(name, arguments):
        response = await original_call(name, arguments)
        mcp_calls.append((name, arguments, response))
        return response

    def audit_callback(job_id, ticket_id, summary, draft, ids, escalate, evidence):
        # Test-only persistence callback: checks evidence provenance, not SQLite writes.
        assert job_id == 'SMOKE-JOB' and ticket_id == 'SMOKE-TICKET'
        assert all(item in evidence for item in ids)
        assert all(item.startswith('SMOKE-POLICY-') for item in ids)
        persisted.append({'summary': summary, 'draft': draft, 'ids': ids,
                          'escalate': escalate})
        return 'PERSISTED'

    async def run():
        with patch.object(team_module, 'call_novacart_tool', tracked_call):
            agents, client = team_module.make_team(
                DemoRetriever(), 'SMOKE-JOB', 'SMOKE-TICKET', audit_callback
            )
            try:
                result = await asyncio.wait_for(
                    agents.run(task=(
                        'Customer asks: What is the fictional NovaCart demo product '
                        'MOUSE-01? KnowledgeAgent: call policy_search AND '
                        'get_product_information(product_id="MOUSE-01") before '
                        'drafting. Do not claim live stock or order status. '
                        'LogAgent: persist_outcome with real policy_search IDs.'
                    )), timeout=120,
                )
                return result
            finally:
                await client.close()

    # Separate event loop avoids pytest/AnyIO nested-loop errors.
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(lambda: asyncio.run(run())).result(timeout=150)

    print('MCP calls:', json.dumps(mcp_calls, default=str))
    print('Persisted:', json.dumps(persisted, default=str))
    print('Stop reason:', getattr(result, 'stop_reason', None))

    assert any(name == 'get_product_information' and
               args.get('product_id') == 'MOUSE-01' and
               response.get('ok') is True
               for name, args, response in mcp_calls), 'Real MCP tool was not successfully invoked'
    assert persisted, 'LogAgent did not call persist_outcome'
    assert all(set(p['ids']) <= {'SMOKE-POLICY-001'} for p in persisted)
    assert not any('refund approved' in p['draft'].lower() or
                   'payment executed' in p['draft'].lower() for p in persisted)
