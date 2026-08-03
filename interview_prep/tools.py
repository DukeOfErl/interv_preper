"""Tools the interviewer LLM can call, and the dispatcher that runs them.

A *tool* is two things kept deliberately separate:

  1. a **schema** the model sees (name, description, JSON-Schema parameters) —
     prompt text, and the only thing that makes the model decide to call it, so
     the description does the real work (including the consent policy);
  2. a **local implementation** the model never sees, invoked by ``ToolBox.run``
     with the arguments the model produced.

Two tools, with opposite invocation policies (policy lives per-tool, in the
description — never in the loop):

* ``web_research(query, topic)`` — CONSENT-GATED: only on the user's explicit
  request. Its ``run`` flow is the security-relevant part (see
  ``web_research.py`` for the dual-LLM rationale): cache → sub-completion
  research → FAIL-CLOSED guardrail scan of the bullets → index the raw
  excerpts (their own fail-closed scan) → return the bullets.
* ``record_evaluation(...)`` — ALWAYS-CALL: after every scored answer. Not an
  action but a structured-output channel: the card the model would otherwise
  only state as prose is captured as data for the Evaluations tab. No consent,
  no guardrail scan (the content is the model's own output, not external).

``run`` never raises. A model inventing arguments, a failing search, or a
flagged result all come back as error strings the model can read and recover
from — an exception here would abort the user's whole turn.
"""

from __future__ import annotations

import json
import re
from datetime import date

from .config import (
    EVALUATION_DIMENSIONS,
    EVALUATION_SCORE_MAX,
    EVALUATION_SCORE_MIN,
    QUESTION_TYPES,
    WEB_TOPICS,
)
from .ingest import IngestedDocument, should_ingest


# One dimension actually being scored: the dimension name, then at most a
# colon/dash separator (optionally bold-wrapped), then a 1-5 score — either
# "n/5" or a standalone digit. The lookahead rejects digits that are part of
# a range ("Relevance (1-5)" in a rubric announcement) or a decimal
# ("relevance averaged 4.2" in a recap): those turns describe scores without
# assigning one, and correctly record no card.
_DIMENSION_SCORED = r"\b{d}\b\*{{0,2}}\s*[:\-–—]?\s*(?:[1-5]\s*/\s*5|[1-5](?![\d.\-–/]))"


