#!/usr/bin/env python3
"""Smoke tests for Recall MCP — exercises all 8 tools over JSON-RPC stdio."""
import json
import os
import subprocess
import sys
import tempfile

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_recall.py")

def main() -> int:
    tmp_dir = tempfile.mkdtemp(prefix="recall_test_")
    tmp_db = os.path.join(tmp_dir, "memory.db")
    env = os.environ.copy()
    env["RECALL_MCP_DB"] = tmp_db
    env["RECALL_LOG_LEVEL"] = "ERROR"

    proc = subprocess.Popen(
        [sys.executable, SCRIPT],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, env=env,
    )

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            err = proc.stderr.read()
            raise AssertionError(f"no response. stderr: {err}")
        return json.loads(line)

    passed = 0
    failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  PASS  {name}")
        else:
            failed += 1
            print(f"  FAIL  {name}  {detail}")

    # 1. initialize
    r = send({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    check("initialize",
          r["result"]["serverInfo"]["name"] == "recall-mcp")

    # 2. tools/list
    r = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = {t["name"] for t in r["result"]["tools"]}
    expected = {"remember", "recall", "forget", "list_memories",
                "summarize_session", "get_stats", "export_memories", "import_memories"}
    check("tools/list has 8 tools", tools == expected, f"got {tools}")

    # 3. remember
    r = send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "remember", "arguments": {
                  "content": "User prefers tabs over spaces. Next.js 14.",
                  "tags": ["coding-style"], "project": "webapp", "importance": 0.9}}})
    data = json.loads(r["result"]["content"][0]["text"])
    check("remember creates", data["status"] == "created", str(data))
    mid = data["id"]

    # 4. recall (note: FTS5 indexes content only, so we query against stored words)
    r = send({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "recall", "arguments": {"query": "tabs spaces"}}})
    results = json.loads(r["result"]["content"][0]["text"])
    check("recall finds match",
          len(results) >= 1 and "tabs" in results[0]["content"].lower(),
          f"got {results}")

    # 5. duplicate detection
    r = send({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
              "params": {"name": "remember", "arguments": {
                  "content": "User prefers tabs over spaces. Next.js 14."}}})
    data = json.loads(r["result"]["content"][0]["text"])
    check("duplicate detection", data["status"] == "duplicate")

    # 6. secret redaction
    r = send({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
              "params": {"name": "remember", "arguments": {
                  "content": "GitHub token: ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD"}}})
    secret_id = json.loads(r["result"]["content"][0]["text"])["id"]
    r = send({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
              "params": {"name": "recall", "arguments": {"query": "GitHub token"}}})
    results = json.loads(r["result"]["content"][0]["text"])
    has_redacted = any("[REDACTED]" in m["content"] for m in results)
    no_leak = not any("ghp_" in m["content"] for m in results)
    check("secret redaction", has_redacted and no_leak)

    # 7. summarize_session
    r = send({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
              "params": {"name": "summarize_session", "arguments": {
                  "messages": [
                      {"role": "user", "content": "I prefer pnpm over npm. Always."},
                      {"role": "assistant", "content": "Noted, will use pnpm."},
                      {"role": "user", "content": "We decided on Vercel for deployment."},
                  ],
                  "project": "webapp"}}})
    data = json.loads(r["result"]["content"][0]["text"])
    check("summarize_session extracts",
          data["extracted"] >= 1 and data["stored"] >= 1, str(data))

    # 8. get_stats
    r = send({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
              "params": {"name": "get_stats", "arguments": {}}})
    stats = json.loads(r["result"]["content"][0]["text"])
    check("get_stats returns counts",
          stats["total"] >= 2 and "by_project" in stats)

    # 9. list_memories with filter
    r = send({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
              "params": {"name": "list_memories", "arguments": {
                  "project": "webapp", "limit": 10}}})
    lst = json.loads(r["result"]["content"][0]["text"])
    check("list_memories filters by project",
          all(m["project"] == "webapp" for m in lst) and len(lst) >= 2)

    # 10. forget by id
    r = send({"jsonrpc": "2.0", "id": 11, "method": "tools/call",
              "params": {"name": "forget", "arguments": {"id": secret_id}}})
    data = json.loads(r["result"]["content"][0]["text"])
    check("forget by id", data["deleted"] == 1)

    # 11. export_memories
    r = send({"jsonrpc": "2.0", "id": 12, "method": "tools/call",
              "params": {"name": "export_memories", "arguments": {}}})
    exp = json.loads(r["result"]["content"][0]["text"])
    check("export_memories returns dict", exp["count"] >= 2 and "memories" in exp)

    # 12. import_memories round-trip
    r = send({"jsonrpc": "2.0", "id": 13, "method": "tools/call",
              "params": {"name": "import_memories", "arguments": {
                  "data": exp, "on_duplicate": "skip"}}})
    imp = json.loads(r["result"]["content"][0]["text"])
    check("import_memories skip duplicates", imp["skipped"] == exp["count"], str(imp))

    # 13. invalid tool returns error
    r = send({"jsonrpc": "2.0", "id": 14, "method": "tools/call",
              "params": {"name": "nonexistent", "arguments": {}}})
    check("invalid tool error", "error" in r)

    # 14. empty recall returns []
    r = send({"jsonrpc": "2.0", "id": 15, "method": "tools/call",
              "params": {"name": "recall", "arguments": {"query": "zyxwvutsrqponm"}}})
    results = json.loads(r["result"]["content"][0]["text"])
    check("empty recall returns []", results == [])

    proc.stdin.close()
    proc.wait(timeout=5)

    print(f"\n=== {passed} passed, {failed} failed ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
