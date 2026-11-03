from backend.rag.customer_index import verify_index
from backend.rag.customer_policy import is_approved_customer_passage
from backend.services.citation_catalog import resolve


def test_approved_only_citation_isolation(monkeypatch):
    monkeypatch.setenv(
        "FIXFLOW_CUSTOMER_INDEX_MODE",
        "approved_only",
    )

    manifest, index = verify_index("local_customer_index")

    assert index.ntotal == 12

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