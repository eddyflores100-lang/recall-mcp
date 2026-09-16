# 🧠 Recall MCP — Long-term Memory for AI Agents

> **Your agent finally remembers what it did yesterday.** Cross-session, persistent, semantic memory for any MCP-compatible agent (Claude Desktop, Cursor, Cline, Continue, …).

[![MCP Compatible](https://img.shields.io/badge/MCP-2024--11--05-blue)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue)](https://www.python.org)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0-success)](#zero-dependencies)
[![Stars](https://img.shields.io/github/stars/eddyflores100-lang/recall-mcp)](https://github.com/eddyflores100-lang/recall-mcp/stargazers)

---

## 🎯 The Problem (worth 100,000+ GitHub stars)

Every time you start a new chat with Claude, Cursor, or any AI agent, **it starts from zero**. It doesn't remember:

- The bug you fixed yesterday
- The architecture decisions from last week
- Your codebase conventions (tabs vs spaces, framework version, test runner)
- Your preferences ("always use TypeScript", "never commit directly to main")
- The project's gotchas ("the auth middleware is broken, bypass it for now")

This means:

- 🔁 **You repeat yourself** — explain the same context every session
- 🐌 **Slower iteration** — agent re-discovers what it already knew
- 📉 **Worse quality** — no accumulated knowledge
- 💸 **Wasted tokens** — re-reading the same files, re-asking the same questions

## ✨ The Solution

Recall MCP gives your agent **persistent, searchable memory across sessions**.

```python
# Yesterday, in chat session A:
agent.call("remember", content="User prefers tabs over spaces. Project uses Next.js 14 with App Router.")
agent.call("remember", content="Auth middleware has a known bug with session expiry; bypass for now.")

# Today, in chat session B (fresh context):
agent.call("recall", query="code style preferences")
# → [{"content": "User prefers tabs over spaces. Project uses Next.js 14 with App Router.",
#     "score": 0.95, "tags": ["coding-style"], "project": "webapp", ...}]

agent.call("recall", query="known bugs auth")
# → [{"content": "Auth middleware has a known bug with session expiry; bypass for now.",
#     "score": 0.87, ...}]
```

### Why it's better than context-pruning approaches

| Approach | When it works | When it fails |
|----------|----------------|----------------|
| **Context pruning** (e.g. the other skill I shipped) | Compresses tool output *within a session* | Useless across sessions — the agent still forgets everything when you start a new chat |
| **Recall MCP** (this project) | Works *across* sessions — yesterday's context is searchable today | Doesn't help if your conversation is one-shot |

They're complementary: use a pruner to fit more useful context in the current session, and use Recall so the next session doesn't have to start from scratch.

## 🚀 Quick Start

### 1. Install

**Option A — pip (coming soon):**
```bash
pip install mcp-recall
```

**Option B — single-file (recommended for now):**
```bash
curl -sSL https://github.com/eddyflores100-lang/recall-mcp/raw/main/mcp_recall.py \
  -o ~/.local/bin/recall-mcp
chmod +x ~/.local/bin/recall-mcp
```

No `pip install` step, no virtualenv, no API keys — just Python 3.10+ with stdlib.

### 2. Configure with your agent

**Claude Desktop** — edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "recall": {
      "command": "python3",
      "args": ["/path/to/mcp_recall.py"]
    }
  }
}
```

**Cursor** — `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "recall": {
      "command": "python3",
      "args": ["/path/to/mcp_recall.py"]
    }
  }
}
```

**Cline / Continue / any MCP client** — same pattern: `command=python3`, `args=["/path/to/mcp_recall.py"]`.

### 3. Start using

Restart your agent and you'll see 8 new tools available. Try:

> *"Remember that I prefer functional React components over class components."*

Then start a brand new chat:

> *"Recall what I told you about React conventions."*

Your agent will pull the memory and continue where you left off.

## 🛠️ MCP Tools (8 total)

| Tool | What it does |
|------|--------------|
| `remember` | Store a memory with optional tags / project / importance (0..1). Auto-deduplicates by content hash. |
| `recall` | Semantic-ish search via FTS5 BM25 + recency/importance/access-count boosting. |
| `forget` | Delete a memory by `id` or by FTS content match (deletes all matches). |
| `list_memories` | List with filters: `project`, `tag`, `limit`, `order` (recent / oldest / accessed / important). |
| `summarize_session` | Pass a list of chat messages → automatically extracts memorable facts/preferences/decisions and stores them. |
| `get_stats` | Total count, per-project, per-source, per-tag breakdown, oldest, newest, most-accessed, avg importance, DB size. |
| `export_memories` | Dump memories as JSON (optionally per-project) — perfect for backups or transferring between machines. |
| `import_memories` | Load JSON back in. `on_duplicate` policy: `skip` / `bump` (increment access) / `replace` (overwrite metadata). |

### Tool call examples

```jsonc
// remember
{
  "content": "PostgreSQL connection string format: postgres://user:pass@host:5432/db",
  "tags": ["database", "postgres"],
  "project": "backend",
  "importance": 0.8
}

// recall
{
  "query": "how to connect to postgres",
  "limit": 5,
  "project": "backend",
  "min_importance": 0.3
}

