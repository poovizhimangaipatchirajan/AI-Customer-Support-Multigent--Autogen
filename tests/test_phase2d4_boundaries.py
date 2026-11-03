"""Focused regression tests; no API, database, or model calls."""
from pathlib import Path
import ast

from backend.services.grounding_guard import customer_safe_passages


def _p(source, page, text, ident='x'):
    return {'id': ident, 'source': source, 'page': page,
            'visibility': 'customer', 'text': text}


def test_internal_guidance_addendum_excluded():
    assert customer_safe_passages([_p('02_Returns_and_Refunds_SOP.pdf', 5,
        'FIXFLOW AI | IMPLEMENTATION GUIDANCE ADDENDUM | Not a policy amendment')]) == []


def test_sensitive_operational_pages_excluded_even_if_mislabeled():
    for source, page in [('01_Shipping_and_Delivery_SOP.pdf', 3),
                         ('01_Shipping_and_Delivery_SOP.pdf', 4),
                         ('02_Returns_and_Refunds_SOP.pdf', 3),
                         ('03_Product_and_Warranty_SOP.pdf', 4)]:
        assert customer_safe_passages([_p(source, page, 'Operational procedures')]) == []


def test_customer_policy_kept():
    p = _p('01_Shipping_and_Delivery_SOP.pdf', 1, 'Promised Estimated Delivery Date (EDD).')
    assert customer_safe_passages([p]) == [p]


def test_worker_keeps_fail_closed_publication_boundary():
    source = Path(__file__).resolve().parents[1] / 'backend' / 'worker' / 'main.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert {'persist', 'finish'} <= functions.keys()
    body = ast.get_source_segment(source.read_text(encoding='utf-8'), functions['finish'])
    assert 'if result and not escalate:' in body
    persist = ast.get_source_segment(source.read_text(encoding='utf-8'), functions['persist'])
    assert 'MISSING_DOMAIN_CITATION' in persist
    assert 'UNSAFE_POLICY_PASSAGE' in persist
