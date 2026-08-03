import sqlite3

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from interview_prep.knowledgebase import KnowledgeBase, _tags_line, parse_seed

SEED = """---
category: question bank
tags:
  roles: [backend engineer, data scientist]
  seniority: senior
---

Ask about a production incident the candidate handled end to end.
"""

PLAIN_SEED = "Prefer behavioral questions over hypotheticals.\n"


class CountingEmbeddings:
    """Deterministic fake that counts how many texts were embedded."""

    def __init__(self, size=32):
        self._inner = DeterministicFakeEmbedding(size=size)
        self.documents_embedded = 0

    def embed_documents(self, texts):
        self.documents_embedded += len(texts)
        return self._inner.embed_documents(texts)

    def embed_query(self, text):
        return self._inner.embed_query(text)


@pytest.fixture
def seed_dir(tmp_path):
    seeds = tmp_path / "knowledgebase"
    seeds.mkdir()
    (seeds / "question-bank.md").write_text(SEED, encoding="utf-8")
    (seeds / "best-practices.md").write_text(PLAIN_SEED, encoding="utf-8")
    return seeds


def make_kb(tmp_path, factories=None):
    """A KnowledgeBase on a temp DB whose per-model embedders count calls."""
    embedders = factories if factories is not None else {}

    def factory(model):
        return embedders.setdefault(model, CountingEmbeddings())

    kb = KnowledgeBase(
        db_path=tmp_path / "data" / "kb.db", api_key="key", embeddings_factory=factory
    )
    return kb, embedders


def test_parse_seed_reads_frontmatter():
    category, tags, body = parse_seed(SEED)
    assert category == "question bank"
    assert tags == {"roles": ["backend engineer", "data scientist"], "seniority": "senior"}
    assert body.startswith("Ask about a production incident")


def test_parse_seed_defaults_without_frontmatter():
    category, tags, body = parse_seed(PLAIN_SEED)
    assert category == "general"
    assert tags == {}
    assert body == PLAIN_SEED.strip()


def test_sync_builds_and_retrieves_with_provenance(tmp_path, seed_dir):
    kb, _ = make_kb(tmp_path)
    report = kb.sync(seed_dir, "model-a")

    assert report.added == 2
    assert report.changed
    assert not kb.is_empty

    chunks = kb.retrieve("production incident", "model-a", k=10)
    assert chunks
    chunk = next(c for c in chunks if c.source == "question-bank")
    assert chunk.doc_type == "knowledge base"
    assert chunk.topic == "question bank"
    # The tags line travels inside the chunk text (into embedding + context).
    assert chunk.text.startswith(
        "[roles: backend engineer, data scientist; seniority: senior]"
    )
    assert isinstance(chunk.score, float)


def test_resync_unchanged_is_a_noop(tmp_path, seed_dir):
    kb, embedders = make_kb(tmp_path)
    kb.sync(seed_dir, "model-a")
    first_calls = embedders["model-a"].documents_embedded

    report = kb.sync(seed_dir, "model-a")
    assert not report.changed
    assert embedders["model-a"].documents_embedded == first_calls


def test_edited_seed_reembeds_only_that_file(tmp_path, seed_dir):
    kb, embedders = make_kb(tmp_path)
    kb.sync(seed_dir, "model-a")
    baseline = embedders["model-a"].documents_embedded

    (seed_dir / "best-practices.md").write_text("Updated advice.", encoding="utf-8")
    report = kb.sync(seed_dir, "model-a")

    assert report.updated == 1
    assert report.added == report.removed == 0
    # Only the edited file's chunks were re-embedded, not the whole corpus.
    assert embedders["model-a"].documents_embedded == baseline + 1
    texts = [c.text for c in kb.retrieve("advice", "model-a", k=10)]
    assert "Updated advice." in texts


def test_deleted_seed_is_removed(tmp_path, seed_dir):
    kb, _ = make_kb(tmp_path)
    kb.sync(seed_dir, "model-a")

    (seed_dir / "question-bank.md").unlink()
    report = kb.sync(seed_dir, "model-a")

    assert report.removed == 1
    assert all(d.name != "question-bank" for d in kb.documents())


def test_model_switch_backfills_once_and_caches(tmp_path, seed_dir):
    kb, embedders = make_kb(tmp_path)
    kb.sync(seed_dir, "model-a")

    # First use of model-b backfills every chunk under that model.
    report = kb.sync(seed_dir, "model-b")
    assert report.chunks_embedded > 0
    assert embedders["model-b"].documents_embedded == report.chunks_embedded

    # Switching back to model-a re-embeds nothing under either model.
    report = kb.sync(seed_dir, "model-a")
    assert not report.changed
    assert kb.retrieve("questions", "model-b")  # model-b vectors kept


