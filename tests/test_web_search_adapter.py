"""Offline Phase 9C tests; no search subscription or network needed."""
import json
import pytest

@pytest.fixture(autouse=True)
def force_brave_provider(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "brave")
from unittest.mock import patch

from backend.services import web_search_adapter as search


def test_private_queries_blocked_before_provider(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")
    blocked = [
        "NovaCart order ID DEMO-1001", "customer email abc@example.com",
        "tracking number 12345", "internal policy refund", "password reset for customer",
        "call +91 9876543210", "check https://localhost/private",
        "my Aadhaar number", "refund status for order", "api key leak",
    ]
    with patch.object(search, "build_opener") as opener:
        for query in blocked:
            result = search.search_public_web(query)
            assert result == {"ok": False, "error": "PRIVATE_QUERY_BLOCKED"}, query
        opener.assert_not_called()


def test_invalid_queries():
    for query in ["", " ", "x" * 181, "hello\nworld", None]:
        assert search.search_public_web(query)["error"] == "INVALID_QUERY"


def test_missing_key(monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    assert search.search_public_web("Python 3.11 release notes") == {
        "ok": False, "error": "SEARCH_NOT_CONFIGURED"
    }


def test_public_urls_only():
    assert search._public_url("https://docs.python.org/3/")
    for url in ["http://example.com", "https://localhost", "https://127.0.0.1/x",
                "https://10.0.0.1/x", "https://example.com:8443/x",
                "https://user:pass@example.com/x", "file:///etc/passwd"]:
        assert search._public_url(url) is None


def test_normalize_discards_bad_links():
    data = {"web": {"results": [
        {"title": "Bad", "url": "http://example.com"},
        {"title": "Good", "url": "https://docs.python.org/3/", "description": "Official docs"},
    ]}}
    assert search._normalize(data) == [{
        "title": "Good", "url": "https://docs.python.org/3/", "snippet": "Official docs"
    }]


class FakeResponse:
    status = 200
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, n):
        return self.payload


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload
    def open(self, request, timeout):
        assert request.full_url.startswith(search.API_ENDPOINT + "?")
        assert timeout == search.TIMEOUT_SECONDS
        assert request.get_header("X-subscription-token") == "test-key"
        return FakeResponse(self.payload)


def test_success_with_mocked_provider(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")
    payload = json.dumps({"web": {"results": [
        {"title": "Python docs", "url": "https://docs.python.org/3/", "description": "Docs"}
    ]}}).encode()
    with patch.object(search, "build_opener", return_value=FakeOpener(payload)):
        result = search.search_public_web("Python 3.11 release notes")
    assert result["ok"] is True
    assert result["source_type"] == "EXTERNAL_UNTRUSTED"
    assert result["results"][0]["url"] == "https://docs.python.org/3/"


def test_bad_json_fails_closed(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")
    with patch.object(search, "build_opener", return_value=FakeOpener(b"not json")):
        result = search.search_public_web("Python 3.11 release notes")
    assert result == {"ok": False, "error": "SEARCH_INVALID_RESPONSE"}


def test_network_failure_fails_closed(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")
    with patch.object(search, "build_opener", side_effect=OSError("network down")):
        result = search.search_public_web("Python 3.11 release notes")
    assert result == {"ok": False, "error": "SEARCH_UNAVAILABLE"}
