"""Regression coverage for conservative repair of return-window sentences."""
from backend.services import answer_repair


def test_never_removes_return_window_even_if_checker_misclassifies(monkeypatch):
    draft = (
        "Fashion and apparel have a 14-day return window if unworn and unwashed. "
        "You can initiate a return through your orders dashboard."
    )
    def checker(text, passages):
        return "UNSUPPORTED_DELIVERY_TIME" if "14-day" in text else None
    monkeypatch.setattr(answer_repair, "check_response_grounding", checker)
    monkeypatch.setattr(answer_repair, "check_customer_visibility", lambda text: None)
    assert answer_repair.repair_isolated_time_claim(draft, []) == (draft, None)


def test_still_repairs_isolated_unsupported_delivery_claim(monkeypatch):
    draft = "Delivery is guaranteed within 2 days. Please check your order details."
    def checker(text, passages):
        return "UNSUPPORTED_DELIVERY_TIME" if "2 days" in text else None
    monkeypatch.setattr(answer_repair, "check_response_grounding", checker)
    monkeypatch.setattr(answer_repair, "check_customer_visibility", lambda text: None)
    assert answer_repair.repair_isolated_time_claim(draft, []) == (
        "Please check your order details.", "UNSUPPORTED_DELIVERY_TIME"
    )
