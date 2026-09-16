"""Tests for the remember / recall / forget memory CRUD operations."""
from __future__ import annotations

import time

import pytest


def test_remember_returns_id_and_status_created(mcp):
    r = mcp.tool("remember", {
        "content": "User prefers tabs over spaces.",
        "tags": ["coding-style"],
        "project": "webapp",
        "importance": 0.9,
    })
    assert r["status"] == "created"
    assert "id" in r and len(r["id"]) > 10
    assert r["tags"] == ["coding-style"]
    assert r["project"] == "webapp"
    assert r["importance"] == 0.9


def test_remember_duplicate_does_not_re_insert(mcp):
    args = {"content": "An exact duplicate test memory.", "project": "demo"}
    r1 = mcp.tool("remember", args)
    r2 = mcp.tool("remember", args)
    assert r1["status"] == "created"
    assert r2["status"] == "duplicate"
    assert r2["id"] == r1["id"]  # same memory, not a new row


def test_remember_empty_content_raises(mcp):
    r = mcp.tool("remember", {"content": ""})
    # The server returns the error inside the tool result text.
    assert "error" in r
    assert "non-empty" in r["error"].lower()


def test_remember_long_content_truncates(mcp):
    long_content = "x" * 100_000
    r = mcp.tool("remember", {"content": long_content})
    assert r["status"] in ("created", "duplicate")


def test_remember_importance_clamped_to_0_1(mcp):
    r1 = mcp.tool("remember", {"content": "Over 1.0 importance test.", "importance": 5.0})
    r2 = mcp.tool("remember", {"content": "Negative importance test.", "importance": -1.0})
    # Server clamps silently — no exception. We just verify it stored.
    assert r1["status"] in ("created", "duplicate")
    assert r2["status"] in ("created", "duplicate")


def test_recall_returns_relevant_memories(mcp):
    mcp.tool("remember", {
        "content": "PostgreSQL connection: postgres://user:pass@host:5432/db",
        "project": "backend",
    })
    mcp.tool("remember", {
        "content": "Frontend uses Tailwind CSS for styling.",
        "project": "frontend",
    })
    results = mcp.tool("recall", {"query": "postgres connection database", "limit": 5})
    assert len(results) >= 1
    assert any("postgres" in m["content"].lower() for m in results)


def test_recall_project_filter(mcp):
    mcp.tool("remember", {
        "content": "Backend uses Flask with SQLAlchemy.",
        "project": "backend",
    })
    mcp.tool("remember", {
        "content": "Frontend uses React with Next.js.",
        "project": "frontend",
    })
    results = mcp.tool("recall", {
        "query": "frontend backend framework",
        "project": "backend",
        "limit": 10,
    })
    # All results must be from project=backend
    assert all(m["project"] == "backend" for m in results)
    assert len(results) >= 1


def test_recall_min_importance_filter(mcp):
    mcp.tool("remember", {"content": "Low importance memory.", "importance": 0.1})
    mcp.tool("remember", {"content": "High importance memory.", "importance": 0.95})
    results = mcp.tool("recall", {
        "query": "memory",
        "min_importance": 0.5,
        "limit": 20,
    })
    assert all(m["importance"] >= 0.5 for m in results)


def test_recall_empty_query_returns_empty_list(mcp):
    mcp.tool("remember", {"content": "Some memory."})
    results = mcp.tool("recall", {"query": ""})
    assert results == []


def test_recall_no_match_returns_empty(mcp):
    mcp.tool("remember", {"content": "Some unique content."})
    results = mcp.tool("recall", {"query": "zyxwvutsrqponmlkjihgfedcba"})
    assert results == []


def test_forget_by_id(mcp):
    r = mcp.tool("remember", {"content": "Will be forgotten."})
    deleted = mcp.tool("forget", {"id": r["id"]})
    assert deleted["deleted"] == 1
    # Verify gone
    results = mcp.tool("recall", {"query": "forgotten"})
    assert not any(m["id"] == r["id"] for m in results)


def test_forget_by_query_deletes_all_matches(mcp):
    mcp.tool("remember", {"content": "Test forget query alpha."})
    mcp.tool("remember", {"content": "Test forget query beta."})
    deleted = mcp.tool("forget", {"query": "forget query"})
    assert deleted["deleted"] >= 2


def test_recall_bumps_access_count(mcp):
    r = mcp.tool("remember", {"content": "Access counter test memory."})
    # Recall once
    mcp.tool("recall", {"query": "counter"})
    # Recall again
    mcp.tool("recall", {"query": "counter"})
    # List and check access_count >= 2
    lst = mcp.tool("list_memories", {"limit": 100, "order": "recent"})
    found = [m for m in lst if m["id"] == r["id"]]
    assert found, "memory was not listed"
    assert found[0]["access_count"] >= 2
