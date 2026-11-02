"""Deterministic contract tests for shipping-specific AutoGen prompt guidance.

No API calls, no database, no retriever changes. Run from project root.
"""
import ast
from pathlib import Path

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
