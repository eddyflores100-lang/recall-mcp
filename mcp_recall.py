#!/usr/bin/env python3
"""
Recall MCP — Long-term memory for AI agents.

A single-file Model Context Protocol server that gives AI agents
persistent, searchable memory across sessions. Powered by SQLite + FTS5.
Zero external dependencies (pure Python stdlib + sqlite3).

Tools exposed (MCP):
    - remember             Store a memory (with tags / project / importance)
    - recall               Semantic search across memories (FTS5 + BM25 ranking)
    - forget               Delete a memory by ID or content match
    - list_memories        List with filters (project / tag / limit)
    - summarize_session    Auto-extract memories from a conversation
    - get_stats           Counts, oldest, most accessed, projects
    - export_memories     Dump memories as JSON
    - import_memories     Load memories from JSON

Configuration (environment variables):
    RECALL_MCP_DB          Path to SQLite DB (default ~/.recall/memory.db)
    RECALL_MAX_LENGTH      Max chars per memory (default 50000)
    RECALL_DECAY_DAYS      Days before decay starts (default 90)
    RECALL_MAX_RESULTS     Default cap on recall/list (default 50)
    RECALL_LOG_LEVEL       DEBUG | INFO | WARNING | ERROR (default INFO)

Author: Eddy Flores (eddyflores100-lang)
License: MIT
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ============================================================================
# Configuration
# ============================================================================

DEFAULT_DB_PATH = str(Path.home() / ".recall" / "memory.db")
DB_PATH = os.environ.get("RECALL_MCP_DB", DEFAULT_DB_PATH)

MAX_MEMORY_LENGTH = int(os.environ.get("RECALL_MAX_LENGTH", "50000"))
DECAY_DAYS = int(os.environ.get("RECALL_DECAY_DAYS", "90"))
MAX_RESULTS = int(os.environ.get("RECALL_MAX_RESULTS", "50"))
LOG_LEVEL = os.environ.get("RECALL_LOG_LEVEL", "INFO").upper()

# ============================================================================
# Logging (stderr only — stdout is reserved for MCP protocol)
# ============================================================================

_LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}


def _log(level: str, msg: str) -> None:
    if _LOG_LEVELS.get(level, 20) < _LOG_LEVELS.get(LOG_LEVEL, 20):
        return
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[{ts}] [{level}] recall: {msg}", file=sys.stderr, flush=True)


# ============================================================================
# Secret / PII redaction
# ============================================================================

# Patterns that should never be stored as memories.
_SECRET_PATTERNS = [
    # GitHub personal access tokens (classic + fine-grained)
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    # OpenAI / Anthropic / Gemini API keys
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b"),
    # AWS access keys
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # JWTs (header.payload.signature, base64-ish)
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    # Generic bearer tokens
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{20,}\b"),
    # Stripe keys
    re.compile(r"\b(sk|pk|rk)_(live|test)_[A-Za-z0-9]{20,}\b"),
    # Slack tokens
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
    # Private keys
    re.compile(r"-----BEGIN (RSA|EC|OPENSSH|PGP|PRIVATE) (PRIVATE KEY|KEY)-----"),
    # Generic password= assignments
    re.compile(r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+"),
    # Connection strings with embedded creds
    re.compile(r"(?i)(postgres|mysql|mongodb|redis)://[^:\s]+:[^@\s]+@"),
]

_SECRET_REPLACEMENT = "[REDACTED]"


def _redact(text: str) -> str:
    """Replace secrets with [REDACTED] before storage."""
    if not text:
        return text
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub(_SECRET_REPLACEMENT, out)
    return out


def _contains_secret(text: str) -> bool:
    if not text:
        return False
    return any(pat.search(text) for pat in _SECRET_PATTERNS)


# ============================================================================
# Database schema
# ============================================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id              TEXT PRIMARY KEY,
    content         TEXT NOT NULL,
    tags            TEXT,         -- JSON array of strings
    project         TEXT,         -- project name (nullable)
    source          TEXT,         -- 'user' | 'agent' | 'auto'
    created_at      REAL NOT NULL,
    last_accessed_at REAL NOT NULL,
    access_count    INTEGER NOT NULL DEFAULT 0,
    importance      REAL NOT NULL DEFAULT 0.5,    -- 0.0 .. 1.0
    content_hash    TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
CREATE INDEX IF NOT EXISTS idx_memories_accessed ON memories(last_accessed_at);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='rowid',
    tokenize='porter unicode61 remove_diacritics 2'
);

-- Triggers to keep FTS in sync with the main table
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts('memories_fts', 'rowid', 'content')
        VALUES('delete', old.rowid, old.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts('memories_fts', 'rowid', 'content')
        VALUES('delete', old.rowid, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;
"""


