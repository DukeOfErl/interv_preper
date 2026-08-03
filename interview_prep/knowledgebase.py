"""Persistent curated knowledge base: SQLite store with per-model embeddings.

Coach-side reference material (interview best practices, question banks tagged
by role, legal guidelines, bias-reduction tips, …) that persists across
sessions, unlike the session-scoped ``DocumentIndex``. The source of truth is
the seed folder — markdown files with YAML frontmatter, versioned in git; the
SQLite file is a derived artifact holding the chunked text plus cached
embeddings, so a correct DB is always reproducible from the repo (ADR-0110).

``sync()`` runs two reconciliations. *Content*: each seed file is compared by
content hash — new/changed files are re-chunked, deleted files removed, and
unchanged files skipped, so startup normally touches the network zero times.
*Coverage*: embeddings are cached per (chunk, model), and only chunks lacking
a vector for the active embedding model are embedded — switching back to a
previously used model re-embeds nothing.

Retrieval is brute-force cosine over the cached vectors in numpy. At curated
scale (hundreds of chunks) that is milliseconds and needs no native extension;
sqlite-vec remains the upgrade path if the corpus ever outgrows it.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import (
    CHUNK_OVERLAP_CHARS,
    CHUNK_SIZE_CHARS,
    KB_TOP_K,
    OPENROUTER_BASE_URL,
)
from .retrieval import RetrievedChunk

# Bumped when the table layout changes. The DB is a derived artifact — on a
# version mismatch the tables are dropped and rebuilt from the seeds rather
# than migrated.
_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_id INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY (chunk_id, model)
);
"""

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

DEFAULT_CATEGORY = "general"


@dataclass(frozen=True)
class SyncReport:
    """What one ``sync()`` call did — all zeros on the no-op fast path."""

    added: int = 0
    updated: int = 0
    removed: int = 0
    chunks_embedded: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed or self.chunks_embedded)


@dataclass(frozen=True)
class KBDocument:
    """One knowledge-base document, as shown in the sidebar panel."""

    name: str
    category: str
    tags: dict
    n_chunks: int


def parse_seed(raw: str) -> tuple[str, dict, str]:
    """Split a seed file into (category, tags, body).

    The optional YAML frontmatter carries ``category`` (a short provenance
    label shown in the context block) and ``tags`` (a free-form dict, e.g.
    roles / seniority / countries). Files without frontmatter get the default
    category and no tags.
    """
    match = _FRONTMATTER.match(raw)
    if not match:
        return DEFAULT_CATEGORY, {}, raw.strip()
    meta = yaml.safe_load(match.group(1))
    meta = meta if isinstance(meta, dict) else {}
    tags = meta.get("tags")
    return (
        str(meta.get("category", DEFAULT_CATEGORY)),
        tags if isinstance(tags, dict) else {},
        raw[match.end() :].strip(),
    )


def _tags_line(tags: dict) -> str:
    """Render tags as a compact bracket line, prepended to every chunk.

    Living inside the chunk text, the tags reach both the embedding (so
    "senior backend questions" matches a chunk tagged with that role) and the
    context block (so the interviewer sees which roles a question bank fits).
    """
    if not tags:
        return ""
    parts = [
        f"{key}: {', '.join(map(str, value)) if isinstance(value, list) else value}"
        for key, value in tags.items()
    ]
    return "[" + "; ".join(parts) + "]\n"


