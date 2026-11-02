"""Offline, zero-credit Serper integration regression tests."""
import json
from unittest.mock import patch
from backend.services import web_search_adapter as search


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
        assert request.full_url == search.SERPER_ENDPOINT
        assert request.get_method() == "POST"
        assert request.get_header("X-api-key") == "test-serper-key"
        assert json.loads(request.data)["num"] == search.MAX_RESULTS
        assert timeout == search.TIMEOUT_SECONDS
        return FakeResponse(self.payload)


def test_serper_success(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "serper")
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    payload = json.dumps({"organic": [
        {"title": "Gov", "link": "https://www.india.gov.in/", "snippet": "Official"},
        {"title": "Bad", "link": "http://localhost/", "snippet": "Unsafe"},
    ]}).encode()
    with patch.object(search, "build_opener", return_value=FakeOpener(payload)):
        result = search.search_public_web("India government consumer rights guidance")
    assert result == {"ok": True, "source_type": "EXTERNAL_UNTRUSTED", "results": [
        {"title": "Gov", "url": "https://www.india.gov.in/", "snippet": "Official"}
    ]}


def test_serper_missing_key(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "serper")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    assert search.search_public_web("India government consumer rights guidance") == {
        "ok": False, "error": "SEARCH_NOT_CONFIGURED"}


def test_serper_private_query_blocked_before_network(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "serper")
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    with patch.object(search, "build_opener") as opener:
        assert search.search_public_web("NovaCart order ID DEMO-1001") == {
            "ok": False, "error": "PRIVATE_QUERY_BLOCKED"}
        opener.assert_not_called()


def test_serper_bad_json(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "serper")
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    with patch.object(search, "build_opener", return_value=FakeOpener(b"invalid json")):
        assert search.search_public_web("India government consumer rights guidance") == {
            "ok": False, "error": "SEARCH_INVALID_RESPONSE"}


def test_serper_invalid_provider_no_network(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "unsupported")
    with patch.object(search, "build_opener") as opener:
        assert search.search_public_web("India government consumer rights guidance") == {
            "ok": False, "error": "SEARCH_NOT_CONFIGURED"}
        opener.assert_not_called()


def test_serper_network_failure(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "serper")
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    with patch.object(search, "build_opener", side_effect=OSError("offline")):
        assert search.search_public_web("India government consumer rights guidance") == {
            "ok": False, "error": "SEARCH_UNAVAILABLE"}
