"""Phase 9C: fixed-template public research, never raw customer text.

Only a trusted code-defined topic may reach the external provider. This is not
an order, refund, account, or NovaCart policy lookup mechanism.
"""
from __future__ import annotations

import asyncio
from typing import Final

from backend.services.web_search_adapter import search_public_web

PUBLIC_TOPICS: Final[dict[str, str]] = {
    "consumer_rights_india": "India government consumer protection consumer rights official guidance",
    "cyber_safety_india": "India government cybercrime reporting cyber safety official guidance",
    "online_shopping_safety": "India government safe online shopping consumer awareness guidance",
}


def allowed_public_topics() -> tuple[str, ...]:
    return tuple(PUBLIC_TOPICS)


async def search_approved_topic(topic: str) -> dict:
    """Only fixed, reviewed queries; no arbitrary query or user text accepted."""
    if not isinstance(topic, str) or topic not in PUBLIC_TOPICS:
        return {"ok": False, "error": "TOPIC_NOT_ALLOWED"}
    try:
        # urllib is synchronous; run it off the worker event loop.
        result = await asyncio.wait_for(
            asyncio.to_thread(search_public_web, PUBLIC_TOPICS[topic]), timeout=12
        )
    except Exception:
        return {"ok": False, "error": "SEARCH_UNAVAILABLE"}
    if not isinstance(result, dict) or result.get("ok") is not True:
        return {"ok": False, "error": result.get("error", "SEARCH_UNAVAILABLE") if isinstance(result, dict) else "SEARCH_UNAVAILABLE"}
    return {
        "ok": True,
        "topic": topic,
        "source_type": "EXTERNAL_UNTRUSTED",
        "results": result.get("results", []),
    }