def looks_like_feedback(reply_text) -> bool:
    """Heuristic: does this reply read like scored answer feedback?

    Used to detect a skipped ``record_evaluation`` call (feedback given, no
    card recorded). Deliberately conservative — a false "you skipped a card"
    warning is worse than a missed one — so it requires at least three rubric
    dimensions each actually being assigned a 1-5 score.
    """
    text = (reply_text or "").lower()
    scored = sum(
        1
        for dimension in EVALUATION_DIMENSIONS
        if re.search(_DIMENSION_SCORED.format(d=dimension), text)
    )
    return scored >= 3


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
        # Evaluation cards recorded this turn, for the caller to commit once
        # the turn is allowed. Keyed by question: a repeat call for the same
        # question is a correction and replaces that card (observed live — a
        # model occasionally re-records with revised scores on the next hop),
        # while distinct questions append (a message can answer two questions).
        self.evaluations = []

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
            },
            {
                "type": "function",
                "function": {
                    "name": "record_evaluation",
                    "description": (
                        "Record your evaluation of the interview answer you "
                        "just scored. Call this EVERY time you give feedback "
                        "on an answer, immediately after scoring it, with the "
                        "SAME scores and the same strengths-and-gaps feedback "
                        "you told the candidate — never different numbers. "
                        "Call it once per scored answer; calling again for "
                        "the same question replaces that card. Do not call "
                        "it outside answer feedback (not during intake, not "
                        "for small talk)."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": (
                                    "The interview question that was "
                                    "answered, shortened to one line."
                                ),
                            },
                            "question_type": {
                                "type": "string",
                                "enum": QUESTION_TYPES,
                            },
                            "scores": {
                                "type": "object",
                                "description": (
                                    "Your 1-5 score per rubric dimension — "
                                    "identical to the scores in your reply."
                                ),
                                "properties": {
                                    dimension: {
                                        "type": "integer",
                                        "minimum": EVALUATION_SCORE_MIN,
                                        "maximum": EVALUATION_SCORE_MAX,
                                    }
                                    for dimension in EVALUATION_DIMENSIONS
                                },
                                "required": list(EVALUATION_DIMENSIONS),
                                "additionalProperties": False,
                            },
                            "verbal_feedback": {
                                "type": "string",
                                "description": (
                                    "Your strengths-and-gaps feedback for "
                                    "this answer, as markdown — the same "
                                    "content as your chat reply."
                                ),
                            },
                        },
                        "required": [
                            "question",
                            "question_type",
                            "scores",
                            "verbal_feedback",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
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
        handlers = {
            "web_research": self._web_research,
            "record_evaluation": self._record_evaluation,
        }
        handler = handlers.get(name)
        if handler is None:
            return f"error: no tool named {name!r}"
        try:
            return handler(**parsed)
        except TypeError as exc:
            # Models do invent arguments a schema never declared; splatting
            # them would raise out of the tool loop and kill the turn.
            return f"error: {exc}"

    def _web_research(self, query, topic="other"):
        # Type-check before touching str methods: JSON-valid arguments can
        # still be the wrong type (e.g. a number), and an AttributeError here
        # would escape run()'s TypeError net and kill the turn.
        if not isinstance(query, str) or not query.strip():
            return "error: query must be a non-empty string"
        query = query.strip()
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

    def _record_evaluation(self, question, question_type, scores, verbal_feedback):
        """Validate and hand one evaluation card to the caller.

        The schema already constrains everything, but providers enforce JSON
        Schema unevenly — re-validate here so a malformed card comes back to
        the model as an error string it can correct, instead of poisoning the
        Evaluations tab with unusable data.
        """
        # Type-check before str methods (see _web_research) — JSON-valid
        # arguments can still be the wrong type.
        if not isinstance(question, str) or not question.strip():
            return "error: question must be a non-empty string"
        question = question.strip()
        if not isinstance(verbal_feedback, str):
            return "error: verbal_feedback must be a string"
        if question_type not in QUESTION_TYPES:
            question_type = "other"
        if not isinstance(scores, dict):
            return "error: scores must be an object of dimension -> integer"
        missing = [d for d in EVALUATION_DIMENSIONS if d not in scores]
        if missing:
            return f"error: scores is missing dimensions: {', '.join(missing)}"
        clean_scores = {}
        for dimension in EVALUATION_DIMENSIONS:
            value = scores[dimension]
            # Accept integral floats (4.0): JSON Schema's "integer" does, so
            # rejecting them here would fail a schema-conforming call — and
            # models retry such rejections verbatim until the hops run out.
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not EVALUATION_SCORE_MIN <= value <= EVALUATION_SCORE_MAX
            ):
                return (
                    f"error: score for {dimension!r} must be an integer "
                    f"between {EVALUATION_SCORE_MIN} and {EVALUATION_SCORE_MAX}"
                )
            clean_scores[dimension] = value
        card = {
            "question": question,
            "question_type": question_type,
            "scores": clean_scores,
            "verbal_feedback": verbal_feedback.strip(),
        }
        # A repeat call for the SAME question is a correction and replaces
        # that card; a different question is a second scored answer (the user
        # answered more than one question in a message) and appends.
        replaced = any(c["question"] == question for c in self.evaluations)
        self.evaluations[:] = [
            c for c in self.evaluations if c["question"] != question
        ] + [card]
        if replaced:
            return "evaluation recorded (replaced your previous card for this question)"
        return "evaluation recorded"
