"""Admission rules for external text entering the conversation.

Every tool that brings in text the interviewer did not write — a fetched
repository file, a web-research digest — puts it through the same two steps:

  1. **screen**, fail-closed: text reaches the model only after a clean
     guardrail scan. An inconclusive scan counts as a failure, not a pass
     (``should_ingest``), because the tools are exactly the channel an attacker
     would use to reach the interviewer.
  2. **index**, so a bounded excerpt can go inline this turn while the whole
     text stays retrievable on later ones.

Both were previously written out per tool handler, which made screening
opt-in-by-convention: a new tool that forgot to call the guard shipped
unscreened, and no test failed. Here they are one definition with one set of
tests, so a handler (and, next, the middleware that will apply this to every
tool result) can only choose whether to apply the policy, never what it means.

Kept free of any agent framework on purpose: this is what the tools do, not how
the loop runs them, so both the hand-rolled loop and the middleware call the
same object.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import GITHUB_FILE_INLINE_CHARS
from .ingest import IngestedDocument, should_ingest
from .spend import OverBudget


@dataclass(frozen=True)
class ToolOutcome:
    """What a tool produced, described so the policy can decide what to do.

    A handler states what it *got*; ``ContentPolicy.apply`` decides what the
    model is allowed to *see*. That split is the point: a handler cannot forget
    to screen, because screening is no longer something it does.

    ``inline`` is the text destined for the model. Leaving it ``None`` means
    "derive it from ``document``" — a bounded excerpt of a fetched file — which
    also makes that document's scan blocking, since the inline text would be a
    piece of it. When ``inline`` is given, ``document`` is extra: its scan
    failing costs the index, not the reply.
    """

    inline: str | None = None
    # Fuller text to keep for retrieval. Carries its own name/type/topic.
    document: IngestedDocument | None = None
    # Guardrail framing — see guardrails.py ("code" vs "web" vs "document").
    kind: str = "document"
    # What a warning about the inline text names.
    label: str = ""
    blocked_as: str = "tool content blocked"
    # Warning kind if the document's own scan fails; defaults to blocked_as.
    document_blocked_as: str | None = None
    # What the model is told when the inline text is refused.
    blocked_message: str = (
        "error: that content was withheld by a safety screen; tell the "
        "candidate you could not read it"
    )
    # Provenance to record on admission, never before it.
    terms: tuple = field(default_factory=tuple)
    # Any other side effect that must wait for admission — web research caches
    # its bullets here, so a refused digest is not served again from cache.
    on_admitted: object = None
    # Graph-state keys to merge ONLY if the content is admitted (research
    # citations: sources for a refused digest must not reach the panel).
    state_update: dict = field(default_factory=dict)
    # Spend to record REGARDLESS of admission — the sub-completion ran and was
    # billed whatever the screen then decided.
    billed_cost: float = 0.0
    # A (name, kind, reason) warning for the user, emitted whatever the screen
    # decides — a tool reporting its own failure, not a refusal.
    warning: tuple | None = None
    # The model's own output coming back (an evaluation card), not external
    # text: the one case that is not screened, and it must be stated.
    trusted: bool = False

    @classmethod
    def own_output(cls, text, warning=None):
        """Text the model itself produced, echoed back — never screened."""
        return cls(inline=text, trusted=True, warning=warning)


class ContentPolicy:
    """The screen-and-index policy for one turn, bound to that turn's state.

    ``guard`` screens, ``index`` stores. The three callbacks report to the
    caller's UI and are never load-bearing — see ``tools._never_fatal``.
    """

    def __init__(
        self,
        guard,
        index,
        on_warning=None,
        on_document=None,
        on_progress=None,
    ):
        self._guard = guard
        self._index = index
        self._on_warning = on_warning or (lambda name, kind, reason="": None)
        self._on_document = on_document or (lambda doc: None)
        self._on_progress = on_progress or (lambda message: None)

    def screen(self, text, *, kind, label, blocked_as, progress=None):
        """Scan ``text``; return True only on a clean verdict.

        ``label`` names the thing in the warning the user sees; ``blocked_as``
        is the warning kind that selects its wording (``ui.warning_message``).
        A blocked *or* errored scan returns False — the caller decides whether
        that is fatal to its tool (a fetched file) or merely means the text is
        not indexed (web research's raw excerpts).
        """
        if progress:
            self._on_progress(progress)
        verdict = self._guard.check_document(text, kind=kind)
        if should_ingest(verdict):
            return True
        reason = verdict.reason or "the safety scan could not complete"
        self._on_warning(label, blocked_as, reason)
        return False

    def apply(self, outcome):
        """Run the policy over one tool result; return ``(text, admitted)``.

        The single place screening happens. A tool that returns external text
        gets it screened here whether or not its handler thought about it —
        which is the whole difference from calling ``screen`` per handler, and
        why a directory listing (previously returned unscreened, because only
        file *bodies* looked like documents) is now covered by default.
        """
        if outcome.trusted:
            return outcome.inline, True

        if outcome.inline is None:
            # The inline text will be an excerpt of the document, so one scan
            # governs both and failing it withholds everything.
            document = outcome.document
            if not self.screen(
                document.text,
                kind=outcome.kind,
                label=outcome.label or document.name,
                blocked_as=outcome.blocked_as,
                progress=f"Screening {document.name}…",
            ):
                return outcome.blocked_message, False
            self.index(document)
            return _excerpt(document), True

        if not self.screen(
            outcome.inline,
            kind=outcome.kind,
            label=outcome.label,
            blocked_as=outcome.blocked_as,
            progress=f"Screening {outcome.label}…" if outcome.label else None,
        ):
            return outcome.blocked_message, False

        # Tier two, independent of the reply: its own scan, and failing it
        # only costs later retrieval.
        if outcome.document is not None:
            if self.screen(
                outcome.document.text,
                kind=outcome.kind,
                label=outcome.document.name,
                blocked_as=outcome.document_blocked_as or outcome.blocked_as,
                progress=f"Indexing {outcome.document.name} for later retrieval…",
            ):
                self.index(outcome.document)
        return outcome.inline, True

    def index(self, doc):
        """Store ``doc`` for retrieval; return whether it was stored.

        Indexing is the durability half of the two-tier contract — the inline
        excerpt still works without it — so a failure warns and returns False
        rather than raising.
        """
        try:
            self._index.add_document(doc)
        except OverBudget:
            # Not swallowed into a warning. Indexing now spends (the embedding
            # call), so this is a *refusal*, and turning a refusal into a
            # "document error" plus a quietly smaller corpus is the silent
            # downgrade R22.8 forbids — the same shape as the guardrail's
            # fail-open. `grounding` re-raises it and `tools` handles it
            # explicitly; this was the one place it leaked into the generic
            # handler.
            raise
        except Exception as exc:
            self._on_warning(doc.name, "error", str(exc))
            return False
        self._on_document(doc)
        return True


def _excerpt(document):
    """The bounded inline view of a document that was admitted in full.

    Tier one of the two-tier contract: enough for a grounded question this
    turn, capped so a large file cannot flood the conversation. The header is
    load-bearing anti-fabrication text — it tells the model this content is
    real and was just fetched, which a model asked to discuss code it could not
    read has been observed inventing instead.
    """
    text = document.text[:GITHUB_FILE_INLINE_CHARS]
    note = ""
    if len(document.text) > GITHUB_FILE_INLINE_CHARS:
        note = (
            f"\n\n[Truncated at {GITHUB_FILE_INLINE_CHARS} of "
            f"{len(document.text)} characters. The whole file is indexed — ask "
            "about the rest and it will reach you through your retrieved "
            "context on the next turn.]"
        )
    return f"{document.name} (real contents, read just now):\n{text}{note}"