def test_persists_across_reopen_without_reembedding(tmp_path, seed_dir):
    kb, _ = make_kb(tmp_path)
    kb.sync(seed_dir, "model-a")
    kb.close()

    reopened, embedders = make_kb(tmp_path)
    assert not reopened.is_empty
    report = reopened.sync(seed_dir, "model-a")
    assert not report.changed
    # Nothing to embed → the embeddings client was never even constructed.
    assert embedders == {}
    assert reopened.retrieve("behavioral questions", "model-a")


def test_missing_seed_dir_yields_empty_kb(tmp_path):
    kb, _ = make_kb(tmp_path)
    report = kb.sync(tmp_path / "does-not-exist", "model-a")
    assert not report.changed
    assert kb.is_empty
    assert kb.retrieve("anything", "model-a") == []


def test_documents_lists_metadata(tmp_path, seed_dir):
    kb, _ = make_kb(tmp_path)
    kb.sync(seed_dir, "model-a")
    docs = {d.name: d for d in kb.documents()}
    assert docs["question-bank"].category == "question bank"
    assert docs["question-bank"].tags["seniority"] == "senior"
    assert docs["best-practices"].n_chunks >= 1


# --- Malformed seeds --------------------------------------------------------------


def test_broken_yaml_frontmatter_fails_open_per_file(tmp_path, seed_dir):
    (seed_dir / "broken.md").write_text(
        "---\ntags: [unclosed\n---\n\nStill useful advice.", encoding="utf-8"
    )
    kb, _ = make_kb(tmp_path)
    report = kb.sync(seed_dir, "model-a")  # must not raise

    assert report.added == 3
    docs = {d.name: d for d in kb.documents()}
    assert docs["broken"].category == "general"
    assert docs["broken"].tags == {}
    texts = [c.text for c in kb.retrieve("useful advice", "model-a", k=20)]
    assert any("Still useful advice." in t for t in texts)


def test_frontmatter_only_and_empty_files_yield_zero_chunks(tmp_path):
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    (seeds / "header-only.md").write_text("---\ncategory: x\n---\n", encoding="utf-8")
    (seeds / "empty.md").write_text("", encoding="utf-8")
    kb, _ = make_kb(tmp_path)
    report = kb.sync(seeds, "model-a")

    assert report.added == 2
    assert {d.n_chunks for d in kb.documents()} == {0}
    assert kb.is_empty  # emptiness is about chunks, not document rows
    assert kb.retrieve("anything", "model-a") == []


@pytest.mark.parametrize("frontmatter", ["- a\n- b", "42"])
def test_non_dict_frontmatter_keeps_whole_file_as_body(frontmatter):
    raw = f"---\n{frontmatter}\n---\nBody text."
    category, tags, body = parse_seed(raw)
    assert (category, tags) == ("general", {})
    # Not metadata → nothing is stripped; dropping the pseudo-frontmatter
    # would silently lose content (see the leading-rule test below).
    assert body == raw


def test_leading_horizontal_rule_loses_no_content():
    raw = "---\n\nIntro paragraph.\n\n---\n\nOutro."
    category, tags, body = parse_seed(raw)
    assert (category, tags) == ("general", {})
    assert "Intro paragraph." in body and "Outro." in body


def test_explicit_empty_category_falls_back_to_default():
    category, _, body = parse_seed("---\ncategory:\n---\nBody.")
    assert category == "general"  # not the literal string "None"
    assert body == "Body."


def test_tags_as_list_dropped_and_odd_values_stringified():
    _, tags, _ = parse_seed("---\ntags: [a, b]\n---\nBody.")
    assert tags == {}
    assert _tags_line({"count": 3, "note": None}) == "[count: 3; note: None]\n"


def test_thematic_break_mid_body_is_not_frontmatter():
    category, tags, body = parse_seed("Intro.\n\n---\n\nOutro.")
    assert category == "general"
    assert body == "Intro.\n\n---\n\nOutro."


def test_unicode_content_round_trips(tmp_path):
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    (seeds / "hebrew.md").write_text(
        "---\ncategory: שאלות\ntags:\n  roles: [מהנדס תוכנה 🚀]\n---\n"
        "שאלות ראיון בעברית 🎯",
        encoding="utf-8",
    )
    kb, _ = make_kb(tmp_path)
    kb.sync(seeds, "model-a")

    doc = kb.documents()[0]
    assert doc.tags == {"roles": ["מהנדס תוכנה 🚀"]}
    chunk = kb.retrieve("שאלות ראיון", "model-a")[0]
    assert "שאלות ראיון בעברית 🎯" in chunk.text
    assert chunk.topic == "שאלות"


