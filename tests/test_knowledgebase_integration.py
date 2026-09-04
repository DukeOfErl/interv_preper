"""Opt-in semantic-retrieval tests against real OpenRouter embeddings.

Unlike ``test_knowledgebase.py`` (fake embeddings), these embed the real seeds
in ``knowledgebase/`` with the real default embedding model and assert that
retrieval routes representative coaching questions to the right documents.
Skipped unless ``OPENROUTER_API_KEY`` is set, so the default suite never
depends on the network or a key. Run with:  ``uv run pytest -m integration``.

The expensive step (embedding the whole corpus) happens once, in a
module-scoped fixture; each test then costs a single query embedding.
"""

import os

import pytest

from interview_prep.config import DEFAULT_EMBEDDING_MODEL, KNOWLEDGEBASE_DIR
from interview_prep.authorization import authorize
from interview_prep.knowledgebase import KnowledgeBase
from interview_prep.retrieval import format_context_block

# These tests spend real credit against a live model, so they must declare an
# authorized identity in as many words (R21.11) — exactly like every other
# caller of a paid client. They are the clearest case for the guard: an
# integration test IS a fourth caller, reaching the operation without the page.
INTEGRATION_IDENTITY = authorize(
    "integration-tests@example.com",
    table={"integration-tests@example.com": "dev"},
    email_verified=True,
)


pytestmark = pytest.mark.integration

API_KEY = os.getenv("OPENROUTER_API_KEY")
requires_key = pytest.mark.skipif(
    not API_KEY, reason="OPENROUTER_API_KEY not set; skipping live embedding test"
)


@pytest.fixture(scope="module")
def kb(tmp_path_factory):
    kb = KnowledgeBase(
        identity=INTEGRATION_IDENTITY,
        db_path=tmp_path_factory.mktemp("kb") / "knowledgebase.db", api_key=API_KEY
    )
    kb.sync(KNOWLEDGEBASE_DIR, DEFAULT_EMBEDDING_MODEL)
    yield kb
    kb.close()


def retrieve(kb, query, k=4):
    return kb.retrieve(query, DEFAULT_EMBEDDING_MODEL, k=k)


@requires_key
def test_legal_question_routes_to_legal_guidelines(kb):
    chunks = retrieve(kb, "Can I ask a candidate about their age or plans to have children?")
    top_sources = [c.source for c in chunks[:2]]
    assert "legal-guidelines-questions-to-avoid" in top_sources
    legal = next(c for c in chunks if c.source == "legal-guidelines-questions-to-avoid")
    assert legal.topic == "legal guidelines"


@requires_key
def test_bias_question_routes_to_bias_doc_and_renders_contract(kb):
    chunks = retrieve(kb, "How do I avoid the halo effect and affinity bias when scoring answers?")
    assert chunks[0].source == "bias-reduction"
    assert chunks[0].doc_type == "knowledge base"
    block = format_context_block(chunks[:1])
    assert "### knowledge base — bias reduction: bias-reduction" in block


@requires_key
def test_tagged_question_bank_surfaces_with_its_tags_line(kb):
    chunks = retrieve(
        kb, "behavioral questions probing conflict resolution and ownership"
    )
    behavioral = [c for c in chunks if c.source == "question-bank-behavioral"]
    assert behavioral, f"expected question-bank-behavioral in top-k, got {[c.source for c in chunks]}"
    # The tags line is part of the stored chunk text — it reached the embedding
    # and reaches the interviewer.
    assert behavioral[0].text.startswith("[kind: behavioral; roles: all]")
