"""Tests for summarize_session, export_memories, import_memories, get_stats."""
from __future__ import annotations

import json


def test_summarize_session_extracts_preferences(mcp):
    summary = mcp.tool("summarize_session", {
        "messages": [
            {"role": "user", "content": "I always use pnpm, never npm. Always TypeScript."},
            {"role": "assistant", "content": "Got it. I'll use pnpm and TypeScript."},
            {"role": "user", "content": "We decided to deploy on Vercel."},
        ],
        "project": "webapp",
    })
    assert summary["extracted"] >= 1
    assert summary["stored"] >= 1
    # The extracted memories should mention pnpm or typescript or vercel
    joined = " ".join(summary["memories"]).lower()
    assert any(kw in joined for kw in ["pnpm", "typescript", "vercel"])


def test_summarize_session_caps_at_max_memories(mcp):
    # Build a long conversation with many memorable sentences
    msgs = []
    for i in range(20):
        msgs.append({"role": "user", "content": f"I prefer to use option {i}. Always."})
    summary = mcp.tool("summarize_session", {
        "messages": msgs,
        "max_memories": 5,
    })
    assert summary["extracted"] <= 5
    assert len(summary["memories"]) <= 5


def test_summarize_session_accepts_json_string(mcp):
    msgs_json = json.dumps([
        {"role": "user", "content": "Always use the functional approach."},
    ])
    summary = mcp.tool("summarize_session", {"messages": msgs_json})
    assert summary["extracted"] >= 0  # at least doesn't crash


def test_get_stats_empty_db_returns_zeros(mcp):
    stats = mcp.tool("get_stats", {})
    assert stats["total"] == 0
    assert stats["by_project"] == {}
    assert stats["oldest"] is None
    assert stats["newest"] is None


def test_get_stats_populated(mcp):
    mcp.tool("remember", {"content": "First.", "project": "a"})
    mcp.tool("remember", {"content": "Second.", "project": "b"})
    mcp.tool("remember", {"content": "Third.", "project": "a"})
    stats = mcp.tool("get_stats", {})
    assert stats["total"] == 3
    assert stats["by_project"].get("a") == 2
    assert stats["by_project"].get("b") == 1
    assert stats["oldest"] is not None
    assert stats["newest"] is not None
    assert stats["db_size_bytes"] > 0


def test_export_then_import_round_trip(mcp, tmp_db):
    # Populate
    mcp.tool("remember", {"content": "Memory A.", "project": "p1"})
    mcp.tool("remember", {"content": "Memory B.", "project": "p2"})
    # Export
    exported = mcp.tool("export_memories", {})
    assert exported["count"] == 2
    assert len(exported["memories"]) == 2
    # Import with skip → all should be duplicates
    result = mcp.tool("import_memories", {"data": exported, "on_duplicate": "skip"})
    assert result["skipped"] == 2
    assert result["created"] == 0


def test_import_invalid_json_raises(mcp):
    r = mcp.tool("import_memories", {"data": "not valid json {"})
    assert "error" in r


def test_import_wrong_shape_raises(mcp):
    r = mcp.tool("import_memories", {"data": {"not_memories_key": []}})
    assert "error" in r


def test_list_memories_orders_by_recent(mcp):
    import time
    mcp.tool("remember", {"content": "Oldest memory."})
    time.sleep(0.05)
    mcp.tool("remember", {"content": "Middle memory."})
    time.sleep(0.05)
    mcp.tool("remember", {"content": "Newest memory."})
    lst = mcp.tool("list_memories", {"limit": 10, "order": "recent"})
    assert len(lst) == 3
    assert "Newest" in lst[0]["content"]
    assert "Oldest" in lst[2]["content"]


def test_list_memories_tag_filter(mcp):
    mcp.tool("remember", {"content": "Tagged alpha.", "tags": ["alpha"]})
    mcp.tool("remember", {"content": "Tagged beta.", "tags": ["beta"]})
    lst = mcp.tool("list_memories", {"tag": "alpha", "limit": 10})
    assert all("alpha" in m["tags"] for m in lst)
    assert any("alpha" in m["content"].lower() for m in lst)
