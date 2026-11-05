"""Customer-safe display copy for existing ticket states; never authorizes actions."""
import re


def review_message(ticket):
    """Explain an ESCALATED ticket without inferring approval from attachments."""
    flags = set(filter(None, (ticket.get('risk') or '').split(',')))
    text = f"{ticket.get('subject') or ''} {ticket.get('description') or ''}".lower()
    evidence = ticket.get('evidence') or []

    if flags & {'FRAUD', 'LEGAL', 'PAYMENT_DISPUTE'}:
        return ('This request requires specialist human review. No refund, '
                'payment, or other account action has been executed.')
    if flags & {'REFUND_GE_500', 'REFUND_AMOUNT_UNVERIFIED', 'REFUND_CURRENCY_UNCERTAIN'}:
        return ('Your refund request requires human review. Any supporting files '
                'are awaiting verification. No refund or payment has been executed.')
    if re.search(r'\b(?:password|log[ -]?in|sign[ -]?in|account access|otp)\b', text):
        return ('Your account-access request is awaiting support review. '
                'Account recovery requires identity verification. Never share '
                'your password or payment-card details.')
    if re.search(r'\b(?:broken|damaged|defective|faulty|replacement|replace)\b', text):
        if evidence:
            return ('Your supporting file has been received and is awaiting review. '
                    'Order ownership and product eligibility still require verification. '
                    'No replacement or refund has been approved or dispatched.')
        return ('Your product request is awaiting review. Please provide your order '
                'details and photographs of the damaged or defective item. '
                'No replacement or refund has been approved or dispatched.')
    return ('This ticket requires human review before a response or action can be '
            'confirmed. No refund or payment has been executed.')
