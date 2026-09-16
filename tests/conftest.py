"""Shared fixtures for the Recall MCP test suite."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp_recall.py"


class MCPClient:
    """Minimal JSON-RPC stdio client for testing the MCP server."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        env = os.environ.copy()
        env["RECALL_MCP_DB"] = db_path
        env["RECALL_LOG_LEVEL"] = "ERROR"  # quiet tests
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )

    def send(self, obj: dict) -> dict:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(__import__("json").dumps(obj) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read() if self.proc.stderr else ""
            raise AssertionError(f"no response from server. stderr: {err}")
        return __import__("json").loads(line)

    def tool(self, name: str, args: dict | None = None) -> dict:
        """Call a tool and return the parsed JSON payload from its text content."""
        r = self.send({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        })
        text = r["result"]["content"][0]["text"]
        return __import__("json").loads(text)

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture
def mcp():
    """Spin up a fresh MCP server with a throwaway DB, tear down after the test."""
    tmp = tempfile.mkdtemp(prefix="recall_test_")
    db = os.path.join(tmp, "memory.db")
    client = MCPClient(db)
    # Initialize handshake
    init = client.send({"jsonrpc": "2.0", "id": 0, "method": "initialize"})
    assert init["result"]["serverInfo"]["name"] == "recall-mcp"
    yield client
    client.close()


@pytest.fixture
def tmp_db():
    """A path to a non-existent DB in a temp dir."""
    tmp = tempfile.mkdtemp(prefix="recall_test_")
    return os.path.join(tmp, "memory.db")
