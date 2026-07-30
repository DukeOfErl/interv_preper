"""Chunking, embedding, indexing, and retrieval for uploaded documents.

Built on LangChain: ``OpenAIEmbeddings`` pointed at OpenRouter's
OpenAI-compatible ``/embeddings`` endpoint, and an ``InMemoryVectorStore``
(session-scoped, like the rest of the chat state — nothing touches disk).

``format_context_block`` + ``fill_retrieved_context`` define the context-block
contract: the one place that decides the shape of the text injected into a
grounding-aware prompt's ``{retrieved_context}`` slot. Persona markdown is
written against that shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import (
    CHUNK_OVERLAP_CHARS,
    CHUNK_SIZE_CHARS,
    OPENROUTER_BASE_URL,
    RETRIEVED_CONTEXT_PLACEHOLDER,
    TOP_K,
)
from .ingest import IngestedDocument


@dataclass(frozen=True)
class RetrievedChunk:
    """One retrieved chunk with its provenance and similarity score."""

    text: str
    source: str  # document name
    doc_type: str
    chunk_id: int
    score: float
    topic: str = ""  # why a web-research document was fetched; "" for uploads


class DocumentIndex:
    """Session-scoped vector index over the uploaded documents."""

    def __init__(self, api_key, embedding_model, embeddings=None):
        self.embedding_model = embedding_model
        # Allow injected embeddings (tests); otherwise embed via OpenRouter.
        # check_embedding_ctx_length uses tiktoken to pre-tokenize, which only
        # works for OpenAI-named models — disabled so any catalog model works.
        self._embeddings = embeddings or OpenAIEmbeddings(
            model=embedding_model,
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            check_embedding_ctx_length=False,
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
            {
                "source": doc.name,
                "doc_type": doc.doc_type,
                "chunk_id": i,
                "topic": getattr(doc, "topic", ""),
            }
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
                topic=document.metadata.get("topic", ""),
            )
            for document, score in results
        ]


def format_context_block(chunks: list[RetrievedChunk]) -> str:
    """Render retrieved chunks as the labeled block personas are written against.

    This is the single definition of the context-block contract: a header, then
    one ``### <doc_type>: <name> (part N)`` section per chunk — with the topic
    spliced in as ``### <doc_type> — <topic>: <name> (part N)`` for chunks that
    carry one (web research). Returns "" when nothing was retrieved.
    """
    if not chunks:
        return ""
    sections = [
        f"### {c.doc_type}{f' — {c.topic}' if c.topic else ''}: "
        f"{c.source} (part {c.chunk_id + 1})\n{c.text}"
        for c in chunks
    ]
    return (
        "The following excerpts were retrieved from the candidate's uploaded "
        "documents and from prior web research:\n\n" + "\n\n".join(sections)
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
