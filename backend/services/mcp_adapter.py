
"""
FixFlow AI - NovaCart MCP Adapter

Provides controlled, read-only access to the local MCP server.

Security:
- Explicit tool allowlist
- Local stdio transport
- Timeout protection
- Safe failure responses
- No refund approval or payment execution
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from autogen_ext.tools.mcp import (
    McpWorkbench,
    StdioServerParams,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

ALLOWED_TOOLS = frozenset(
    {
        "get_product_information",
        "check_refund_review_threshold",
    }
)

MCP_TIMEOUT_SECONDS = 15


def _decode(value: Any) -> dict:
    """Normalize AutoGen ToolResult and MCP JSON responses."""

    if getattr(value, "is_error", False):
        raise RuntimeError("MCP tool reported an error")

    if isinstance(value, dict):
        if value.get("isError") or value.get("is_error"):
            raise RuntimeError("MCP tool reported an error")

        for key in (
            "structuredContent",
            "structured_content",
            "result",
            "content",
        ):
            if key in value:
                return _decode(value[key])

        return value

    if isinstance(value, list):
        if not value:
            raise ValueError("Empty MCP response")

        for item in value:
            try:
                return _decode(item)
            except (ValueError, TypeError):
                continue

        raise ValueError("No valid MCP response content")

    if isinstance(value, str):
        parsed = json.loads(value)

        if not isinstance(parsed, dict):
            raise ValueError("MCP response must contain a JSON object")

        return _decode(parsed)

    if hasattr(value, "result"):
        return _decode(value.result)

    if hasattr(value, "content"):
        return _decode(value.content)

    if hasattr(value, "text"):
        return _decode(value.text)

    raise TypeError(
        f"Unsupported MCP response type: {type(value).__name__}"
    )


async def call_novacart_tool(
    name: str,
    arguments: dict,
) -> dict:
    """
    Invoke an allowlisted MCP tool.

    Returns:
        {"ok": True, "data": {...}}

    On failure:
        {"ok": False, "error": "..."}
    """

    if name not in ALLOWED_TOOLS:
        return {
            "ok": False,
            "error": "TOOL_NOT_ALLOWED",
        }

    if not isinstance(arguments, dict):
        return {
            "ok": False,
            "error": "INVALID_ARGUMENTS",
        }

    params = StdioServerParams(
        command=sys.executable,
        args=[
            "-m",
            "backend.mcp_server.server",
        ],
        cwd=str(PROJECT_ROOT),
    )

    try:
        async with asyncio.timeout(MCP_TIMEOUT_SECONDS):
            async with McpWorkbench(params) as workbench:
                tools = await workbench.list_tools()

                available = {
                    tool["name"]
                    if isinstance(tool, dict)
                    else tool.name
                    for tool in tools
                }

                if name not in available:
                    return {
                        "ok": False,
                        "error": "TOOL_UNAVAILABLE",
                    }

                response = await workbench.call_tool(
                    name,
                    arguments,
                )

                decoded = _decode(response)

                return {
                    "ok": True,
                    "data": decoded,
                }

    except TimeoutError:
        return {
            "ok": False,
            "error": "MCP_TIMEOUT",
        }

    except Exception:
        # Fail closed without exposing internal exception details to callers.
        return {
            "ok": False,
            "error": "MCP_UNAVAILABLE",
        }
