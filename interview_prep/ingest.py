"""Parsing uploaded documents into plain text.

This module only turns an uploaded file (name + bytes) into text and metadata.
Screening (guardrail), chunking, and embedding happen elsewhere: screening is
orchestrated by the entry point, chunking/embedding live in ``retrieval``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from .context import estimate_text_tokens


class DocumentParseError(Exception):
    """Raised when an uploaded file cannot be turned into usable text."""


@dataclass
class IngestedDocument:
    """A parsed upload, ready to be screened and indexed.

    ``doc_type`` is mutable: the user can correct the inferred type from the
    ingestion panel. ``n_chunks`` is filled in once the document is indexed.
    """

    name: str
    doc_type: str
    text: str
    n_chunks: int = 0

    @property
    def token_estimate(self) -> int:
        return estimate_text_tokens(self.text)


def parse_document(name: str, data: bytes) -> str:
    """Extract plain text from an uploaded file, dispatching on its extension.

    Raises ``DocumentParseError`` on an unsupported extension, unreadable
    bytes, or a document with no extractable text.
    """
    suffix = Path(name).suffix.lower()
    try:
        if suffix == ".pdf":
            text = _parse_pdf(data)
        elif suffix == ".docx":
            text = _parse_docx(data)
        elif suffix in (".txt", ".md"):
            text = data.decode("utf-8", errors="replace")
        else:
            raise DocumentParseError(f"unsupported file type: {suffix or 'none'}")
    except DocumentParseError:
        raise
    except Exception as exc:
        raise DocumentParseError(f"could not read {name}: {exc}") from exc

    text = text.strip()
    if not text:
        raise DocumentParseError(f"no text could be extracted from {name}")
    return text


def _parse_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _parse_docx(data: bytes) -> str:
    from docx import Document

    document = Document(BytesIO(data))
    # document.paragraphs only yields top-level paragraphs — text inside table
    # cells and text boxes (how most CV templates lay out content) is missed,
    # which would hide it from both the guardrail scan and the index. Walk every
    # w:p in the body in document order instead. Headers/footers are separate
    # document parts and are still not included.
    paragraphs = []
    for p in document.element.xpath("//w:p"):
        text = "".join(t.text or "" for t in p.xpath(".//w:t"))
        if text.strip():
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def infer_doc_type(filename: str) -> str:
    """Guess the document type from its filename (user-correctable in the UI)."""
    words = set(re.split(r"[^a-z0-9]+", Path(filename).stem.lower()))
    if words & {"resume", "cv"}:
        return "resume"
    if "cover" in words:
        return "cover letter"
    if words & {"job", "ad", "posting", "vacancy", "jd"}:
        return "job ad"
    return "other"


def should_ingest(verdict) -> bool:
    """Fail-closed ingestion policy for a guardrail verdict on document text.

    Unlike the per-turn chat guardrail (which fails open), a document is only
    ingested after a successful, clean scan — a flagged document *or* a failed
    scan both reject it. The chat itself continues without the document.
    """
    return verdict.allowed and not verdict.errored
