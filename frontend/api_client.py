import os
import requests

# Configured via FIXFLOW_API_URL; an empty value falls back to the local default.
BASE = (os.getenv("FIXFLOW_API_URL") or "http://127.0.0.1:8000").rstrip("/")

# User-facing fallbacks. Server text for 5xx responses is never shown because it
# may contain internal details; 4xx details are backend messages meant for users.
DEFAULT_MESSAGES = {
    400: "The request could not be processed. Please check the details and try again.",
    401: "Your session has expired. Please sign in again.",
    403: "You do not have permission to perform this action.",
    404: "The requested item could not be found.",
    409: "This item was changed by another action.",
    413: "The uploaded file is too large.",
    422: "Please check the information you entered.",
    429: "Too many requests. Please wait a moment and try again.",
}
SERVER_ERROR = "The support service ran into a problem. Please try again shortly."
BUSY = "The support service is busy. Please try again in a moment."
UNREACHABLE = "The support service is unreachable. Please try again shortly."
TIMEOUT = "The support service took too long to respond. Please try again."


class ApiError(RuntimeError):
    """An API failure with a user-safe message. status is None for network errors."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _detail(response):
    """Extract a short, user-facing FastAPI error detail, if one exists."""
    try:
        body = response.json()
    except ValueError:
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str):
        return detail.strip()[:300] or None
    if isinstance(detail, list) and detail and isinstance(detail[0], dict):
        # Pydantic validation error: show the first message only.
        message = str(detail[0].get("msg") or "").removeprefix("Value error, ").strip()
        return message[:300] or None
    return None


def call(method, path, token=None, **kw):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = requests.request(method, BASE + path, headers=headers, timeout=30, **kw)
    except requests.Timeout:
        raise ApiError(None, TIMEOUT) from None
    except requests.RequestException:
        raise ApiError(None, UNREACHABLE) from None
    if not r.ok:
        status = r.status_code
        if status == 503:  # deliberate, user-safe "busy, retry" message from the backend
            raise ApiError(status, _detail(r) or BUSY)
        if status >= 500:
            raise ApiError(status, SERVER_ERROR)
        raise ApiError(status, _detail(r) or DEFAULT_MESSAGES.get(status, DEFAULT_MESSAGES[400]))
    return r.json()
