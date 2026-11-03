"""Offline security tests; no FAISS, OpenAI key, or network required."""
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "customer_policy_under_test", ROOT / "backend/rag/customer_policy.py"
)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


def test_curated_excerpt_count():
    assert len(policy.approved_passages()) >= 10


def test_approved_excerpts_have_real_provenance():
    for passage in policy.approved_passages():
        assert passage["source"].endswith(".pdf")
        assert passage["page"] >= 1
        assert policy.is_approved_customer_passage(passage)


def test_legacy_customer_label_not_enough():
    legacy = {"id": "legacy", "visibility": "customer", "text": "internal"}
    assert not policy.is_approved_customer_passage(legacy)


def test_forged_id_cannot_override_text():
    forged = policy.approved_passages()[0].copy()
    forged["text"] = "Internal discretionary compensation matrix"
    assert not policy.is_approved_customer_passage(forged)


def test_forged_id_cannot_override_page():
    forged = policy.approved_passages()[0].copy()
    forged["page"] = 4
    assert not policy.is_approved_customer_passage(forged)


def test_forged_id_cannot_override_source():
    forged = policy.approved_passages()[0].copy()
    forged["source"] = "06_Escalations_and_Soft_Skills_SOP.pdf"
    assert not policy.is_approved_customer_passage(forged)


def test_internal_content_not_in_approved_excerpts():
    content = " ".join(p["text"] for p in policy.approved_passages()).lower()
    for secret in ("kill_session_and_lock", "goodwill credit", "fraud score",
                   "geofence audit", "csr override", "trust metric",
                   "we have dispatched", "representment evidence"):
        assert secret not in content


def test_safe_return_policy_available():
    content = " ".join(p["text"] for p in policy.approved_passages()).lower()
    assert "7-day return window" in content
    assert "14-day return window" in content
    assert "photographs" in content


def test_no_raw_shipping_or_escalation_pages_approved():
    sources = {p["source"] for p in policy.approved_passages()}
    assert "01_Shipping_and_Delivery_SOP.pdf" not in sources
    assert "06_Escalations_and_Soft_Skills_SOP.pdf" not in sources


def test_tampered_visibility_is_rejected():
    forged = policy.approved_passages()[0].copy()
    forged["visibility"] = "internal"
    assert not policy.is_approved_customer_passage(forged)
