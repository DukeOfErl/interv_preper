"""Web research sub-completion: search the web and return a citable digest.

There is no bare search endpoint on OpenRouter — its ``web`` plugin only exists
as a modifier on chat completions. So research runs as a SUB-COMPLETION: one
non-streamed call on a cheap model with the plugin attached. OpenRouter runs the
search (Exa by default), injects the page excerpts, and the model's only job is
extraction — fact bullets, each citing its source.

This shape is the dual-LLM (quarantined/privileged) pattern: the sub-call is the
quarantined model that reads raw, untrusted web pages; the interviewer — the
privileged model holding the conversation and the tools — sees only this
module's output, which the caller additionally screens before use.

Two outputs, two tiers: ``bullets`` go back to the interviewer as the tool
result; ``raw_text`` (the Exa excerpts, verbatim) is what the caller indexes for
RAG so later turns can retrieve fuller detail without a new search.

Citation URLs in the bullets are validated in code against the annotations the
provider returned — the sub-model's output is untrusted too, and small models
mangle (or invent) URLs when asked to copy them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from openai import OpenAI

from .config import (
    OPENROUTER_BASE_URL,
    PROMPTS_DIR,
    WEB_RESEARCH_MODEL,
    WEB_RESEARCH_PROMPT_FILE,
    WEB_SEARCH_ENGINE,
    WEB_SEARCH_MAX_RESULTS,
)

_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\((\S+?)(?:\s+\"[^\"]*\")?\)")


@dataclass(frozen=True)
class Citation:
    """One web source from the provider's ``url_citation`` annotations."""

    url: str
    title: str = ""
    content: str = ""  # the excerpt the search engine extracted from the page


@dataclass(frozen=True)
class ResearchResult:
    """Outcome of one research call.

    ``errored`` is True when the call failed outright; ``bullets`` and
    ``raw_text`` are then empty, ``error_reason`` says why (for the warnings
    log — a schema change or outage should be diagnosable from there), and the
    caller should degrade gracefully.
    """

    bullets: str = ""
    citations: list[Citation] = field(default_factory=list)
    raw_text: str = ""
    cost: float | None = None
    errored: bool = False
    error_reason: str = ""


def load_web_research_prompt(prompt_dir=PROMPTS_DIR, file_name=WEB_RESEARCH_PROMPT_FILE):
    """Read the extractor's instructions from the markdown prompt file."""
    path = prompt_dir / file_name
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _parse_citations(message) -> list[Citation]:
    """Pull ``url_citation`` annotations off the response message, defensively.

    Annotations are an OpenRouter standardization on top of the OpenAI schema;
    shape and presence vary by provider, so anything missing yields an empty
    list rather than an error.
    """
    annotations = getattr(message, "annotations", None) or []
    citations = []
    for annotation in annotations:
        cite = getattr(annotation, "url_citation", None)
        if cite is None and isinstance(annotation, dict):
            cite = annotation.get("url_citation")
        if cite is None:
            continue
        get = (
            cite.get
            if isinstance(cite, dict)
            else lambda key, default="", _c=cite: getattr(_c, key, default)
        )
        url = str(get("url", "") or "")
        if not url:
            continue
        citations.append(
            Citation(
                url=url,
                title=str(get("title", "") or ""),
                content=str(get("content", "") or ""),
            )
        )
    return citations


def _validate_links(bullets: str, citations: list[Citation]) -> str:
    """Strip markdown links whose URL is not among the returned citations.

    The extractor is asked to copy citation URLs into its bullets, but its
    output is untrusted: small models mangle URLs, and a hostile page could
    steer the summary toward a crafted link. Any link not backed by an
    annotation is reduced to its plain text.
    """
    known = {c.url for c in citations}

    def replace(match):
        text, url = match.group(1), match.group(2)
        return match.group(0) if url in known else text

    return _MARKDOWN_LINK.sub(replace, bullets)


def _format_raw_text(citations: list[Citation]) -> str:
    """Join the citation excerpts, verbatim, under per-source headers.

    This is tier two: the text later chunked and indexed for retrieval. It is
    deliberately NOT summarized — summarizing what then gets chunked and
    retrieved would be doubly lossy; its safety layer is the caller's
    fail-closed guardrail scan, not compression.
    """
    sections = []
    for cite in citations:
        if not cite.content:
            continue
        title = cite.title or cite.url
        sections.append(f"## {title}\nSource: {cite.url}\n\n{cite.content}")
    return "\n\n".join(sections)


def _extract_cost(usage) -> float | None:
    """OpenRouter's actual USD cost off the usage object (an SDK-extra field)."""
    cost = getattr(usage, "cost", None)
    if cost is None and getattr(usage, "model_extra", None):
        cost = usage.model_extra.get("cost")
    try:
        return float(cost) if cost is not None else None
    except (TypeError, ValueError):
        return None


class WebResearcher:
    """One-shot web search + extraction over OpenRouter's web plugin."""

    def __init__(
        self,
        api_key,
        model=WEB_RESEARCH_MODEL,
        instructions=None,
        base_url=OPENROUTER_BASE_URL,
        client=None,
        engine=WEB_SEARCH_ENGINE,
        max_results=WEB_SEARCH_MAX_RESULTS,
    ):
        self.model = model
        self.engine = engine
        self.max_results = max_results
        self.instructions = (
            instructions if instructions is not None else load_web_research_prompt()
        )
        # Allow an injected client (tests); otherwise build the real one.
        self._client = client or OpenAI(base_url=base_url, api_key=api_key)

    def research(self, query) -> ResearchResult:
        """Search the web for ``query`` and return the screened-ready digest.

        Fails soft: any exception returns ``ResearchResult(errored=True)`` so
        the tool layer can hand the model a readable error string instead of
        aborting the turn.
        """
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.instructions},
                    {"role": "user", "content": query},
                ],
                extra_body={
                    # Explicit plugin config, NOT the ":online" model suffix —
                    # the suffix is equivalent to routing via openrouter/auto,
                    # which would surrender the model choice.
                    "plugins": [
                        {
                            "id": "web",
                            "engine": self.engine,
                            "max_results": self.max_results,
                        }
                    ],
                    "usage": {"include": True},
                },
            )
            message = response.choices[0].message
            bullets = (message.content or "").strip()
            citations = _parse_citations(message)
            cost = _extract_cost(getattr(response, "usage", None))
        except Exception as exc:
            return ResearchResult(
                errored=True, error_reason=f"{type(exc).__name__}: {exc}"
            )
        if not bullets:
            return ResearchResult(
                errored=True,
                error_reason="the model returned an empty reply",
                citations=citations,
                cost=cost,
            )
        return ResearchResult(
            bullets=_validate_links(bullets, citations),
            citations=citations,
            raw_text=_format_raw_text(citations),
            cost=cost,
        )
