
import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from autogen_ext.tools.mcp import (
    McpWorkbench,
    StdioServerParams,
)


def extract_result(response):
    if isinstance(response, dict):
        if response.get("isError") or response.get("is_error"):
            raise AssertionError(f"MCP error: {response}")

        if "structuredContent" in response:
            return extract_result(response["structuredContent"])

        if "result" in response:
            return extract_result(response["result"])

        if "content" in response:
            return extract_result(response["content"])

        return response

    if isinstance(response, list):
        assert response, "Empty MCP response"
        return extract_result(response[0])

    if getattr(response, "is_error", False):
        raise AssertionError(f"MCP error: {response}")

    if hasattr(response, "result"):
        return extract_result(response.result)

    if hasattr(response, "content"):
        return extract_result(response.content)

    if hasattr(response, "text"):
        return extract_result(response.text)

    if isinstance(response, str):
        return json.loads(response)

    raise AssertionError(f"Unexpected MCP response: {response!r}")


async def verify_mcp():
    params = StdioServerParams(
        command=sys.executable,
        args=["-m", "backend.mcp_server.server"],
    )

    async with McpWorkbench(params) as workbench:
        tools = await workbench.list_tools()

        names = {
            tool["name"] if isinstance(tool, dict) else tool.name
            for tool in tools
        }

        assert "get_product_information" in names
        assert "check_refund_review_threshold" in names

        for amount, expected in (
            (499, False),
            (500, True),
            (501, True),
        ):
            response = await workbench.call_tool(
                "check_refund_review_threshold",
                {"amount_inr": amount},
            )

            result = extract_result(response)

            assert result["requires_human_review"] is expected
            assert result["decision"] == "NOT_MADE"
            assert result["execution_status"] == "NOT_CONFIGURED"


def test_mcp_tool_discovery_and_invocation():
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: asyncio.run(verify_mcp()))
        future.result(timeout=30)
