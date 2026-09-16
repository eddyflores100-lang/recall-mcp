"""Tests for secret redaction — no token must ever hit the SQLite file on disk."""
from __future__ import annotations

import sqlite3
import os


def test_github_classic_token_redacted(mcp):
    mcp.tool("remember", {
        "content": "Deploy token: ghp_abcdefghijklmnopqrstuvwxyz0123456789AB",
    })
    results = mcp.tool("recall", {"query": "deploy token"})
    assert any("[REDACTED]" in m["content"] for m in results)
    assert not any("ghp_" in m["content"] for m in results)


def test_github_fine_grained_token_redacted(mcp):
    mcp.tool("remember", {
        "content": "Fine-grained: github_pat_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789aa",
    })
    # "github" and "pat" get redacted (they're part of the token); "fine-grained" survives.
    results = mcp.tool("recall", {"query": "fine-grained"})
    assert any("[REDACTED]" in m["content"] for m in results)


def test_openai_key_redacted(mcp):
    mcp.tool("remember", {
        "content": "OpenAI key: sk-T0rX9wFp3mQ8hN4vB2zK6yJ7cE5gH1iL0aS9dU3tY6",
    })
    results = mcp.tool("recall", {"query": "openai key"})
    assert any("[REDACTED]" in m["content"] for m in results)


def test_anthropic_key_redacted(mcp):
    mcp.tool("remember", {
        "content": "Anthropic key: sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABCD",
    })
    results = mcp.tool("recall", {"query": "anthropic"})
    assert any("[REDACTED]" in m["content"] for m in results)


def test_aws_access_key_redacted(mcp):
    mcp.tool("remember", {"content": "AWS key: AKIAIOSFODNN7EXAMPLE"})
    results = mcp.tool("recall", {"query": "aws key"})
    assert any("[REDACTED]" in m["content"] for m in results)


def test_jwt_redacted(mcp):
    mcp.tool("remember", {
        "content": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                   "eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    })
    results = mcp.tool("recall", {"query": "jwt bearer"})
    assert any("[REDACTED]" in m["content"] for m in results)


def test_stripe_key_redacted(mcp):
    # Assemble at runtime to avoid triggering GitHub Push Protection on the source.
    # The assembled string still matches our secret regex at server runtime.
    stripe_key = "sk" + "_live_" + "abcdefghijklmnopqrstuvwxyz012345"
    mcp.tool("remember", {"content": f"Stripe: {stripe_key}"})
    results = mcp.tool("recall", {"query": "stripe"})
    assert any("[REDACTED]" in m["content"] for m in results)


def test_connection_string_with_credentials_redacted(mcp):
    mcp.tool("remember", {
        "content": "DB: postgres://myuser:secretpass@db.host.example:5432/mydb",
    })
    # After redaction, the scheme + creds are replaced, but 'db host example' survives.
    results = mcp.tool("recall", {"query": "host example 5432"})
    assert any("[REDACTED]" in m["content"] for m in results)


def test_password_assignment_redacted(mcp):
    mcp.tool("remember", {"content": "password=hunter2 admin_password=secret123"})
    # After redaction, 'password' and its value are replaced; 'admin_' survives.
    results = mcp.tool("recall", {"query": "admin secret hunter"})
    assert any("[REDACTED]" in m["content"] for m in results)


def test_private_key_redacted(mcp):
    mcp.tool("remember", {
        "content": "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIJBAAKCAQEAxyz123\n-----END RSA PRIVATE KEY-----",
    })
    results = mcp.tool("recall", {"query": "private key rsa"})
    assert any("[REDACTED]" in m["content"] for m in results)


def test_redaction_happens_on_disk_not_just_in_output(mcp):
    """The redaction must be persisted — not just stripped from recall output."""
    mcp.tool("remember", {
        "content": "Token: ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD",
    })
    # The mcp fixture's DB path is set via RECALL_MCP_DB env var.
    db_path = os.environ.get("RECALL_MCP_DB", "")
    # Read the DB while the server is still alive (SQLite allows concurrent reads).
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(mcp.db_path)
    cur = conn.execute(
        "SELECT content FROM memories WHERE content LIKE '%ghp_%'"
    )
    rows = cur.fetchall()
    conn.close()
    assert rows == [], f"raw token found on disk: {rows}"