# --- Filesystem -------------------------------------------------------------------


def test_rename_is_remove_plus_add_and_reembeds(tmp_path, seed_dir):
    kb, embedders = make_kb(tmp_path)
    kb.sync(seed_dir, "model-a")
    baseline = embedders["model-a"].documents_embedded

    (seed_dir / "best-practices.md").rename(seed_dir / "renamed.md")
    report = kb.sync(seed_dir, "model-a")

    assert (report.added, report.removed, report.updated) == (1, 1, 0)
    # Identity is the file name, so identical content is still re-embedded —
    # the documented cost of per-file (not per-chunk) reconciliation.
    assert embedders["model-a"].documents_embedded > baseline


def test_only_top_level_md_files_are_seeds(tmp_path, seed_dir):
    (seed_dir / "notes.txt").write_text("not markdown", encoding="utf-8")
    (seed_dir / "nested").mkdir()
    (seed_dir / "nested" / "inner.md").write_text("nested", encoding="utf-8")
    kb, _ = make_kb(tmp_path)
    report = kb.sync(seed_dir, "model-a")

    assert report.added == 2  # only the two top-level .md seeds
    assert {d.name for d in kb.documents()} == {"question-bank", "best-practices"}


# --- DB-level ---------------------------------------------------------------------


def test_schema_version_mismatch_drops_and_rebuilds(tmp_path, seed_dir):
    kb, _ = make_kb(tmp_path)
    kb.sync(seed_dir, "model-a")
    kb.close()
    with sqlite3.connect(tmp_path / "data" / "kb.db") as conn:
        conn.execute("PRAGMA user_version = 999")

    reopened, embedders = make_kb(tmp_path)
    assert reopened.is_empty
    report = reopened.sync(seed_dir, "model-a")
    assert report.added == 2
    assert embedders["model-a"].documents_embedded > 0


def test_lock_error_does_not_delete_healthy_db(tmp_path, seed_dir, monkeypatch):
    kb, _ = make_kb(tmp_path)
    kb.sync(seed_dir, "model-a")
    kb.close()
    db_path = tmp_path / "data" / "kb.db"

    # "database is locked" is an OperationalError — a DatabaseError subclass —
    # raised when a sibling session holds the lock. It must propagate, NOT
    # trigger the corruption self-heal that unlinks the file.
    def locked(path):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(KnowledgeBase, "_open", staticmethod(locked))
    with pytest.raises(sqlite3.OperationalError):
        make_kb(tmp_path)
    monkeypatch.undo()

    assert db_path.exists()
    reopened, _ = make_kb(tmp_path)  # data survived intact
    assert not reopened.is_empty


def test_non_utf8_seed_degrades_that_file_only(tmp_path, seed_dir):
    # 0x92 is a Windows-1252 curly quote — invalid UTF-8, a common paste slip.
    (seed_dir / "pasted.md").write_bytes(b"Don\x92t ask leading questions.")
    kb, _ = make_kb(tmp_path)
    report = kb.sync(seed_dir, "model-a")  # must not raise

    assert report.added == 3
    texts = [c.text for c in kb.retrieve("leading questions", "model-a", k=20)]
    assert any("ask leading questions." in t for t in texts)


def test_corrupt_db_file_is_discarded_and_rebuilt(tmp_path, seed_dir):
    db_path = tmp_path / "data" / "kb.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"this is not a sqlite database at all")

    kb, _ = make_kb(tmp_path)  # must not raise
    report = kb.sync(seed_dir, "model-a")
    assert report.added == 2
    assert kb.retrieve("questions", "model-a")


def test_cascade_delete_leaves_no_orphans_across_models(tmp_path, seed_dir):
    kb, _ = make_kb(tmp_path)
    kb.sync(seed_dir, "model-a")
    kb.sync(seed_dir, "model-b")

    (seed_dir / "question-bank.md").unlink()
    kb.sync(seed_dir, "model-a")

    orphans = kb._conn.execute(
        "SELECT COUNT(*) FROM embeddings e"
        " WHERE NOT EXISTS (SELECT 1 FROM chunks c WHERE c.id = e.chunk_id)"
    ).fetchone()[0]
    assert orphans == 0
    # The surviving doc's model-b vectors were untouched: nothing to backfill.
    assert kb.sync(seed_dir, "model-b").chunks_embedded == 0
    assert {c.source for c in kb.retrieve("advice", "model-b")} == {"best-practices"}


