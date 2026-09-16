"""Tests for the MCP protocol layer (initialize, ping, tools/list, tools/call)."""
from __future__ import annotations

import pytest


def test_initialize_returns_server_info(mcp):
    r = mcp.send({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r["id"] == 1
    assert r["result"]["protocolVersion"] == "2024-11-05"
    assert r["result"]["serverInfo"]["name"] == "recall-mcp"
    assert "tools" in r["result"]["capabilities"]


def test_ping_pong(mcp):
    r = mcp.send({"jsonrpc": "2.0", "id": 99, "method": "ping"})
    assert r["id"] == 99
    assert r["result"] == {}


def test_initialized_notification_has_no_response(mcp):
    # Per MCP spec, notifications/initialized is a notification → server must NOT respond.
    # We send it and then send a ping; the next response should be the ping's, not the notification's.
    import json
    assert mcp.proc.stdin is not None
    mcp.proc.stdin.write(json.dumps({
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }) + "\n")
    mcp.proc.stdin.flush()
    # Now send a ping — we expect the ping response, not the notification response.
    r = mcp.send({"jsonrpc": "2.0", "id": 7, "method": "ping"})
    assert r["id"] == 7
    assert r["result"] == {}


def test_tools_list_returns_all_8_tools(mcp):
    r = mcp.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = r["result"]["tools"]
    names = {t["name"] for t in tools}
    expected = {
        "remember", "recall", "forget", "list_memories",
        "summarize_session", "get_stats", "export_memories", "import_memories",
    }
    assert names == expected, f"missing: {expected - names}"
    # Every tool must have an inputSchema
    for t in tools:
        assert "inputSchema" in t
        assert "description" in t
        assert isinstance(t["description"], str) and len(t["description"]) > 10


def test_unknown_method_returns_jsonrpc_error(mcp):
    r = mcp.send({"jsonrpc": "2.0", "id": 1, "method": "totally/made/up"})
    assert "error" in r
    assert r["error"]["code"] == -32601


def test_unknown_tool_returns_error(mcp):
    r = mcp.send({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "nonexistent_tool", "arguments": {}},
    })
    assert "error" in r
    assert "Unknown tool" in r["error"]["message"]


def test_invalid_json_returns_parse_error(mcp):
    import json
    assert mcp.proc.stdin is not None
    mcp.proc.stdin.write("{this is not valid json\n")
    mcp.proc.stdin.flush()
    line = mcp.proc.stdout.readline() if mcp.proc.stdout else ""
    assert line, "expected a parse error response"
    r = json.loads(line)
    assert r["error"]["code"] == -32700


def test_resources_list_returns_empty(mcp):
    r = mcp.send({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
    assert r["result"] == {"resources": []}


def test_prompts_list_returns_empty(mcp):
    r = mcp.send({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})
    assert r["result"] == {"prompts": []}
