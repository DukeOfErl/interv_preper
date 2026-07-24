from langchain_core.embeddings import DeterministicFakeEmbedding

from interview_prep.ingest import IngestedDocument
from interview_prep.retrieval import (
    DocumentIndex,
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
