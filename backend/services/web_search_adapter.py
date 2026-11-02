"""Restricted public-web lookup for FixFlow AI.

Only use through the approved-topic router. Provider selection is explicit:
SEARCH_PROVIDER=brave (default) or SEARCH_PROVIDER=serper.
Provider results are external, untrusted data, never policy evidence.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

API_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
SERPER_ENDPOINT = "https://google.serper.dev/search"
MAX_QUERY_LENGTH = 180
MAX_RESULTS = 5
TIMEOUT_SECONDS = 8

_PRIVATE = re.compile(
    r"\b(?:order\s*(?:id|number|#)|ticket\s*(?:id|number|#)|"
    r"account\s*(?:id|number)|customer\s*(?:id|number)|"
    r"password|otp|api[_ -]?key|access[_ -]?token|"
    r"refund\s*(?:id|request|status)|tracking\s*(?:id|number)|"
    r"internal\s*(?:policy|document)|novacart)\b",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)(?!\d)")
_URL = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_SENSITIVE = re.compile(r"\b(?:ssn|aadhaar|pan\s*(?:number|card)|credit\s*card|cvv)\b", re.I)


def validate_query(query: str) -> str | None:
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_LENGTH:
        return "INVALID_QUERY"
    if any(ord(c) < 32 for c in query):
        return "INVALID_QUERY"
    if _PRIVATE.search(query) or _EMAIL.search(query) or _PHONE.search(query):
        return "PRIVATE_QUERY_BLOCKED"
    if _URL.search(query) or _SENSITIVE.search(query):
        return "PRIVATE_QUERY_BLOCKED"
    return None


def _public_url(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = urlsplit(raw)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return None
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
                return None
        if parsed.port not in (None, 443):
            return None
        return raw
    except ValueError:
        return None


def _normalize(payload: object) -> list[dict[str, str]]:
    """Original Brave normalization contract retained for existing tests."""
    if not isinstance(payload, dict):
        raise ValueError("Unexpected provider response")
    web = payload.get("web", {})
    if not isinstance(web, dict):
        raise ValueError("Unexpected provider response")
    entries = web.get("results", [])
    return _normalize_entries(entries, "url", "description")


def _normalize_serper(payload: object) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        raise ValueError("Unexpected provider response")
    return _normalize_entries(payload.get("organic", []), "link", "snippet")


def _normalize_entries(entries: object, url_field: str, snippet_field: str) -> list[dict[str, str]]:
    if not isinstance(entries, list):
        raise ValueError("Unexpected provider results")
    clean = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        url = _public_url(item.get(url_field))
        title = item.get("title")
        if not url or not isinstance(title, str) or not title.strip():
            continue
        snippet = item.get(snippet_field, "")
        if not isinstance(snippet, str):
            snippet = ""
        clean.append({"title": title[:200], "url": url, "snippet": snippet[:500]})
        if len(clean) == MAX_RESULTS:
            break
    return clean


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def search_public_web(query: str) -> dict:
    """Search public web. Never pass raw customer messages or identifiers."""
    rejection = validate_query(query)
    if rejection:
        return {"ok": False, "error": rejection}

    provider = os.environ.get("SEARCH_PROVIDER", "brave").strip().lower()
    if provider == "brave":
        key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
        if not key:
            return {"ok": False, "error": "SEARCH_NOT_CONFIGURED"}
        url = API_ENDPOINT + "?" + urlencode({"q": query.strip(), "count": MAX_RESULTS})
        request = Request(url, headers={"Accept": "application/json", "X-Subscription-Token": key}, method="GET")
        normalizer = _normalize
    elif provider == "serper":
        key = os.environ.get("SERPER_API_KEY", "").strip()
        if not key:
            return {"ok": False, "error": "SEARCH_NOT_CONFIGURED"}
        body = json.dumps({"q": query.strip(), "num": MAX_RESULTS}).encode("utf-8")
        request = Request(
            SERPER_ENDPOINT,
            data=body,
            headers={"Accept": "application/json", "Content-Type": "application/json", "X-API-KEY": key},
            method="POST",
        )
        normalizer = _normalize_serper
    else:
        return {"ok": False, "error": "SEARCH_NOT_CONFIGURED"}

    try:
        with build_opener(NoRedirect).open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                return {"ok": False, "error": "SEARCH_UNAVAILABLE"}
            response_body = response.read(1_000_001)
            if len(response_body) > 1_000_000:
                return {"ok": False, "error": "SEARCH_INVALID_RESPONSE"}
            data = json.loads(response_body)
        results = normalizer(data)
        if not results:
            return {"ok": False, "error": "NO_VERIFIABLE_RESULTS"}
        return {"ok": True, "results": results, "source_type": "EXTERNAL_UNTRUSTED"}
    except (HTTPError, URLError, TimeoutError, OSError):
        return {"ok": False, "error": "SEARCH_UNAVAILABLE"}
    except (ValueError, TypeError, json.JSONDecodeError):
        return {"ok": False, "error": "SEARCH_INVALID_RESPONSE"}
