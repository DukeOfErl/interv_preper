"""Tools the interviewer LLM can call, and the dispatcher that runs them.

A *tool* is two things kept deliberately separate:

  1. a **schema** the model sees (name, description, JSON-Schema parameters) —
     prompt text, and the only thing that makes the model decide to call it, so
     the description does the real work (including the consent policy);
  2. a **local implementation** the model never sees, invoked by ``ToolBox.run``
     with the arguments the model produced.

The one tool here is ``web_research(query, topic)``. Its ``run`` flow is the
security-relevant part (see ``web_research.py`` for the dual-LLM rationale):

  cache → sub-completion research → FAIL-CLOSED guardrail scan of the bullets
  → index the raw excerpts (their own fail-closed scan) → return the bullets.

``run`` never raises. A model inventing arguments, a failing search, or a
flagged result all come back as error strings the model can read and recover
from — an exception here would abort the user's whole turn.
"""

from __future__ import annotations

import json
from datetime import date

from .config import WEB_TOPICS
from .ingest import IngestedDocument, should_ingest


class ToolBox:
    """The tools available for one turn, bound to that turn's live state.

    The model supplies only what the schemas declare (the judgment calls:
    what to search, why). Everything else the implementations need — the
    researcher, the guardrail, the vector index, the session cache, the UI
    hooks — is bound at construction by the caller.

    ``extra_cost`` accumulates the USD cost of sub-completions run by tools
    this turn, for the caller to add to the turn's spend (the main loop's
    usage accounting never sees these calls).
    """

    def __init__(
        self,
        researcher,
        guard,
        index,
        cache=None,
        on_warning=None,
        on_progress=None,
        on_document=None,
    ):
        self._researcher = researcher
        self._guard = guard
        self._index = index
        # Called with each successfully indexed IngestedDocument so the caller
        # can register it in the session's document list — otherwise the doc
        # is invisible to the Documents panel and lost when an embedding-model
        # switch rebuilds the index from that list.
        self._on_document = on_document or (lambda doc: None)
        # Session-scoped cache: normalized query -> bullets already returned.
        # A chatty model will otherwise re-search the same company mid-session.
        self._cache = cache if cache is not None else {}
        self._on_warning = on_warning or (lambda name, kind, reason="": None)
        self._on_progress = on_progress or (lambda message: None)
        self.extra_cost = 0.0
        # Citations from every research call this turn, for the sources panel.
        self.citations = []

    @property
    def specs(self):
        """The tool schemas, in the shape the chat-completions API expects."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "web_research",
                    "description": (
                        "Search the web and return cited fact bullets. Use it "
                        "for current information you cannot know: the target "
                        "company, up-to-date technologies for the role, "
                        "salary data, recent news. CONSENT POLICY: call this "
                        "only when the candidate has explicitly asked for web "
                        "research, or has just said yes to your offer to "
                        "research something. If you believe a search would "
                        "help but the candidate has not asked, offer it and "
                        "wait for their answer — never search preemptively. "
                        "Fuller excerpts from earlier searches may already "
                        "appear in your retrieved context; prefer those over "
                        "repeating a search. Today's date is "
                        f"{date.today():%B %d, %Y} — your own knowledge ends "
                        "earlier than that, so never write past years into "
                        "the query from memory; ask for what is current or "
                        "recent instead."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": (
                                    "A standalone web search query, e.g. "
                                    "'Acme Corp engineering culture and "
                                    "recent news'."
                                ),
                            },
                            "topic": {
                                "type": "string",
                                "enum": WEB_TOPICS,
                                "description": (
                                    "Why you are searching. Shown back to "
                                    "you later as the label on retrieved "
                                    "excerpts from this research."
                                ),
                            },
                        },
                        "required": ["query", "topic"],
                        "additionalProperties": False,
                    },
                },
            }
        ]

    def run(self, name, arguments):
        """Execute tool ``name`` with the model's ``arguments`` (a JSON string).

        Always returns a string — the tool message's content — including for
        failures. Raising here would abort the turn; an error string lets the
        model recover or tell the user.
        """
        try:
            parsed = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return "error: arguments were not valid JSON"
        if name != "web_research":
            return f"error: no tool named {name!r}"
        try:
            return self._web_research(**parsed)
        except TypeError as exc:
            # Models do invent arguments a schema never declared; splatting
            # them would raise out of the tool loop and kill the turn.
            return f"error: {exc}"

    def _web_research(self, query, topic="other"):
        query = (query or "").strip()
        if not query:
            return "error: query must be a non-empty string"
        if topic not in WEB_TOPICS:
            topic = "other"

        cached = self._cache.get(query.lower())
        if cached is not None:
            return cached

        self._on_progress(f"Researching the web: {query}…")
        result = self._researcher.research(query)
        if result.errored:
            self._on_warning(
                query,
                "web research",
                result.error_reason or "the search request failed",
            )
            return "error: web research is unavailable right now"
        if result.cost:
            self.extra_cost += result.cost

        # Fail CLOSED, like document ingestion and unlike the per-turn chat
        # guardrail: web content reaches the interviewer only after a
        # successful, clean scan of the digest.
        self._on_progress("Screening the research results…")
        verdict = self._guard.check_document(result.bullets, kind="web")
        if not should_ingest(verdict):
            reason = verdict.reason or "the safety scan could not complete"
            self._on_warning(query, "web research blocked", reason)
            return (
                "error: the research results were withheld by a safety screen; "
                "tell the candidate web research is unavailable for this query"
            )

        self.citations.extend(result.citations)

        # Tier two: index the verbatim excerpts so later turns can retrieve
        # fuller detail without a new search. Its own fail-closed scan; failure
        # degrades to bullets-only (which already passed their scan).
        if result.raw_text:
            self._on_progress("Indexing the research for later retrieval…")
            raw_verdict = self._guard.check_document(result.raw_text, kind="web")
            if should_ingest(raw_verdict):
                doc = IngestedDocument(
                    name=f"web: {query[:60]}",
                    doc_type="web search",
                    text=result.raw_text,
                    topic=topic,
                )
                try:
                    self._index.add_document(doc)
                except Exception as exc:
                    self._on_warning(doc.name, "error", str(exc))
                else:
                    self._on_document(doc)
            else:
                self._on_warning(
                    f"web: {query[:60]}",
                    "web research not indexed",
                    raw_verdict.reason or "the safety scan could not complete",
                )

        self._cache[query.lower()] = result.bullets
        return result.bullets
