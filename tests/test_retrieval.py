from types import SimpleNamespace

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from interview_prep.config import OPENROUTER_BASE_URL
from interview_prep.ingest import IngestedDocument
from interview_prep.privacy import PrivacyNotEnsuredError
from interview_prep.retrieval import (
    DocumentIndex,
    PrivateOpenAIEmbeddings,
    RetrievedChunk,
    fill_retrieved_context,
    format_context_block,
)


def make_index():
    return DocumentIndex(
        api_key="key",
        embedding_model="fake-model",
        embeddings=DeterministicFakeEmbedding(size=32),
    )


def make_doc(name="resume.txt", doc_type="resume", text="Python developer at Acme."):
    return IngestedDocument(name=name, doc_type=doc_type, text=text)


def test_add_and_retrieve_with_provenance():
    index = make_index()
    doc = make_doc()
    n = index.add_document(doc)

    assert n == 1
    assert doc.n_chunks == 1
    assert not index.is_empty

    chunks = index.retrieve("python experience", k=2)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.text == "Python developer at Acme."
    assert chunk.source == "resume.txt"
    assert chunk.doc_type == "resume"
    assert chunk.chunk_id == 0
    assert isinstance(chunk.score, float)


def test_long_document_is_split_into_chunks():
    index = make_index()
    doc = make_doc(text="A paragraph about work.\n\n" * 200)
    n = index.add_document(doc)
    assert n > 1
    assert doc.n_chunks == n


def test_re_adding_same_name_replaces_previous_version():
    index = make_index()
    index.add_document(make_doc(text="old content"))
    index.add_document(make_doc(text="new content"))

    chunks = index.retrieve("content", k=10)
    texts = [c.text for c in chunks]
    assert texts == ["new content"]


def test_remove_document_empties_index():
    index = make_index()
    index.add_document(make_doc())
    index.remove_document("resume.txt")

    assert index.is_empty
    assert index.retrieve("anything") == []


def test_set_doc_type_retags_chunks():
    index = make_index()
    index.add_document(make_doc(doc_type="other"))
    index.set_doc_type("resume.txt", "resume")

    assert index.retrieve("python")[0].doc_type == "resume"


def test_retrieve_on_empty_index_returns_nothing():
    assert make_index().retrieve("anything") == []


class _FakeEmbeddingsClient:
    """Records the kwargs of each /embeddings request; returns unit vectors."""

    def __init__(self):
        self.calls = []
        self.embeddings = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        n = len(kwargs["input"])
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[float(i), 1.0]) for i in range(n)]
        )


def _fake_embedder():
    client = _FakeEmbeddingsClient()
    return PrivateOpenAIEmbeddings(
        api_key="key",
        model="openai/some-embedder",
        base_url=OPENROUTER_BASE_URL,
        client=client,
    ), client


def test_document_embeddings_carry_the_provider_privacy_params():
    """Document chunks are the most sensitive payload the app sends.

    The privacy params are now an explicit ``extra_body`` argument on the raw SDK
    call rather than a kwarg forwarded by LangChain, so they cannot silently stop
    being sent when a dependency changes (ADR-0110).
    """
    embedder, client = _fake_embedder()
    embedder.embed_documents(["chunk one", "chunk two"])

    (call,) = client.calls
    assert call["extra_body"] == {"provider": {"data_collection": "deny"}}
    assert call["input"] == ["chunk one", "chunk two"]


def test_query_embeddings_carry_the_provider_privacy_params():
    # The query is the user's own message — same posture as the documents.
    embedder, client = _fake_embedder()
    embedder.embed_query("what did I do at Acme?")

    (call,) = client.calls
    assert call["extra_body"] == {"provider": {"data_collection": "deny"}}


def test_embeddings_are_batched():
    embedder, client = _fake_embedder()
    embedder.batch_size = 2
    vectors = embedder.embed_documents(["a", "b", "c"])

    assert [len(c["input"]) for c in client.calls] == [2, 1]
    assert len(vectors) == 3


def test_embedder_refuses_an_unensured_provider():
    # Fail closed: no embedder exists for a provider we can't vouch for, so
    # document chunks cannot be shipped there at all.
    with pytest.raises(PrivacyNotEnsuredError):
        PrivateOpenAIEmbeddings(
            api_key="key",
            model="openai/some-embedder",
            base_url="https://gateway.example/v1",
            client=_FakeEmbeddingsClient(),
        )


def test_document_index_refuses_an_unensured_provider():
    with pytest.raises(PrivacyNotEnsuredError):
        DocumentIndex(
            api_key="key",
            embedding_model="openai/some-embedder",
            base_url="https://gateway.example/v1",
        )


def make_chunk(**overrides):
    defaults = dict(
        text="Led a team of five.",
        source="resume.txt",
        doc_type="resume",
        chunk_id=0,
        score=0.9,
    )
    return RetrievedChunk(**{**defaults, **overrides})


def test_format_context_block_shape():
    # This asserts the context-block contract persona markdown is written
    # against: a header line plus one labeled section per chunk.
    block = format_context_block([make_chunk(), make_chunk(chunk_id=1, text="More.")])
    assert "retrieved from the candidate's uploaded documents" in block
    assert "### resume: resume.txt (part 1)\nLed a team of five." in block
    assert "### resume: resume.txt (part 2)\nMore." in block


def test_format_context_block_empty():
    assert format_context_block([]) == ""


def test_fill_retrieved_context_substitutes_only_its_placeholder():
    prompt = "Role: {target_role}\n\n{retrieved_context}\n\nEnd."
    filled = fill_retrieved_context(prompt, "THE BLOCK")
    assert "THE BLOCK" in filled
    assert "{retrieved_context}" not in filled
    # The other placeholders are the model's to fill conversationally — they
    # must survive untouched (str.format would have raised on them).
    assert "{target_role}" in filled


def test_fill_retrieved_context_empty_block_notes_no_documents():
    filled = fill_retrieved_context("{retrieved_context}", "")
    assert filled == "(no documents provided)"
