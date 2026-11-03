"""Explicitly approved customer-facing excerpts from the six NovaCart SOP PDFs.

These are curated summaries of customer-disclosable policy, NOT raw operational
SOP chunks. Review changes against the source PDF before shipping.
"""
import hashlib

# (source filename, original PDF page, customer-safe policy excerpt)
APPROVED_EXCERPTS = (
    (
        "02_Returns_and_Refunds_SOP.pdf", 1,
        "Fashion and apparel have a 14-day return window if unworn and unwashed, "
        "with brand tags intact and original packaging. Eligible remedies include "
        "refund to source, wallet refund, or size/color exchange, subject to verification."
    ),
    (
        "02_Returns_and_Refunds_SOP.pdf", 1,
        "Consumer electronics have a 7-day return window. The original box, "
        "serial numbers matching the invoice, and all accessories are required. "
        "A technical replacement or refund requires technician diagnosis and verification."
    ),
    (
        "02_Returns_and_Refunds_SOP.pdf", 1,
        "Large home appliances have a 10-day return window, subject to being "
        "uninstalled, retaining original packaging and manuals, and mandatory "
        "brand-technician inspection before pickup."
    ),
    (
        "02_Returns_and_Refunds_SOP.pdf", 1,
        "Personal care and beauty products are normally non-returnable once "
        "their hygiene seal is opened. Broken, expired, or leaking items may "
        "qualify for a refund or replacement after verification."
    ),
    (
        "02_Returns_and_Refunds_SOP.pdf", 1,
        "Customers can initiate a return through the orders dashboard. "
        "Photographs are required for damaged or defective products. "
        "Submission is not approval; eligibility and order ownership must be verified."
    ),
    (
        "03_Product_and_Warranty_SOP.pdf", 1,
        "NovaCart's 30-day platform assurance covers manufacturing defects, "
        "latent hardware malfunction and authenticity discrepancies within "
        "30 days of confirmed delivery. Manufacturer warranties commonly run "
        "12 to 24 months, subject to the manufacturer terms."
    ),
    (
        "03_Product_and_Warranty_SOP.pdf", 1,
        "Optional NovaCare extended protection may cover accidental liquid damage, "
        "drop damage and power surges for up to 36 months, subject to purchased coverage."
    ),
    (
        "03_Product_and_Warranty_SOP.pdf", 2,
        "An electronic device or appliance that fails to power on or has severe "
        "functional failure within seven calendar days of delivery may qualify "
        "for Dead-on-Arrival assessment. Certification and replacement require "
        "verification; no replacement dispatch is confirmed by this policy."
    ),
    (
        "03_Product_and_Warranty_SOP.pdf", 4,
        "For electrical sparks, battery swelling, fire, explosion, chemical "
        "leakage or injury, stop using the product, disconnect power only if "
        "safe to do so, and seek urgent specialist assistance. Do not attempt "
        "to power on or troubleshoot a hazardous device."
    ),
    (
        "04_Account_and_Login_SOP.pdf", 1,
        "Account changes and recovery require identity verification. "
        "Never share a password, full payment-card number, or CVV with a support agent."
    ),
    (
        "05_Payments_and_Billing_SOP.pdf", 2,
        "A pending card authorization is not necessarily a completed charge. "
        "If you see a possible duplicate payment, report both transaction "
        "statuses for verification; no bank hold release is guaranteed."
    ),
    (
        "05_Payments_and_Billing_SOP.pdf", 3,
        "Buy-now-pay-later late fees charged by third-party lenders cannot "
        "be waived directly by NovaCart. Payment disputes require specialist review."
    ),
)


def approved_passages():
    """Produce immutable-identity, provenance-bearing safe excerpts."""
    result = []
    for source, page, passage in APPROVED_EXCERPTS:
        key = f"approved-v1|{source}|{page}|{passage}"
        result.append({
            "id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
            "source": source,
            "page": page,
            "chunk": 0,
            "text": passage,
            "version": "approved-v1",
            "visibility": "customer",
            "approval": "curated-v1",
        })
    return result


_APPROVED = {p["id"]: p for p in approved_passages()}


def is_approved_customer_passage(doc):
    """Do not trust caller-provided visibility, ID or source metadata alone."""
    if not isinstance(doc, dict):
        return False
    expected = _APPROVED.get(doc.get("id"))
    return expected is not None and all(
        doc.get(field) == expected[field]
        for field in ("source", "page", "chunk", "text", "version", "visibility", "approval")
    )
