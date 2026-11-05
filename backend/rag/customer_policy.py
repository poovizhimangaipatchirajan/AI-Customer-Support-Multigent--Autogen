"""Explicitly approved customer-facing excerpts from the NovaCart SOP PDFs,
plus app help text describing this application's own screens.

These are curated summaries of customer-disclosable policy, NOT raw operational
SOP chunks. Review changes against the source PDF before shipping.
"""
import hashlib

# (source filename, original PDF page, customer-safe policy excerpt)
APPROVED_EXCERPTS = (
    (
        "01_Shipping_and_Delivery_SOP.pdf", 3,
        "If an order is marked delivered but you have not received it, first check "
        "your mailroom, front desk, building concierge, and other authorized "
        "household recipients. If it is still missing, contact NovaCart support "
        "with your order details for identity and delivery verification and "
        "investigation. A refund or replacement is not automatically guaranteed."
    ),
    (
        # Customer-safe wording of the SOP p1 compliance rule (in transit within
        # the promised EDD is not lost; no compensation) and the p2 stalled-
        # consignment investigation. Approved by the project owner, 2026-11-05.
        "01_Shipping_and_Delivery_SOP.pdf", 1,
        "If your order has not arrived but is still in transit and within its "
        "estimated delivery date, it is not treated as lost and no compensation "
        "applies at that stage. If it is past the estimated delivery date, contact "
        "NovaCart support with your order details for delivery verification and "
        "investigation."
    ),
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
        # Describes this application's own reset screen (frontend/app.py,
        # /auth/password-reset/*), not an SOP page. Keep in step with the UI.
        # Approved by the project owner, 2026-11-05.
        "NovaCart_App_Help", 1,
        "To reset a forgotten password, choose Forgot password? on the sign-in "
        "(login) page, or open the Verify / Reset tab, and enter your registered "
        "email address. A password reset code is sent to that email and expires "
        "in 30 minutes. Enter the code with a new password of at least 12 "
        "characters. Never share the reset code with anyone, including support agents."
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


def approved_mfa_guidance(query):
    """Return None for unrelated queries, or approved evidence for MFA queries.

    Authorization hierarchy is an internal procedure, not customer-approved
    evidence. Never substitute unrelated search results for missing guidance.
    """
    import re
    if not isinstance(query, str):
        return None
    q = query.lower()
    mfa = bool(re.search(r"\bmfa\b|multi.factor|two.factor|2fa|authenticator|authentication app", q))
    # Routine forgotten-password/login-recovery questions have an approved
    # customer-facing identity-verification excerpt. Provide that excerpt in
    # either retrieval mode rather than depending on an unrelated dense match.
    # This does not authorize a reset or expose internal recovery procedures.
    password_recovery = bool(re.search(
        r"\b(?:forgot|forgotten|reset|recover|lost|can.t|cannot|unable|trouble)\b"
        r".{0,90}\b(?:password|log[ -]?in|sign[ -]?in|account access)\b"
        r"|\b(?:password|log[ -]?in|sign[ -]?in)\b.{0,90}"
        r"\b(?:forgot|forgotten|reset|recover|lost|can.t|cannot|unable|trouble)\b",
        q,
    ))
    if not mfa and not password_recovery:
        return None
    # Internal authorization procedures have no customer-approved passage.
    if re.search(r"hierarch|authorization level|approval chain|override procedure|internal procedure", q):
        return []
    # Identity-verification guidance, plus the app's own reset steps for a
    # forgotten password (never for MFA, which has no approved reset path).
    return [p for p in approved_passages()
            if (p["source"] == "04_Account_and_Login_SOP.pdf"
                and "identity verification" in p["text"].lower())
            or (password_recovery and not mfa and p["source"] == "NovaCart_App_Help")]


def approved_topic_guidance(query):
    """Return None for ordinary queries, or a strictly curated topic result.

    [] is intentional: no supported delivery SLA exists in the approved
    customer excerpts. Never substitute return or warranty windows.
    """
    import re
    if not isinstance(query, str):
        return None
    q = query.lower()
    # Only the delivered-but-not-received passage answers that question; the
    # in-transit passage covers orders that have not been marked delivered.
    shipping = [p for p in approved_passages()
                if p["source"] == "01_Shipping_and_Delivery_SOP.pdf"
                and "marked delivered" in p["text"]]
    warranty = [p for p in approved_passages()
                if p["source"] == "03_Product_and_Warranty_SOP.pdf"
                and "30-day platform assurance" in p["text"]]
    if re.search(r"\b(deliver(?:y|ed)?|shipping|shipment|package|parcel)\b", q):
        if (re.search(r"marked as delivered|marked delivered|shows delivered|says delivered|delivered but|delivered.*not (?:been )?received|false delivery", q)
                and re.search(r"not received|haven.t received|didn.t receive|missing|false delivery|not (?:been )?received", q)):
            return shipping
        if re.search(r"timeframe|time frame|how long|delivery time|delivery estimate|delivery window|standard delivery|shipping time|days to deliver|when (?:will|does).*arrive", q):
            return []
    if re.search(r"\b(warranty|platform assurance|manufacturer warranty)\b", q):
        if re.search(r"standard|cover|included|manufacturer|platform assurance", q) and not re.search(r"novacare|extended|optional|accidental", q):
            return warranty
    return None
