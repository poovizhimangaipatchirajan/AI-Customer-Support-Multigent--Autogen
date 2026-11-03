from backend.services.evidence_quality import missing_citation_domains
from backend.services.grounding_guard import customer_safe_passages


def _p(id, source, page, text):
    return {"id": id, "source": source, "page": page, "visibility": "customer", "text": text}


def test_shipping_and_returns_require_both_domains():
    question = "How long does delivery take, and how can I return it?"
    draft = "The SOP does not specify a universal delivery time. Returns depend on category."
    returns = _p("r", "02_Returns_and_Refunds_SOP.pdf", 1, "Returns depend on category")
    shipping = _p("s", "01_Shipping_and_Delivery_SOP.pdf", 1, "Estimated Delivery Date (EDD)")
    assert missing_citation_domains(question, draft, [returns]) == ("shipping",)
    assert missing_citation_domains(question, draft, [returns, shipping]) == ()


def test_known_internal_pages_and_addendum_do_not_reach_agent():
    raw = [
        _p("bad", "01_Shipping_and_Delivery_SOP.pdf", 4, "Internal compensation macro"),
        _p("add", "02_Returns_and_Refunds_SOP.pdf", 5,
           "FIXFLOW AI | IMPLEMENTATION GUIDANCE ADDENDUM | Not a policy amendment"),
        _p("good", "02_Returns_and_Refunds_SOP.pdf", 1, "Fashion 14 Days"),
    ]
    assert [p["id"] for p in customer_safe_passages(raw)] == ["good"]