class KnowledgeBase:
    """SQLite-backed knowledge base with per-model cached embeddings."""

    def __init__(self, db_path, api_key, embeddings_factory=None):
        self._api_key = api_key
        # Allow an injected factory (tests); otherwise embed via OpenRouter,
        # same client settings as DocumentIndex.
        self._embeddings_factory = embeddings_factory or self._openrouter_embeddings
        self._embedders: dict[str, object] = {}
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE_CHARS, chunk_overlap=CHUNK_OVERLAP_CHARS
        )
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: Streamlit reruns the script on varying
        # worker threads while this object lives in session state; access
        # within a session is still sequential.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self._conn.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION:
            self._conn.executescript(
                "DROP TABLE IF EXISTS embeddings;"
                "DROP TABLE IF EXISTS chunks;"
                "DROP TABLE IF EXISTS documents;"
                f"PRAGMA user_version = {_SCHEMA_VERSION};"
            )
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # Per-model (rows, matrix) retrieval cache; dropped whenever sync
        # changes anything.
        self._cache: dict[str, tuple[list, np.ndarray]] = {}

    def _openrouter_embeddings(self, model):
        # check_embedding_ctx_length uses tiktoken to pre-tokenize, which only
        # works for OpenAI-named models — disabled so any catalog model works.
        return OpenAIEmbeddings(
            model=model,
            api_key=self._api_key,
            base_url=OPENROUTER_BASE_URL,
            check_embedding_ctx_length=False,
        )

    def _embedder(self, model):
        if model not in self._embedders:
            self._embedders[model] = self._embeddings_factory(model)
        return self._embedders[model]

    @property
    def is_empty(self) -> bool:
        return self._conn.execute("SELECT 1 FROM chunks LIMIT 1").fetchone() is None

    def documents(self) -> list[KBDocument]:
        rows = self._conn.execute(
            "SELECT d.name, d.category, d.tags, COUNT(c.id)"
            " FROM documents d LEFT JOIN chunks c ON c.doc_id = d.id"
            " GROUP BY d.id ORDER BY d.name"
        ).fetchall()
        return [
            KBDocument(name=n, category=c, tags=json.loads(t), n_chunks=k)
            for n, c, t, k in rows
        ]

    def sync(self, seed_dir, model) -> SyncReport:
        """Reconcile the DB with the seed folder and the active model.

        Content pass: per-file hash comparison — only new/changed seeds are
        re-chunked (their cached embeddings cascade away), vanished seeds are
        deleted. Coverage pass: embed exactly the chunks that have no vector
        under ``model``, in one batch. Both passes are no-ops on a warm DB.
        """
        seed_dir = Path(seed_dir)
        files = sorted(seed_dir.glob("*.md")) if seed_dir.is_dir() else []
        stored = {
            name: (doc_id, digest)
            for doc_id, name, digest in self._conn.execute(
                "SELECT id, name, content_hash FROM documents"
            )
        }
        added = updated = removed = 0
        seen = set()
        for path in files:
            raw = path.read_text(encoding="utf-8")
            name = path.stem
            seen.add(name)
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            previous = stored.get(name)
            if previous and previous[1] == digest:
                continue
            self._replace_document(name, digest, raw)
            if previous:
                updated += 1
            else:
                added += 1
        for name, (doc_id, _) in stored.items():
            if name not in seen:
                self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                removed += 1

        missing = self._conn.execute(
            "SELECT c.id, c.text FROM chunks c WHERE NOT EXISTS ("
            " SELECT 1 FROM embeddings e WHERE e.chunk_id = c.id AND e.model = ?)",
            (model,),
        ).fetchall()
        if missing:
            vectors = self._embedder(model).embed_documents([t for _, t in missing])
            self._conn.executemany(
                "INSERT INTO embeddings (chunk_id, model, vector) VALUES (?, ?, ?)",
                [
                    (chunk_pk, model, np.asarray(vec, dtype=np.float32).tobytes())
                    for (chunk_pk, _), vec in zip(missing, vectors)
                ],
            )

        self._conn.commit()
        report = SyncReport(
            added=added,
            updated=updated,
            removed=removed,
            chunks_embedded=len(missing),
        )
        if report.changed:
            self._cache.clear()
        return report

    def _replace_document(self, name, digest, raw) -> None:
        category, tags, body = parse_seed(raw)
        self._conn.execute("DELETE FROM documents WHERE name = ?", (name,))
        cursor = self._conn.execute(
            "INSERT INTO documents (name, category, tags, content_hash)"
            " VALUES (?, ?, ?, ?)",
            (name, category, json.dumps(tags), digest),
        )
        prefix = _tags_line(tags)
        self._conn.executemany(
            "INSERT INTO chunks (doc_id, chunk_id, text) VALUES (?, ?, ?)",
            [
                (cursor.lastrowid, i, prefix + chunk)
                for i, chunk in enumerate(self._splitter.split_text(body))
            ],
        )

    def retrieve(self, query, model, k: int = KB_TOP_K) -> list[RetrievedChunk]:
        """Top-k chunks for ``query`` by cosine similarity under ``model``.

        Returned as ``RetrievedChunk`` with ``doc_type="knowledge base"`` and
        the category as the topic, so ``format_context_block`` renders the
        ``### knowledge base — <category>: <name> (part N)`` header without
        any knowledge-base-specific code.
        """
        rows, matrix = self._load(model)
        if not rows:
            return []
        query_vec = np.asarray(
            self._embedder(model).embed_query(query), dtype=np.float32
        )
        query_vec /= np.linalg.norm(query_vec) or 1.0
        scores = matrix @ query_vec
        top = np.argsort(scores)[::-1][:k]
        return [
            RetrievedChunk(
                text=rows[i][0],
                source=rows[i][1],
                doc_type="knowledge base",
                chunk_id=rows[i][3],
                score=float(scores[i]),
                topic=rows[i][2],
            )
            for i in top
        ]

    def _load(self, model):
        """The (rows, row-normalized matrix) for one model, cached."""
        if model in self._cache:
            return self._cache[model]
        records = self._conn.execute(
            "SELECT c.text, d.name, d.category, c.chunk_id, e.vector"
            " FROM embeddings e"
            " JOIN chunks c ON c.id = e.chunk_id"
            " JOIN documents d ON d.id = c.doc_id"
            " WHERE e.model = ?",
            (model,),
        ).fetchall()
        rows = [record[:4] for record in records]
        if records:
            matrix = np.stack(
                [np.frombuffer(record[4], dtype=np.float32) for record in records]
            )
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            matrix = matrix / np.where(norms == 0, 1.0, norms)
        else:
            matrix = np.empty((0, 0), dtype=np.float32)
        self._cache[model] = (rows, matrix)
        return rows, matrix

    def close(self) -> None:
        self._conn.close()