# ============================================================================
# Memory store
# ============================================================================


class MemoryStore:
    """SQLite-backed memory store with FTS5 semantic-ish search."""

    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        _log("INFO", f"Memory store opened at {db_path}")

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _now() -> float:
        return time.time()

    @staticmethod
    def _normalize_tags(tags: Any) -> List[str]:
        if tags is None:
            return []
        if isinstance(tags, str):
            # comma-separated string
            return [t.strip().lower() for t in tags.split(",") if t.strip()]
        if isinstance(tags, (list, tuple)):
            return [str(t).strip().lower() for t in tags if str(t).strip()]
        return []

    @staticmethod
    def _validate_source(source: Optional[str]) -> str:
        if source in ("user", "agent", "auto", None):
            return source or "user"
        return "user"

    @staticmethod
    def _validate_importance(importance: Optional[float]) -> float:
        try:
            v = float(importance)
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(1.0, v))

    # ------------------------------------------------------------- operations

    def remember(
        self,
        content: str,
        tags: Any = None,
        project: Optional[str] = None,
        source: Optional[str] = None,
        importance: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Store a memory. Returns dict with id, status, duplicate flag."""
        if not content or not content.strip():
            raise ValueError("content must be non-empty")

        content = content.strip()
        if len(content) > MAX_MEMORY_LENGTH:
            content = content[: MAX_MEMORY_LENGTH - 20] + "\n…[truncated]"
        content = _redact(content)

        content_hash = self._hash(content)
        existing = self.conn.execute(
            "SELECT id, access_count FROM memories WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if existing:
            # bump access count for duplicate; don't re-insert
            self.conn.execute(
                "UPDATE memories SET access_count = ?, last_accessed_at = ? WHERE id = ?",
                (existing["access_count"] + 1, self._now(), existing["id"]),
            )
            self.conn.commit()
            return {
                "id": existing["id"],
                "status": "duplicate",
                "message": "Memory already exists; access count bumped.",
            }

        tags_list = self._normalize_tags(tags)
        mem_id = str(uuid.uuid4())
        now = self._now()
        self.conn.execute(
            """INSERT INTO memories
               (id, content, tags, project, source, created_at,
                last_accessed_at, access_count, importance, content_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (
                mem_id,
                content,
                json.dumps(tags_list),
                project,
                self._validate_source(source),
                now,
                now,
                self._validate_importance(importance),
                content_hash,
            ),
        )
        self.conn.commit()
        _log("DEBUG", f"remember: stored {mem_id} (project={project})")
        return {
            "id": mem_id,
            "status": "created",
            "tags": tags_list,
            "project": project,
            "importance": self._validate_importance(importance),
        }

    def recall(
        self,
        query: str,
        limit: int = 10,
        project: Optional[str] = None,
        tags: Any = None,
        min_importance: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Semantic-ish search via FTS5 BM25 + recency/importance boost."""
        if not query or not query.strip():
            return []

        limit = max(1, min(int(limit), MAX_RESULTS))
        # Build FTS5 query: prefix tokens joined with OR for high recall.
        # NOTE: FTS5 does NOT support '"token"'* (quoted + prefix) — must use bare
        # prefix form: token*. We also strip FTS5 special chars to avoid parse errors.
        terms = [re.sub(r'[^\w]', '', t) for t in re.split(r"\s+", query.strip()) if t]
        terms = [t for t in terms if len(t) >= 2]  # skip 1-char noise
        if not terms:
            return []
        fts_query = " OR ".join(f"{t}*" for t in terms)

        sql = (
            "SELECT m.id, m.content, m.tags, m.project, m.source, "
            "       m.created_at, m.last_accessed_at, m.access_count, m.importance, "
            "       bm25(memories_fts) AS rank "
            "FROM memories_fts JOIN memories m ON m.rowid = memories_fts.rowid "
            "WHERE memories_fts MATCH ? "
        )
        params: List[Any] = [fts_query]
        if project:
            sql += " AND m.project = ? "
            params.append(project)
        if min_importance > 0:
            sql += " AND m.importance >= ? "
            params.append(float(min_importance))
        sql += " ORDER BY rank ASC LIMIT ? "
        params.append(limit * 3)  # over-fetch for re-ranking

        rows = self.conn.execute(sql, params).fetchall()

        # Re-rank by combining BM25 score with recency + importance + access count
        now = self._now()
        results: List[Dict[str, Any]] = []
        for r in rows:
            age_days = max(0.0, (now - r["created_at"]) / 86400.0)
            # decay: full weight for first DECAY_DAYS, then linear fade
            decay = 1.0 if age_days <= DECAY_DAYS else max(
                0.25, DECAY_DAYS / max(age_days, 1.0)
            )
            # BM25 returns negative ranks (more negative = better match)
            bm25_score = -float(r["rank"]) if r["rank"] is not None else 0.0
            score = (
                bm25_score * 1.0
                + decay * 0.5
                + float(r["importance"]) * 0.5
                + min(float(r["access_count"]) * 0.05, 0.5)
            )

            tags_list = json.loads(r["tags"]) if r["tags"] else []
            if tags:
                wanted = set(self._normalize_tags(tags))
                if wanted and not wanted.intersection(tags_list):
                    continue

            results.append(
                {
                    "id": r["id"],
                    "content": r["content"],
                    "tags": tags_list,
                    "project": r["project"],
                    "source": r["source"],
                    "created_at": r["created_at"],
                    "last_accessed_at": r["last_accessed_at"],
                    "access_count": r["access_count"],
                    "importance": r["importance"],
                    "score": round(score, 4),
                }
            )

        # Sort by combined score and trim
        results.sort(key=lambda x: x["score"], reverse=True)
        results = results[:limit]

        # Bump access counts
        for r in results:
            self.conn.execute(
                "UPDATE memories SET access_count = access_count + 1, "
                "last_accessed_at = ? WHERE id = ?",
                (now, r["id"]),
            )
        self.conn.commit()
        _log("DEBUG", f"recall: '{query}' -> {len(results)} results")
        return results

    def forget(self, id: Optional[str] = None, query: Optional[str] = None) -> int:
        """Delete by id or by FTS match. Returns count deleted."""
        if not id and not query:
            raise ValueError("forget requires either id or query")
        if id:
            cur = self.conn.execute("DELETE FROM memories WHERE id = ?", (id,))
            self.conn.commit()
            return cur.rowcount
        # query: FTS match (same prefix-OR form as recall)
        terms = [re.sub(r'[^\w]', '', t) for t in query.strip().split() if t]
        terms = [t for t in terms if len(t) >= 2]
        if not terms:
            return 0
        fts_query = " OR ".join(f"{t}*" for t in terms)
        rows = self.conn.execute(
            "SELECT m.id FROM memories_fts JOIN memories m ON m.rowid = memories_fts.rowid "
            "WHERE memories_fts MATCH ?",
            (fts_query,),
        ).fetchall()
        ids = [r["id"] for r in rows]
        if not ids:
            return 0
        cur = self.conn.executemany(
            "DELETE FROM memories WHERE id = ?", [(i,) for i in ids]
        )
        self.conn.commit()
        return len(ids)

    def list_memories(
        self,
        project: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 50,
        order: str = "recent",
    ) -> List[Dict[str, Any]]:
        """List memories with filters. order: recent | oldest | accessed | important."""
        limit = max(1, min(int(limit), MAX_RESULTS))
        sql = (
            "SELECT id, content, tags, project, source, created_at, "
            "       last_accessed_at, access_count, importance "
            "FROM memories WHERE 1=1 "
        )
        params: List[Any] = []
        if project:
            sql += " AND project = ? "
            params.append(project)
        if tag:
            # JSON array membership via LIKE
            sql += " AND lower(tags) LIKE ? "
            params.append(f'%"{tag.lower()}"%')
        order = (order or "recent").lower()
        if order == "oldest":
            sql += " ORDER BY created_at ASC "
        elif order == "accessed":
            sql += " ORDER BY access_count DESC "
        elif order == "important":
            sql += " ORDER BY importance DESC, created_at DESC "
        else:  # recent
            sql += " ORDER BY created_at DESC "
        sql += " LIMIT ? "
        params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        return [
            {
                "id": r["id"],
                "content": r["content"],
                "tags": json.loads(r["tags"]) if r["tags"] else [],
                "project": r["project"],
                "source": r["source"],
                "created_at": r["created_at"],
                "last_accessed_at": r["last_accessed_at"],
                "access_count": r["access_count"],
                "importance": r["importance"],
            }
            for r in rows
        ]

    def summarize_session(
        self,
        messages: Any,
        project: Optional[str] = None,
        max_memories: int = 5,
    ) -> Dict[str, Any]:
        """Extract memories from a conversation. Heuristic: pull facts, decisions,
        code refs, and TODO items from user/assistant messages."""
        if isinstance(messages, str):
            try:
                messages = json.loads(messages)
            except json.JSONDecodeError:
                messages = [{"role": "user", "content": messages}]
        if not isinstance(messages, list):
            raise ValueError("messages must be a list of {role, content} dicts")

        # Patterns we treat as "memorable"
        fact_patterns = [
            re.compile(r"(?im)^(?:user|assistant):\s*(.+)$"),
            re.compile(r"(?i)(?:prefer\w*|always|never|don't|please remember)\s+(.+?)\."),
            re.compile(r"(?i)(?:decided|decision|agreed|concluded)\s*:?\s*(.+?)\."),
            re.compile(r"(?i)(?:todo|fixme|bug|issue|task)\s*[:=]\s*(.+?)\."),
            re.compile(r"(?i)(?:rule|convention|standard|guideline)\s*:?\s*(.+?)\."),
            re.compile(r"(?i)(?:file|module|function|class|component)\s+(\w[\w\.\-]+)"),
            re.compile(r"(?im)```(\w+)?\n([\s\S]+?)```"),
        ]

        extracted: List[str] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = (msg.get("role") or "").lower()
            content = msg.get("content") or ""
            if not isinstance(content, str) or not content.strip():
                continue
            # Per-message heuristic: short factual sentences are more likely memories
            sentences = re.split(r"(?<=[\.\?\!])\s+", content)
            for sent in sentences:
                sent = sent.strip()
                if len(sent) < 10 or len(sent) > 400:
                    continue
                # Prefer sentences that look like facts/preferences
                if re.search(
                    r"\b(prefer|always|never|use|don't|avoid|standard|convention|"
                    r"decided|agreed|configured|installed|created|deployed|fixed)\b",
                    sent,
                    re.IGNORECASE,
                ):
                    # Prefix with role for context
                    tag_src = "user" if role == "user" else "agent"
                    extracted.append(f"[{tag_src}] {sent}")

        # Dedupe and cap
        seen = set()
        unique = []
        for e in extracted:
            key = e.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(e)
        unique = unique[:max_memories]

        # Store each as a memory with source='auto'
        stored: List[Dict[str, Any]] = []
        for e in unique:
            try:
                res = self.remember(content=e, project=project, source="auto")
                stored.append(res)
            except ValueError:
                continue

        return {
            "extracted": len(unique),
            "stored": len([s for s in stored if s.get("status") == "created"]),
            "duplicates": len([s for s in stored if s.get("status") == "duplicate"]),
            "memories": unique,
            "ids": [s.get("id") for s in stored],
        }

    def get_stats(self) -> Dict[str, Any]:
        """Aggregate stats about the memory store."""
        total = self.conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"]
        if total == 0:
            return {
                "total": 0,
                "by_project": {},
                "by_source": {},
                "by_tag": {},
                "oldest": None,
                "newest": None,
                "most_accessed": [],
                "avg_importance": 0.0,
                "db_size_bytes": os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0,
            }
        by_project: Dict[str, int] = {}
        for r in self.conn.execute(
            "SELECT project, COUNT(*) AS n FROM memories GROUP BY project"
        ).fetchall():
            by_project[r["project"] or "(none)"] = r["n"]

        by_source: Dict[str, int] = {}
        for r in self.conn.execute(
            "SELECT source, COUNT(*) AS n FROM memories GROUP BY source"
        ).fetchall():
            by_source[r["source"] or "(none)"] = r["n"]

        by_tag: Dict[str, int] = {}
        for r in self.conn.execute("SELECT tags FROM memories").fetchall():
            for t in json.loads(r["tags"]) if r["tags"] else []:
                by_tag[t] = by_tag.get(t, 0) + 1
        # top 10 tags
        by_tag = dict(sorted(by_tag.items(), key=lambda x: -x[1])[:10])

        oldest = self.conn.execute(
            "SELECT id, content, created_at FROM memories ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        newest = self.conn.execute(
            "SELECT id, content, created_at FROM memories ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        most_accessed = [
            {
                "id": r["id"],
                "content": r["content"][:200],
                "access_count": r["access_count"],
            }
            for r in self.conn.execute(
                "SELECT id, content, access_count FROM memories "
                "ORDER BY access_count DESC LIMIT 5"
            ).fetchall()
        ]
        avg_imp = self.conn.execute(
            "SELECT AVG(importance) AS v FROM memories"
        ).fetchone()["v"] or 0.0

        return {
            "total": total,
            "by_project": by_project,
            "by_source": by_source,
            "by_tag": by_tag,
            "oldest": {
                "id": oldest["id"],
                "content": oldest["content"][:200],
                "created_at": oldest["created_at"],
            }
            if oldest
            else None,
            "newest": {
                "id": newest["id"],
                "content": newest["content"][:200],
                "created_at": newest["created_at"],
            }
            if newest
            else None,
            "most_accessed": most_accessed,
            "avg_importance": round(float(avg_imp), 4),
            "db_size_bytes": os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0,
        }

    def export_memories(self, project: Optional[str] = None) -> Dict[str, Any]:
        """Export memories as JSON-serializable dict."""
        sql = (
            "SELECT id, content, tags, project, source, created_at, "
            "       last_accessed_at, access_count, importance "
            "FROM memories"
        )
        params: List[Any] = []
        if project:
            sql += " WHERE project = ? "
            params.append(project)
        sql += " ORDER BY created_at ASC"
        rows = self.conn.execute(sql, params).fetchall()
        return {
            "version": 1,
            "exported_at": self._now(),
            "count": len(rows),
            "memories": [
                {
                    "id": r["id"],
                    "content": r["content"],
                    "tags": json.loads(r["tags"]) if r["tags"] else [],
                    "project": r["project"],
                    "source": r["source"],
                    "created_at": r["created_at"],
                    "last_accessed_at": r["last_accessed_at"],
                    "access_count": r["access_count"],
                    "importance": r["importance"],
                }
                for r in rows
            ],
        }

    def import_memories(self, data: Any, on_duplicate: str = "skip") -> Dict[str, Any]:
        """Import memories from a JSON dict or string. on_duplicate: skip | bump | replace."""
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON: {e}")
        if not isinstance(data, dict) or "memories" not in data:
            raise ValueError("Expected {version, memories: [...]}")
        on_duplicate = (on_duplicate or "skip").lower()
        if on_duplicate not in ("skip", "bump", "replace"):
            raise ValueError("on_duplicate must be skip | bump | replace")

        created = 0
        skipped = 0
        bumped = 0
        replaced = 0
        for m in data["memories"]:
            content = (m.get("content") or "").strip()
            if not content:
                continue
            content = _redact(content)
            content_hash = self._hash(content)
            existing = self.conn.execute(
                "SELECT id FROM memories WHERE content_hash = ?", (content_hash,)
            ).fetchone()
            if existing:
                if on_duplicate == "skip":
                    skipped += 1
                elif on_duplicate == "bump":
                    self.conn.execute(
                        "UPDATE memories SET access_count = access_count + 1, "
                        "last_accessed_at = ? WHERE id = ?",
                        (self._now(), existing["id"]),
                    )
                    bumped += 1
                elif on_duplicate == "replace":
                    self.conn.execute(
                        "UPDATE memories SET tags = ?, project = ?, importance = ?, "
                        "last_accessed_at = ? WHERE id = ?",
                        (
                            json.dumps(self._normalize_tags(m.get("tags"))),
                            m.get("project"),
                            self._validate_importance(m.get("importance")),
                            self._now(),
                            existing["id"],
                        ),
                    )
                    replaced += 1
                continue
            # Insert new
            self.conn.execute(
                """INSERT INTO memories
                   (id, content, tags, project, source, created_at,
                    last_accessed_at, access_count, importance, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    m.get("id") or str(uuid.uuid4()),
                    content,
                    json.dumps(self._normalize_tags(m.get("tags"))),
                    m.get("project"),
                    self._validate_source(m.get("source")),
                    m.get("created_at") or self._now(),
                    m.get("last_accessed_at") or self._now(),
                    int(m.get("access_count") or 0),
                    self._validate_importance(m.get("importance")),
                    content_hash,
                ),
            )
            created += 1
        self.conn.commit()
        return {
            "created": created,
            "skipped": skipped,
            "bumped": bumped,
            "replaced": replaced,
        }

    def close(self) -> None:
        self.conn.close()


# ============================================================================
# MCP protocol (JSON-RPC over stdio)
# ============================================================================

# Tool registry: name -> (handler, input_schema, description)
TOOLS: Dict[str, Dict[str, Any]] = {
    "remember": {
        "description": (
            "Store a long-term memory. Use this whenever the user states a preference, "
            "decision, fact, or piece of context worth recalling in future sessions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The memory text to store.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for categorization.",
                },
                "project": {
                    "type": "string",
                    "description": "Optional project name to scope this memory.",
                },
                "source": {
                    "type": "string",
                    "enum": ["user", "agent", "auto"],
                    "description": "Who created the memory. Default: user.",
                },
                "importance": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "0..1 importance score. Default: 0.5.",
                },
            },
            "required": ["content"],
        },
    },
    "recall": {
        "description": (
            "Semantic search across all stored memories. Use this at the start of a "
            "session to recover context from prior sessions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language query."},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 10,
                },
                "project": {
                    "type": "string",
                    "description": "Restrict to a project.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by tags (any-match).",
                },
                "min_importance": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 0,
                },
            },
            "required": ["query"],
        },
    },
    "forget": {
        "description": "Delete a memory by id or by content match (FTS query).",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "query": {"type": "string", "description": "FTS query; deletes all matches."},
            },
        },
    },
    "list_memories": {
        "description": "List memories with optional filters and ordering.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "tag": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                "order": {
                    "type": "string",
                    "enum": ["recent", "oldest", "accessed", "important"],
                    "default": "recent",
                },
            },
        },
    },
    "summarize_session": {
        "description": (
            "Auto-extract memorable facts/preferences/decisions from a list of "
            "conversation messages and store them as memories."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string"},
                            "content": {"type": "string"},
                        },
                    },
                    "description": "List of {role, content} messages, or a JSON string.",
                },
                "project": {"type": "string"},
                "max_memories": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 5,
                },
            },
            "required": ["messages"],
        },
    },
    "get_stats": {
        "description": "Return aggregate stats: total, per-project, per-tag, oldest/newest, most-accessed.",
        "input_schema": {"type": "object", "properties": {}},
    },
    "export_memories": {
        "description": "Export all memories (or one project) as a JSON dict.",
        "input_schema": {
            "type": "object",
            "properties": {"project": {"type": "string"}},
        },
    },
    "import_memories": {
        "description": "Import memories from JSON. on_duplicate: skip | bump | replace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "data": {
                    "type": ["object", "string"],
                    "description": "Either a JSON object {version, memories:[...]} or a JSON string.",
                },
                "on_duplicate": {
                    "type": "string",
                    "enum": ["skip", "bump", "replace"],
                    "default": "skip",
                },
            },
            "required": ["data"],
        },
    },
}


