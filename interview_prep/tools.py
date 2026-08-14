"""Tools the interviewer LLM can call, and the dispatcher that runs them.

A *tool* is two things kept deliberately separate:

  1. a **schema** the model sees (name, description, JSON-Schema parameters) —
     prompt text, and the only thing that makes the model decide to call it, so
     the description does the real work (including the consent policy);
  2. a **local implementation** the model never sees, invoked by ``ToolBox.run``
     with the arguments the model produced.

Two *local* tools, with opposite invocation policies (policy lives per-tool, in
the description — never in the loop):

* ``web_research(query, topic)`` — CONSENT-GATED: only on the user's explicit
  request. Its ``run`` flow is the security-relevant part (see
  ``web_research.py`` for the dual-LLM rationale): cache → sub-completion
  research → FAIL-CLOSED guardrail scan of the bullets → index the raw
  excerpts (their own fail-closed scan) → return the bullets.
* ``record_evaluation(...)`` — ALWAYS-CALL: after every scored answer. Not an
  action but a structured-output channel: the card the model would otherwise
  only state as prose is captured as data for the Evaluations tab. No consent,
  no guardrail scan (the content is the model's own output, not external).

A ``ToolBox`` may additionally carry MCP tools (``github_mcp.GitHubMCP``):
their schemas are discovered from the remote server rather than written here,
and ``run`` relays their calls. A fetched repository file takes the same
two-tier route as web research — screened fail-closed, indexed for retrieval,
returned only as a bounded excerpt.

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
    GITHUB_FILE_INLINE_CHARS,
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


def _never_fatal(callback):
    """Stop a reporting callback from being able to break a tool.

    ``run()`` promises never to raise, but the progress and warning hooks fire
    from several places outside its try blocks — so a callback that throws
    escapes as a tool failure. Observed live: under the LangChain loop, tool
    nodes execute on a ThreadPoolExecutor thread, Streamlit's context is
    thread-local, and ``on_progress`` painting a status line raised
    ``NoSessionContext``. A working GitHub call was reported to the model as
    "failed after 3 attempts" — the retry middleware faithfully repeating a
    failure that was ours, not the server's.

    The caller is responsible for making its callbacks work (chat_bot
    re-attaches the Streamlit context); this only ensures the blast radius of
    getting that wrong is a missing status line rather than a dead tool.
    """

    def guarded(*args, **kwargs):
        try:
            return callback(*args, **kwargs)
        except Exception:
            return None

    return guarded


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
        mcp=None,
        seen_terms=None,
        code_corpus=None,
    ):
        # Optional MCP client (e.g. GitHubMCP): contributes its discovered
        # ``specs`` and handles calls routed by ``tool_names`` membership.
        self._mcp = mcp
        # Session-scoped record of everything MCP tools actually returned
        # (repo paths, identifiers from fetched files). The caller keeps it
        # across turns and checks replies against it, so a fabricated file or
        # function can be flagged instead of passing silently.
        self._seen_terms = seen_terms if seen_terms is not None else set()
        # Verbatim text of every file fetched this session, so code the reply
        # quotes can be checked by substring match rather than judgment.
        self._code_corpus = code_corpus if code_corpus is not None else []
        # This turn's tool activity, for ``final_hop_note``.
        self.tool_calls_made = 0
        self.mcp_calls = 0
        self.files_read = []
        self._researcher = researcher
        self._guard = guard
        self._index = index
        # Called with each successfully indexed IngestedDocument so the caller
        # can register it in the session's document list — otherwise the doc
        # is invisible to the Documents panel and lost when an embedding-model
        # switch rebuilds the index from that list.
        self._on_document = _never_fatal(on_document or (lambda doc: None))
        # Session-scoped cache: normalized query -> bullets already returned.
        # A chatty model will otherwise re-search the same company mid-session.
        self._cache = cache if cache is not None else {}
        self._on_warning = _never_fatal(
            on_warning or (lambda name, kind, reason="": None)
        )
        self._on_progress = _never_fatal(on_progress or (lambda message: None))
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
        local = self._local_specs()
        if self._mcp is None:
            return local
        local_names = {spec["function"]["name"] for spec in local}
        # A remote tool shadowing a local name would make dispatch ambiguous;
        # the local tool wins.
        return local + [
            spec
            for spec in self._mcp.specs
            if spec["function"]["name"] not in local_names
        ]

    def _local_specs(self):
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
        if handler is not None:
            self.tool_calls_made += 1
            try:
                return handler(**parsed)
            except TypeError as exc:
                # Models do invent arguments a schema never declared; splatting
                # them would raise out of the tool loop and kill the turn.
                return f"error: {exc}"
        if self._mcp is not None and name in self._mcp.tool_names:
            # Counted only for names that exist: a hallucinated tool name must
            # not make ``final_hop_note`` claim tools were used.
            self.tool_calls_made += 1
            self._on_progress(f"Checking GitHub: {name}…")
            try:
                return self._github(name, parsed)
            except Exception as exc:
                # Network/auth failures from the remote server must not
                # escape the never-raise contract.
                self._on_warning(name, "github tool", str(exc))
                return f"error: the GitHub tool {name!r} failed: {exc}"
        return f"error: no tool named {name!r}"

    def final_hop_note(self):
        """A reminder to inject before the answer is forced, or ``""``.

        The tool loop withholds tools on its last hop to force a text answer.
        A model still mid-exploration is then cornered: it must say something,
        cannot fetch more, and (observed twice in testing) either invents a
        repository or writes out the tool calls it *would* have made along with
        their imagined results. Naming the situation is what makes stopping
        honestly an available move — the note therefore fires whenever any tool
        ran this turn, not only when nothing was read.
        """
        if not self.tool_calls_made:
            return ""
        preamble = (
            "SYSTEM NOTE: you have no tool calls left this turn. Whatever you "
            "have already received is all you get. Do NOT write tool calls, "
            "tool arguments, or tool results in your reply, and do not "
            "describe what a further call would have returned — a model in "
            "your position has been observed acting out the rest of its "
            "exploration in prose, which is fabrication. "
        )
        if self.mcp_calls and not self.files_read:
            return preamble + (
                "This turn obtained no file contents at all — only listings, "
                "searches, or errors — so you have not read any of this "
                "candidate's code. Say that plainly and ask which file to "
                "look at, or ask them to describe it."
            )
        if self.mcp_calls:
            return preamble + (
                "You read: "
                + ", ".join(self.files_read)
                + ". Work only from those (and your retrieved context). If you "
                "need a file you did not read, say so and ask the candidate "
                "about it, or offer to read it next turn."
            )
        return preamble + (
            "Answer from what the tools already returned; if it was not "
            "enough, say so plainly."
        )

    def _github(self, name, arguments):
        """Relay one MCP call, treating a fetched file like a document.

        Listings and searches come back inline. A file body does not: it is
        screened fail-closed (like uploads and web research), indexed for
        retrieval, and returned only as a bounded excerpt — so a large file
        informs later turns through RAG instead of flooding this one.
        """
        self.mcp_calls += 1
        result = self._mcp.call(name, arguments)
        if result.errored:
            return result.text
        if not result.file_text:
            self._seen_terms.update(result.terms)
            return result.text

        self._on_progress(f"Screening {result.source}…")
        verdict = self._guard.check_document(result.file_text, kind="code")
        if not should_ingest(verdict):
            reason = verdict.reason or "the safety scan could not complete"
            self._on_warning(result.source, "github file blocked", reason)
            return (
                "error: that file was withheld by a safety screen; tell the "
                "candidate you could not read it and ask about another file"
            )

        self._seen_terms.update(result.terms)
        self._code_corpus.append(result.file_text)
        self.files_read.append(result.source)
        doc = IngestedDocument(
            name=result.source, doc_type="github", text=result.file_text
        )
        try:
            self._index.add_document(doc)
        except Exception as exc:
            # Indexing is the durability half; the excerpt below still works.
            self._on_warning(doc.name, "error", str(exc))
        else:
            self._on_document(doc)

        excerpt = result.file_text[:GITHUB_FILE_INLINE_CHARS]
        note = ""
        if len(result.file_text) > GITHUB_FILE_INLINE_CHARS:
            note = (
                f"\n\n[Truncated at {GITHUB_FILE_INLINE_CHARS} of "
                f"{len(result.file_text)} characters. The whole file is "
                "indexed — ask about the rest and it will reach you through "
                "your retrieved context on the next turn.]"
            )
        return f"{result.source} (real contents, read just now):\n{excerpt}{note}"

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
