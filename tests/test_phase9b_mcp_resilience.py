"""Phase 9B: MCP adapter failure isolation (no network, model, or real subprocess).

Place in tests/test_phase9b_mcp_resilience.py.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import backend.services.mcp_adapter as adapter


def run(coro):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=10)


class FakeWorkbench:
    def __init__(self, params):
        self.params = params

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def list_tools(self):
        return [{"name": "get_product_information"}]

    async def call_tool(self, name, arguments):
        return SimpleNamespace(result=[SimpleNamespace(text='{"found":true,"demo_data":true}')], is_error=False)


def test_mcp_success_with_mock_workbench(monkeypatch):
    monkeypatch.setattr(adapter, "McpWorkbench", FakeWorkbench)
    result = run(adapter.call_novacart_tool("get_product_information", {"product_id": "MOUSE-01"}))
    assert result == {"ok": True, "data": {"found": True, "demo_data": True}}


def test_mcp_server_start_failure_is_safe(monkeypatch):
    class CrashingWorkbench(FakeWorkbench):
        async def __aenter__(self):
            raise OSError("simulated subprocess startup failure")

    monkeypatch.setattr(adapter, "McpWorkbench", CrashingWorkbench)
    result = run(adapter.call_novacart_tool("get_product_information", {"product_id": "MOUSE-01"}))
    assert result["ok"] is False
    assert result["error"] == "MCP_UNAVAILABLE"
    assert "data" not in result
    assert "simulated" not in str(result)


def test_mcp_timeout_is_safe(monkeypatch):
    class HangingWorkbench(FakeWorkbench):
        async def list_tools(self):
            await asyncio.sleep(0.1)
            return await super().list_tools()

    monkeypatch.setattr(adapter, "McpWorkbench", HangingWorkbench)
    monkeypatch.setattr(adapter, "MCP_TIMEOUT_SECONDS", 0.01)
    result = run(adapter.call_novacart_tool("get_product_information", {"product_id": "MOUSE-01"}))
    assert result == {"ok": False, "error": "MCP_TIMEOUT"}


def test_malformed_mcp_response_is_not_treated_as_success(monkeypatch):
    class MalformedWorkbench(FakeWorkbench):
        async def call_tool(self, name, arguments):
            return SimpleNamespace(result=[SimpleNamespace(text="not valid JSON")], is_error=False)

    monkeypatch.setattr(adapter, "McpWorkbench", MalformedWorkbench)
    result = run(adapter.call_novacart_tool("get_product_information", {"product_id": "MOUSE-01"}))
    assert result["ok"] is False
    assert result["error"] == "MCP_UNAVAILABLE"
    assert "data" not in result


def test_mcp_tool_error_is_not_treated_as_success(monkeypatch):
    class ErrorWorkbench(FakeWorkbench):
        async def call_tool(self, name, arguments):
            return SimpleNamespace(result=[SimpleNamespace(text='{"found":true}')], is_error=True)

    monkeypatch.setattr(adapter, "McpWorkbench", ErrorWorkbench)
    result = run(adapter.call_novacart_tool("get_product_information", {"product_id": "MOUSE-01"}))
    assert result["ok"] is False
    assert "data" not in result


def test_unavailable_tool_is_rejected(monkeypatch):
    class MissingToolWorkbench(FakeWorkbench):
        async def list_tools(self):
            return []

    monkeypatch.setattr(adapter, "McpWorkbench", MissingToolWorkbench)
    result = run(adapter.call_novacart_tool("get_product_information", {"product_id": "MOUSE-01"}))
    assert result == {"ok": False, "error": "TOOL_UNAVAILABLE"}


def test_unapproved_tool_does_not_launch_mcp(monkeypatch):
    class ShouldNotLaunch:
        def __init__(self, *args, **kwargs):
            raise AssertionError("MCP should not be launched")

    monkeypatch.setattr(adapter, "McpWorkbench", ShouldNotLaunch)
    result = run(adapter.call_novacart_tool("approve_refund", {"amount_inr": 500}))
    assert result == {"ok": False, "error": "TOOL_NOT_ALLOWED"}
