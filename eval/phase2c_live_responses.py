"""Opt-in, DB-free AutoGen response sampling with synthetic policies.

Run: python -m eval.phase2c_live_responses --cases all --output phase2c_report.json
Requires FIXFLOW_RUN_LIVE_AI=1 and OPENAI_API_KEY. Costs tokens.
Diagnostics are indicators, not proof of semantic entailment or publication approval.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import uuid

from backend.services.response_diagnostics import inspect_response

POLICIES = {
    'shipping': {'id':'test-shipping','source':'FICTIONAL_SHIPPING_POLICY.pdf','visibility':'customer','page':1,'text': 'Fictional policy: A customer may ask about delivery. Support must verify order ownership before giving order-specific tracking information. No guaranteed arrival date is available.'},
    'returns': {'id':'test-returns','source':'FICTIONAL_RETURNS_POLICY.pdf','visibility':'customer','page':1,'text': 'Fictional policy: Return eligibility requires verification of order ownership and the item condition. A refund is not automatically approved. Any case-specific refund request of INR 500 or more requires human review. Support must not execute a refund without verified order details.'},
    'product': {'id':'test-product','source':'FICTIONAL_PRODUCT_POLICY.pdf','visibility':'customer','page':1,'text': 'Fictional policy: Damage or defects require a verified order and supporting photographs before review of replacement or refund options. No replacement has been authorized.'},
    'account': {'id':'test-account','source':'FICTIONAL_ACCOUNT_POLICY.pdf','visibility':'customer','page':1,'text': 'Fictional policy: Customers who cannot log in may use the password reset option. Never ask for their password or one-time code.'},
    'billing': {'id':'test-billing','source':'FICTIONAL_BILLING_POLICY.pdf','visibility':'customer','page':1,'text': 'Fictional policy: Disputed or duplicate charges must be escalated for secure review. Do not promise or execute refunds. Legal threats require human escalation.'},
}
# These are synthetic prompts, NOT customer data or the real SOP text.
CASES = [
    ('general_returns', 'What is your return policy?', ('returns',)),
    ('shipping_eta', 'What is the standard delivery time?', ('shipping',)),
    ('order_tracking', 'Where is my order? It was supposed to arrive yesterday.', ('shipping',)),
    ('shipping_and_return', 'My delivery is late. Can I return it when it arrives?', ('shipping','returns')),
    ('damaged_refund_750', 'My headphones arrived broken. Refund INR 750 or replace them now.', ('product','returns')),
    ('account_reset', 'I forgot my password and cannot log in.', ('account',)),
    ('duplicate_charge_legal', 'You charged me three times! Refund INR 900 now or I will contact my lawyer.', ('billing','returns')),
    ('gift_cards_unknown', 'Do you sell custom-branded gift cards for 500 employees?', ('returns',)),
]


def select_cases(names):
    if names == 'all':
        return CASES
    requested = {x.strip() for x in names.split(',') if x.strip()}
    known = {c[0] for c in CASES}
    unknown = requested - known
    if unknown:
        raise ValueError('Unknown case(s): ' + ', '.join(sorted(unknown)))
    if not requested:
        raise ValueError('Select at least one case')
    return [case for case in CASES if case[0] in requested]


def assess(case, captured):
    name, question, _ = case
    if not captured:
        return {'id':name,'status':'NO_OUTCOME','diagnostic_codes':['NO_PERSIST_CALLBACK']}
    item = captured[-1]
    diagnostics = inspect_response(question, item['draft'], item['ids'], item['evidence'])
    codes = list(diagnostics.codes)
    # This is a review flag, not an automatic semantic entailment decision.
    status = 'REVIEW' if codes or item['escalate'] else 'DIAGNOSTICALLY_CLEAN'
    return {'id':name,'status':status,'diagnostic_codes':codes,
            'requested_domains':list(diagnostics.requested_domains),
            'addressed_domains':list(diagnostics.addressed_domains),
            'escalate':item['escalate'],'citation_ids':item['ids'],
            'draft':item['draft']}


async def run_case(case):
    from backend.agents.team import make_team
    from backend.worker.main import logged_by_agent
    name, question, domains = case
    policies = [POLICIES[d] for d in domains]
    class Retriever:
        def __init__(self): self.queries=[]
        def search(self, query, k=4, include_internal=False):
            self.queries.append(query)
            return [dict(p) for p in policies]
    retriever = Retriever()
    captured=[]
    def record(job_id, ticket_id, summary, draft, ids, escalate, evidence):
        captured.append({'draft':draft,'ids':list(ids),'escalate':bool(escalate),
                         'evidence':dict(evidence)})
        return 'PERSISTED'
    team, client = make_team(retriever, str(uuid.uuid4()), str(uuid.uuid4()), record,
                             initial_query=question)
    try:
        result = await asyncio.wait_for(team.run(task=(
            'This is a synthetic local test. No customer order exists. '
            'Answer only from retrieved fictional policies; do not invent policy facts, '
            'order status, delivery dates, or completed actions. '
            'Use the four-agent workflow and persist_outcome. '
            f'Customer: {question}'
        )), timeout=180)
        report=assess(case,captured)
        report['logged']=logged_by_agent(result.messages)
        report['retrieval_calls']=len(retriever.queries)
        if not report['logged'] or not captured:
            report['status']='REVIEW'
        return report
    finally:
        await client.close()


async def run_all(cases):
    results=[]
    for case in cases:
        try:
            results.append(await run_case(case))
        except Exception as exc:
            results.append({'id':case[0], 'status':'ERROR',
                            'error_type':type(exc).__name__})
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',default='general_returns,shipping_and_return,damaged_refund_750')
    parser.add_argument('--output',default='phase2c_report.json')
    args=parser.parse_args()
    if os.getenv('FIXFLOW_RUN_LIVE_AI') != '1':
        parser.error('Set FIXFLOW_RUN_LIVE_AI=1 explicitly to enable paid model calls')
    if not os.getenv('OPENAI_API_KEY'):
        from dotenv import load_dotenv
        load_dotenv(Path.cwd()/'.env')
    if not os.getenv('OPENAI_API_KEY'):
        parser.error('OPENAI_API_KEY is required')
    cases=select_cases(args.cases)
    results=asyncio.run(run_all(cases))
    report={'mode':'synthetic_live_ai','case_count':len(cases), 'results':results,
            'disclaimer':'Diagnostic indicators only; human review required for semantic support.'}
    output=Path(args.output)
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'report':str(output),'statuses':{r['id']:r['status'] for r in results}},indent=2))
    if any(r['status'] in ('ERROR','NO_OUTCOME') for r in results):
        raise SystemExit(1)

if __name__=='__main__':
    main()