# ============================================================================
# JSON-RPC helpers (minimal, no external deps)
# ============================================================================


def _jsonrpc_response(req_id: Any, result: Any) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})


def _jsonrpc_error(req_id: Any, code: int, message: str, data: Any = None) -> str:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "error": err})


def _text_content(text: str) -> List[Dict[str, Any]]:
    return [{"type": "text", "text": text}]


def _tool_response(req_id: Any, payload: Any) -> str:
    """Wrap a payload as a CallToolResult."""
    if isinstance(payload, (dict, list)):
        text = json.dumps(payload, default=str, indent=2)
    else:
        text = str(payload)
    result = {"content": _text_content(text)}
    return _jsonrpc_response(req_id, result)


# ============================================================================
# Tool dispatch
# ============================================================================


def _dispatch_tool(store: MemoryStore, name: str, args: Dict[str, Any]) -> Any:
    if name == "remember":
        return store.remember(
            content=args["content"],
            tags=args.get("tags"),
            project=args.get("project"),
            source=args.get("source"),
            importance=args.get("importance"),
        )
    if name == "recall":
        return store.recall(
            query=args["query"],
            limit=args.get("limit", 10),
            project=args.get("project"),
            tags=args.get("tags"),
            min_importance=args.get("min_importance", 0.0),
        )
    if name == "forget":
        return {"deleted": store.forget(id=args.get("id"), query=args.get("query"))}
    if name == "list_memories":
        return store.list_memories(
            project=args.get("project"),
            tag=args.get("tag"),
            limit=args.get("limit", 50),
            order=args.get("order", "recent"),
        )
    if name == "summarize_session":
        return store.summarize_session(
            messages=args["messages"],
            project=args.get("project"),
            max_memories=args.get("max_memories", 5),
        )
    if name == "get_stats":
        return store.get_stats()
    if name == "export_memories":
        return store.export_memories(project=args.get("project"))
    if name == "import_memories":
        return store.import_memories(
            data=args["data"], on_duplicate=args.get("on_duplicate", "skip")
        )
    raise ValueError(f"Unknown tool: {name}")


