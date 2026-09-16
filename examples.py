#!/usr/bin/env python3
"""Usage examples for Recall MCP — copy/paste any block into your agent or script.

This file does NOT require an MCP client — it imports the MemoryStore class
directly to show the API surface. In real use, your agent calls these tools
over the MCP protocol (see README.md for config).
"""
import os
import sys
import tempfile

# Allow importing from this folder when run directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_recall import MemoryStore  # noqa: E402


def main() -> None:
    # Use a throwaway DB for the demo
    tmp_db = os.path.join(tempfile.mkdtemp(prefix="recall_demo_"), "memory.db")
    store = MemoryStore(db_path=tmp_db)

    print("=" * 60)
    print("1. Storing memories")
    print("=" * 60)
    print(store.remember(
        content="User prefers tabs over spaces. Project uses Next.js 14 with App Router.",
        tags=["coding-style", "frontend"],
        project="webapp",
        importance=0.9,
    ))
    print(store.remember(
        content="Auth middleware has a known bug with session expiry; bypass for now.",
        tags=["bug", "auth"],
        project="webapp",
        importance=0.7,
    ))
    print(store.remember(
        content="PostgreSQL connection string: postgres://user:pass@localhost:5432/mydb",
        tags=["database", "config"],
        project="backend",
        importance=0.8,
    ))
    print(store.remember(
        content="User's preferred test runner is vitest, not jest.",
        tags=["testing", "preference"],
        project="webapp",
    ))

    print("\n" + "=" * 60)
    print("2. Semantic-ish recall (FTS5 + recency + importance boost)")
    print("=" * 60)
    for r in store.recall("code style preferences", limit=3):
        print(f"  [{r['score']:.2f}] project={r['project']} tags={r['tags']}")
        print(f"        {r['content'][:90]}...")

    print("\n" + "=" * 60)
    print("3. Project-scoped recall")
    print("=" * 60)
    for r in store.recall("auth bug", project="webapp", limit=3):
        print(f"  [{r['score']:.2f}] {r['content'][:90]}...")

    print("\n" + "=" * 60)
    print("4. Secret redaction (note [REDACTED] in stored content)")
    print("=" * 60)
    store.remember(
        content="GitHub token for deploy: ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD",
        project="backend",
    )
    for r in store.recall("GitHub token deploy", limit=2):
        print(f"  {r['content']}")
        # Note: the ghp_ token has been replaced with [REDACTED] on disk

    print("\n" + "=" * 60)
    print("5. Duplicate detection")
    print("=" * 60)
    r1 = store.remember(content="This is a unique fact.", project="demo")
    r2 = store.remember(content="This is a unique fact.", project="demo")
    print(f"  first:   {r1['status']}  id={r1['id'][:8]}...")
    print(f"  second:  {r2['status']}  (same content → duplicate)")

    print("\n" + "=" * 60)
    print("6. Summarize a session (auto-extract memories)")
    print("=" * 60)
    summary = store.summarize_session(
        messages=[
            {"role": "user", "content": "I always use pnpm for this project, never npm."},
            {"role": "assistant", "content": "Understood. I'll use pnpm throughout."},
            {"role": "user", "content": "We decided to deploy on Vercel with preview builds per PR."},
            {"role": "user", "content": "Don't commit directly to main, always use PRs."},
        ],
        project="webapp",
        max_memories=10,
    )
    print(f"  extracted: {summary['extracted']}")
    print(f"  stored:    {summary['stored']}")
    print(f"  duplicate: {summary['duplicates']}")
    for m in summary["memories"]:
        print(f"    - {m}")

    print("\n" + "=" * 60)
    print("7. Stats")
    print("=" * 60)
    import json
    stats = store.get_stats()
    print(json.dumps(stats, indent=2, default=str))

    print("\n" + "=" * 60)
    print("8. List memories (filter by project)")
    print("=" * 60)
    for m in store.list_memories(project="webapp", limit=10, order="recent"):
        print(f"  [{m['importance']:.2f}] {m['project']:8s} {m['content'][:80]}...")

    print("\n" + "=" * 60)
    print("9. Export → JSON → import into a fresh store (portability demo)")
    print("=" * 60)
    exported = store.export_memories()
    print(f"  exported {exported['count']} memories")

    store2 = MemoryStore(db_path=os.path.join(tempfile.mkdtemp(), "fresh.db"))
    result = store2.import_memories(exported, on_duplicate="skip")
    print(f"  imported: created={result['created']} skipped={result['skipped']}")

    print("\n" + "=" * 60)
    print("10. Forget a memory by id")
    print("=" * 60)
    target = store.list_memories(limit=1)[0]
    print(f"  deleting: {target['id']}")
    deleted = store.forget(id=target["id"])
    print(f"  deleted:  {deleted} memory")

    print("\nDemo complete. DBs are in temp dirs — they'll be cleaned up by the OS.")


if __name__ == "__main__":
    main()
