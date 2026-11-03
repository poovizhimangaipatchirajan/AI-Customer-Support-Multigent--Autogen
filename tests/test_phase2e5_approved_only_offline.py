"""Phase 2E.5 offline, customer-approved index security tests.

Requires activated local_customer_index. Never calls OpenAI or starts services.
"""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.rag.customer_index import CustomerHybridRetriever, verify_index, DEFAULT_INDEX
from backend.rag.customer_policy import approved_passages, is_approved_customer_passage
from backend.rag.customer_runtime import create_customer_retriever, customer_mode
from backend.services import citation_catalog, help_center


@pytest.fixture(autouse=True)
def approved_only(monkeypatch):
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


def test_manifest_exactly_matches_curated_allowlist(approved_index):
    manifest, index = approved_index
    expected = {doc["id"]: doc for doc in approved_passages()}
    actual = {doc["id"]: doc for doc in manifest["chunks"]}
    assert len(expected) == len(actual) == index.ntotal == 12
    assert actual == expected
    assert all(is_approved_customer_passage(doc) for doc in actual.values())
    assert manifest["kind"] == "approved-customer-only-v1"
    assert manifest["dimension"] == index.d == 1536


def test_tampered_metadata_and_text_are_rejected(approved_index):
    manifest, _ = approved_index
    source = manifest["chunks"][0]
    for field, value in [("text", "Different policy"), ("approval", "unapproved"),
                         ("visibility", "internal"), ("source", "unknown.pdf"),
                         ("id", "fabricated")]:
        changed = dict(source, **{field: value})
        assert not is_approved_customer_passage(changed), field


def test_retrieval_uses_approved_documents_only(offline_retriever):
    retriever, calls = offline_retriever
    results = retriever.search("fashion apparel return", k=4)
    assert results, "Known relevant approved topic unexpectedly returned no evidence"
    assert len(results) <= 4
    assert all(is_approved_customer_passage(doc) for doc in results)
    assert calls, "FAISS embedding callback was not exercised"


def test_unrelated_question_fails_closed_without_embedding(offline_retriever):
    retriever, calls = offline_retriever
    assert retriever.search("spaceship teleportation", k=4) == []
    assert calls == [], "Irrelevant topic should be rejected before embedding"


def test_internal_retrieval_is_denied(offline_retriever):
    retriever, calls = offline_retriever
    with pytest.raises(PermissionError):
        retriever.search("refund", include_internal=True)
    assert calls == []


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


def test_help_center_rejects_fabricated_customer_passage(approved_index):
    manifest, _ = approved_index
    forged = dict(manifest["chunks"][0], text="Free lifetime unlimited refunds for everyone.")
    result = help_center.answer_question("free lifetime unlimited refunds", search=StaticSearch([forged]))
    assert result["status"] == "no_evidence", result
    assert result["sources"] == []


def test_help_center_requires_ticket_for_personal_refund(approved_index):
    manifest, _ = approved_index
    search = StaticSearch(manifest["chunks"])
    result = help_center.answer_question("refund my order 12345", search=search)
    assert result["status"] == "ticket_required", result
    assert result["sources"] == []
    assert not search.calls