# ============================================================================
# Main loop
# ============================================================================


def _handle_request(store: MemoryStore, line: str) -> Optional[str]:
    """Parse and handle a single JSON-RPC request. Return response string or None."""
    try:
        req = json.loads(line)
    except json.JSONDecodeError as e:
        return _jsonrpc_error(None, -32700, f"Parse error: {e}")

    req_id = req.get("id")
    method = req.get("method")

    if method == "initialize":
        return _jsonrpc_response(
            req_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "recall-mcp",
                    "version": "1.0.0",
                },
            },
        )
    if method == "initialized" or method == "notifications/initialized":
        return None  # notification — no response
    if method == "ping":
        return _jsonrpc_response(req_id, {})
    if method == "tools/list":
        tools = [
            {
                "name": n,
                "description": t["description"],
                "inputSchema": t["input_schema"],
            }
            for n, t in TOOLS.items()
        ]
        return _jsonrpc_response(req_id, {"tools": tools})
    if method == "tools/call":
        params = req.get("params") or {}
        tool_name = params.get("name")
        if tool_name not in TOOLS:
            return _jsonrpc_error(req_id, -32602, f"Unknown tool: {tool_name}")
        try:
            args = params.get("arguments") or {}
            payload = _dispatch_tool(store, tool_name, args)
            return _tool_response(req_id, payload)
        except ValueError as e:
            return _tool_response(req_id, {"error": str(e)})
        except Exception as e:
            _log("ERROR", f"tool call failed: {e}")
            return _tool_response(req_id, {"error": f"internal error: {e}"})
    if method == "resources/list":
        return _jsonrpc_response(req_id, {"resources": []})
    if method == "prompts/list":
        return _jsonrpc_response(req_id, {"prompts": []})

    return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")


def main() -> int:
    store = MemoryStore()
    _log("INFO", "Recall MCP server starting (stdio mode)")
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            resp = _handle_request(store, line)
            if resp is not None:
                sys.stdout.write(resp + "\n")
                sys.stdout.flush()
    except KeyboardInterrupt:
        _log("INFO", "Interrupted by user")
    finally:
        store.close()
        _log("INFO", "Recall MCP server stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
