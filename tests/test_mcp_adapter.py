
"""
Tests for the FixFlow AI MCP adapter.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from backend.services.mcp_adapter import call_novacart_tool


def run_async(coroutine):
    """
    Execute an async operation in a separate thread
    to avoid conflicts with an existing event loop.
    """
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            lambda: asyncio.run(coroutine)
        )
        return future.result(timeout=30)


def test_mcp_allowed_tool():
    result = run_async(
        call_novacart_tool(
            "check_refund_review_threshold",
            {"amount_inr": 500},
        )
    )

    assert result["ok"] is True, result
    assert result["data"]["requires_human_review"] is True
    assert result["data"]["decision"] == "NOT_MADE"
    assert result["data"]["execution_status"] == "NOT_CONFIGURED"


def test_mcp_below_threshold():
    result = run_async(
        call_novacart_tool(
            "check_refund_review_threshold",
            {"amount_inr": 499},
        )
    )

    assert result["ok"] is True, result
    assert result["data"]["requires_human_review"] is False


def test_mcp_above_threshold():
    result = run_async(
        call_novacart_tool(
            "check_refund_review_threshold",
            {"amount_inr": 501},
        )
    )

    assert result["ok"] is True, result
    assert result["data"]["requires_human_review"] is True


def test_mcp_product_information():
    result = run_async(
        call_novacart_tool(
            "get_product_information",
            {"product_id": "MOUSE-01"},
        )
    )

    assert result["ok"] is True, result
    assert result["data"]["found"] is True
    assert result["data"]["demo_data"] is True


def test_mcp_disallowed_tool():
    result = run_async(
        call_novacart_tool(
            "approve_refund",
            {"amount_inr": 500},
        )
    )

    assert result == {
        "ok": False,
        "error": "TOOL_NOT_ALLOWED",
    }


def test_mcp_invalid_arguments():
    result = run_async(
        call_novacart_tool(
            "get_product_information",
            None,
        )
    )

    assert result == {
        "ok": False,
        "error": "INVALID_ARGUMENTS",
    }