def test_failed_embedding_mid_sync_persists_nothing(tmp_path, seed_dir):
    class FailingEmbeddings:
        def embed_documents(self, texts):
            raise RuntimeError("embeddings endpoint down")

    kb = KnowledgeBase(
        db_path=tmp_path / "data" / "kb.db",
        api_key="key",
        embeddings_factory=lambda model: FailingEmbeddings(),
    )
    with pytest.raises(RuntimeError):
        kb.sync(seed_dir, "model-a")
    # The failed sync rolled back — no write transaction (RESERVED lock) may
    # linger on the long-lived connection to block sibling sessions.
    assert not kb._conn.in_transaction
    kb.close()

    # Nothing was committed — a fresh open sees an empty KB and a clean sync
    # (with a working embedder) redoes all the work.
    reopened, _ = make_kb(tmp_path)
    assert reopened.is_empty
    assert reopened.sync(seed_dir, "model-a").added == 2


def test_second_instance_serves_stale_cache_until_resync(tmp_path, seed_dir):
    kb_a, _ = make_kb(tmp_path)
    kb_a.sync(seed_dir, "model-a")
    assert kb_a.retrieve("content", "model-a")  # warm kb_a's cache

    kb_b, _ = make_kb(tmp_path)
    (seed_dir / "best-practices.md").write_text("Rewritten advice.", encoding="utf-8")
    kb_b.sync(seed_dir, "model-a")

    # Documented limitation: one KnowledgeBase object per session — a sibling
    # instance's write is invisible to a warm cache until this instance syncs.
    stale = [c.text for c in kb_a.retrieve("advice", "model-a", k=10)]
    assert "Rewritten advice." not in stale
    kb_a.sync(seed_dir, "model-a")
    fresh = [c.text for c in kb_a.retrieve("advice", "model-a", k=10)]
    assert "Rewritten advice." in fresh


def test_probes_need_no_db_after_sync(tmp_path, seed_dir):
    kb, _ = make_kb(tmp_path)
    kb.sync(seed_dir, "model-a")
    kb.close()  # sever the DB — the rerun-path probes must still answer

    assert not kb.is_empty
    assert {d.name for d in kb.documents()} == {"question-bank", "best-practices"}


# --- Retrieval & API misbehavior --------------------------------------------------


def test_k_larger_than_corpus_returns_all_sorted(tmp_path, seed_dir):
    kb, _ = make_kb(tmp_path)
    kb.sync(seed_dir, "model-a")
    total = sum(d.n_chunks for d in kb.documents())

    chunks = kb.retrieve("anything", "model-a", k=999)
    assert len(chunks) == total
    scores = [c.score for c in chunks]
    assert scores == sorted(scores, reverse=True)


def test_zero_vectors_yield_finite_scores(tmp_path, seed_dir):
    class ZeroEmbeddings:
        def embed_documents(self, texts):
            return [[0.0] * 8 for _ in texts]

        def embed_query(self, text):
            return [0.0] * 8

    kb = KnowledgeBase(
        db_path=tmp_path / "data" / "kb.db",
        api_key="key",
        embeddings_factory=lambda model: ZeroEmbeddings(),
    )
    kb.sync(seed_dir, "model-a")
    chunks = kb.retrieve("anything", "model-a")
    assert chunks
    assert all(c.score == 0.0 for c in chunks)  # guarded norms, no NaN


def test_embedding_count_mismatch_raises_instead_of_truncating(tmp_path, seed_dir):
    class TruncatingEmbeddings(CountingEmbeddings):
        def embed_documents(self, texts):
            return super().embed_documents(texts)[:-1]  # one vector short

    kb = KnowledgeBase(
        db_path=tmp_path / "data" / "kb.db",
        api_key="key",
        embeddings_factory=lambda model: TruncatingEmbeddings(),
    )
    with pytest.raises(ValueError):
        kb.sync(seed_dir, "model-a")


def test_edit_synced_under_one_model_hides_doc_from_other_until_resync(
    tmp_path, seed_dir
):
    kb, _ = make_kb(tmp_path)
    kb.sync(seed_dir, "model-a")
    kb.sync(seed_dir, "model-b")

    (seed_dir / "best-practices.md").write_text("Fresh guidance.", encoding="utf-8")
    kb.sync(seed_dir, "model-a")

    # The edit cascaded away the old chunks (and their model-b vectors); the
    # new chunks are embedded only under model-a so far. This is why chat_bot
    # re-syncs on every embedding-model switch.
    assert {c.source for c in kb.retrieve("guidance", "model-b", k=10)} == {
        "question-bank"
    }
    kb.sync(seed_dir, "model-b")
    assert "best-practices" in {
        c.source for c in kb.retrieve("guidance", "model-b", k=10)
    }