// summarize_session (great for end-of-session snapshots)
{
  "messages": [
    {"role": "user", "content": "I always use pnpm, never npm."},
    {"role": "assistant", "content": "Noted. I'll use pnpm throughout."},
    {"role": "user", "content": "We decided to deploy on Vercel."}
  ],
  "project": "webapp"
}
```

## 🏗️ Architecture

```
┌──────────────┐      stdio (JSON-RPC)      ┌──────────────────┐
│  MCP client  │ ◄────────────────────────► │  mcp_recall.py    │
│ (Claude /    │                              │                  │
│  Cursor /    │                              │  ┌────────────┐  │
│  Cline …)    │                              │  │ MemoryStore│  │
└──────────────┘                              │  │   - remember│  │
                                              │  │   - recall  │  │
                                              │  │   - forget  │  │
                                              │  │   - stats   │  │
                                              │  │   ...       │  │
                                              │  └──────┬─────┘  │
                                              │         │        │
                                              │         ▼        │
                                              │  ┌────────────┐  │
                                              │  │  SQLite    │  │
                                              │  │  + FTS5    │  │
                                              │  │ (local file)│  │
                                              │  └────────────┘  │
                                              └──────────────────┘
                                              Default path:
                                              ~/.recall/memory.db
```

**Ranking formula** (for `recall` results):

```
score = bm25_rank × 1.0
      + recency_decay × 0.5         (full weight 90d, then linear fade to 0.25)
      + importance × 0.5           (user-set 0..1)
      + min(access_count × 0.05, 0.5)
```

This means: a memory that matches the query, was recently stored, marked as important, and accessed often will rank highest. A 6-month-old low-importance memory won't completely vanish, but it won't crowd out fresher results either.

## 🔒 Privacy & Security

- **100% local** — every byte lives in `~/.recall/memory.db` on your machine.
- **Zero telemetry** — no external API calls, no analytics, no phone-home.
- **Auto-redacts secrets** before storage. Detected patterns include:
  - GitHub tokens (`ghp_…`, `gho_…`, `ghs_…`, fine-grained)
  - OpenAI keys (`sk-…`), Anthropic keys (`sk-ant-…`), Gemini (`AIza…`)
  - AWS access keys (`AKIA…`)
  - JWTs (`eyJ…`)
  - Stripe keys, Slack tokens, Bearer tokens, private keys
  - `password=…` assignments
  - Connection strings with embedded credentials (`postgres://user:pass@…`)
  - All replaced with `[REDACTED]` before being written to disk.
- **Per-project isolation** — memories from project A don't bleed into project B's `recall` unless you ask for them.
- **Export anytime** — `export_memories` gives you the full database as JSON. Your data is yours.

## 📊 Comparison

| Feature | Recall MCP | mem0 | LangChain Memory | Zep |
|---------|-----------|------|------------------|-----|
| Local-first (no cloud) | ✅ | ❌ | ✅ | ❌ |
| Zero external dependencies | ✅ | ❌ | ❌ | ❌ |
| MCP-native | ✅ | ❌ | ❌ | ❌ |
| Cross-session | ✅ | ✅ | ❌ (per-conversation) | ✅ |
| Auto-summarize sessions | ✅ | ❌ | ❌ | ✅ |
| Smart decay (LRU + recency) | ✅ | ❌ | ❌ | ❌ |
| Secret redaction built-in | ✅ | ❌ | ❌ | ❌ |
| Export / import | ✅ | partial | ❌ | ❌ |
| Free (no paid plan) | ✅ | $ | ✅ | $ |
| Setup time | 30 sec | 10 min | 5 min | 10 min |

## ⚙️ Configuration (env vars)

| Var | Default | Description |
|-----|---------|-------------|
| `RECALL_MCP_DB` | `~/.recall/memory.db` | Path to SQLite DB file |
| `RECALL_MAX_LENGTH` | `50000` | Max chars per memory (truncates with notice) |
| `RECALL_DECAY_DAYS` | `90` | Days at full weight before decay starts |
| `RECALL_MAX_RESULTS` | `50` | Hard cap on `recall` and `list_memories` limits |
| `RECALL_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` (stderr only) |

## 🧪 Testing

```bash
# Run the smoke test suite (14 tests, no network needed)
curl -sSL https://github.com/eddyflores100-lang/recall-mcp/raw/main/test_recall.py -o /tmp/test_recall.py
python3 /tmp/test_recall.py
# Expected: "=== ALL TESTS PASSED ==="
```

The smoke test covers: initialize handshake, tools/list, remember, recall, duplicate detection, secret redaction, summarize_session, get_stats, list_memories, forget, export/import round-trip, and error handling.

## 🗺️ Roadmap

- [ ] Optional vector embeddings (with `OPENAI_API_KEY` or local sentence-transformers) for true semantic search beyond FTS5
- [ ] Multi-agent memory sharing (memory namespaces)
- [ ] Web UI for browsing / searching / editing memories
- [ ] Backup to S3 / Dropbox / gists
- [ ] MCP resources endpoint (expose memories as browsable resources)
- [ ] Auto-tagging (extract dates, URLs, file paths from content)
- [ ] CLI tool (`recall search "query"`, `recall add "text"`)
- [ ] Plugin SDK for custom extractors in `summarize_session`

## 🤝 Why I built this

I ship MCP servers for a living (see also: [`context-pruner-mcp`](https://github.com/eddyflores100-lang/context-pruner-mcp)). After using agents for months, the single biggest quality boost came not from better models or bigger context windows — it came from giving the agent a way to **not forget**. Context pruning squeezes more juice out of the current session; long-term memory means the next session starts ahead instead of from zero.

If this saves you 10 minutes of re-explaining per session, that's roughly 40 hours a year for a daily user. Open source it so everyone gets those hours back.

## 📄 License

MIT — see [LICENSE](LICENSE).

## ⭐ Star History

If this saved you time, please ⭐ the repo — it helps others discover it.

---

**Author:** Eddy Flores ([`eddyflores100-lang`](https://github.com/eddyflores100-lang))

**Issues / feature requests:** [GitHub Issues](https://github.com/eddyflores100-lang/recall-mcp/issues)
