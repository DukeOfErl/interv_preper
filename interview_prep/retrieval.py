"""Chunking, embedding, indexing, and retrieval for uploaded documents.

Uses LangChain's ``InMemoryVectorStore`` and text splitter (session-scoped, like
the rest of the chat state — nothing touches disk), but embeds through
:class:`PrivateOpenAIEmbeddings`, a small adapter over the raw OpenAI SDK rather
than ``langchain_openai.OpenAIEmbeddings``. Document chunks are the most sensitive
payload the app sends, and ``OpenAIEmbeddings`` exposes no way to attach the
provider's privacy params except via ``model_kwargs`` forwarding — undocumented
behavior that could stop working on a dependency upgrade with *no runtime signal*.
See ADR-0110: for an irreversible disclosure, an explicit argument beats a tested
escape hatch.

``format_context_block`` + ``fill_retrieved_context`` define the context-block
contract: the one place that decides the shape of the text injected into a
grounding-aware prompt's ``{retrieved_context}`` slot. Persona markdown is
written against that shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openai import OpenAI

from .config import (
    CHUNK_OVERLAP_CHARS,
    CHUNK_SIZE_CHARS,
    EMBEDDING_BATCH_SIZE,
    OPENROUTER_BASE_URL,
    RETRIEVED_CONTEXT_PLACEHOLDER,
    TOP_K,
)
from .ingest import IngestedDocument
from .privacy import require_embedding_privacy_extra_body


@dataclass(frozen=True)
class RetrievedChunk:
    """One retrieved chunk with its provenance and similarity score."""

    text: str
    source: str  # document name
    doc_type: str
    chunk_id: int
    score: float


class PrivateOpenAIEmbeddings(Embeddings):
    """Embeddings over the raw OpenAI SDK, with the privacy params explicit.

    Implements the two abstract methods ``InMemoryVectorStore`` actually calls on
    a sync path (``add_texts`` → ``embed_documents``,
    ``similarity_search_with_score`` → ``embed_query``); the async variants come
    from the base class and no sync path uses them.

    Constructing this **raises** for a provider whose data-usage policy is
    unknown, so there is no object capable of shipping document chunks there.
    """

    def __init__(self, api_key, model, base_url, client=None, batch_size=None):
        self.model = model
        self.base_url = base_url
        self.batch_size = batch_size or EMBEDDING_BATCH_SIZE
        # Fail closed before the client exists. Narrowed to the keys /embeddings
        # accepts — some privacy params are chat-only and would be rejected.
        self.privacy_extra_body = require_embedding_privacy_extra_body(base_url)
        # Allow an injected client (tests); otherwise build the real one.
        self._client = client or OpenAI(base_url=base_url, api_key=api_key)

    def embed_documents(self, texts):
        vectors = []
        for start in range(0, len(texts), self.batch_size):
            response = self._client.embeddings.create(
                model=self.model,
                input=texts[start : start + self.batch_size],
                # An explicit argument, not a forwarded kwarg: it cannot silently
                # stop being sent when a dependency changes.
                extra_body=dict(self.privacy_extra_body),
            )
            vectors.extend(item.embedding for item in response.data)
        return vectors

    def embed_query(self, text):
        return self.embed_documents([text])[0]


class DocumentIndex:
    """Session-scoped vector index over the uploaded documents."""

    def __init__(self, api_key, embedding_model, embeddings=None, base_url=None):
        self.embedding_model = embedding_model
        base_url = base_url or OPENROUTER_BASE_URL
        # Allow injected embeddings (tests); otherwise embed via the provider.
        self._embeddings = embeddings or PrivateOpenAIEmbeddings(
            api_key=api_key, model=embedding_model, base_url=base_url
        )
        self._store = InMemoryVectorStore(self._embeddings)
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE_CHARS, chunk_overlap=CHUNK_OVERLAP_CHARS
        )
        self._ids_by_doc: dict[str, list[str]] = {}

    @property
    def is_empty(self) -> bool:
        return not self._ids_by_doc

    def add_document(self, doc: IngestedDocument) -> int:
        """Chunk, embed, and index one document; returns the chunk count.

        Re-adding a document with the same name replaces the previous version.
        """
        self.remove_document(doc.name)
        chunks = self._splitter.split_text(doc.text)
        metadatas = [
            {"source": doc.name, "doc_type": doc.doc_type, "chunk_id": i}
            for i in range(len(chunks))
        ]
        ids = self._store.add_texts(chunks, metadatas=metadatas)
        self._ids_by_doc[doc.name] = list(ids)
        doc.n_chunks = len(chunks)
        return len(chunks)

    def remove_document(self, name: str) -> None:
        ids = self._ids_by_doc.pop(name, None)
        if ids:
            self._store.delete(ids)

    def set_doc_type(self, name: str, doc_type: str) -> None:
        """Re-tag a document's chunks in place (no re-embedding needed)."""
        for chunk_id in self._ids_by_doc.get(name, []):
            record = self._store.store.get(chunk_id)
            if record:
                record["metadata"]["doc_type"] = doc_type

    def retrieve(self, query: str, k: int = TOP_K) -> list[RetrievedChunk]:
        if self.is_empty:
            return []
        results = self._store.similarity_search_with_score(query, k=k)
        return [
            RetrievedChunk(
                text=document.page_content,
                source=document.metadata.get("source", "?"),
                doc_type=document.metadata.get("doc_type", "other"),
                chunk_id=int(document.metadata.get("chunk_id", 0)),
                score=float(score),
            )
            for document, score in results
        ]


def format_context_block(chunks: list[RetrievedChunk]) -> str:
    """Render retrieved chunks as the labeled block personas are written against.

    This is the single definition of the context-block contract: a header, then
    one ``### <doc_type>: <name> (part N)`` section per chunk. Returns "" when
    nothing was retrieved.
    """
    if not chunks:
        return ""
    sections = [
        f"### {c.doc_type}: {c.source} (part {c.chunk_id + 1})\n{c.text}"
        for c in chunks
    ]
    return (
        "The following excerpts were retrieved from the candidate's uploaded "
        "documents:\n\n" + "\n\n".join(sections)
    )


def fill_retrieved_context(system_prompt: str, block: str) -> str:
    """Substitute the context block into the prompt's placeholder slot.

    Uses ``str.replace`` deliberately — the prompt markdown contains other
    ``{placeholders}`` the model fills conversationally, so ``str.format``
    would raise on them.
    """
    return system_prompt.replace(
        RETRIEVED_CONTEXT_PLACEHOLDER, block or "(no documents provided)"
    )
