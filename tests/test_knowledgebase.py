import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from interview_prep.knowledgebase import KnowledgeBase, parse_seed

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
