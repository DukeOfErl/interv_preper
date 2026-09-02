"""Turn grounding: condense, retrieve, build the context block.

This was 45 lines inside `chat_bot.main()`, reachable only by running the app,
and it carries the turn's fail-open policy: a condensation failure still
searches (with the raw message), and each retrieval source fails independently
so a broken knowledge base cannot cost you the document excerpts. None of that
was asserted anywhere — `chat_bot.py` lines 448-730 had no coverage at all.

Extracted here so the policy is testable without a browser, following the
project's injectable-client convention (R13.2): the condenser and the warning
sink are both passed in, so this module imports no Streamlit. The two
`st.spinner(...)` calls in the original are presentation and stay with the
caller.

`query` is ``None`` exactly when no retrieval was attempted (an unaware
source, or nothing to search). The caller relies on that to leave the
Developer tab's last-retrieval panel alone on an ungrounded turn instead of
blanking it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .config import QUERY_REWRITE_HISTORY_TURNS
from .retrieval import fill_retrieved_context, format_context_block


@dataclass(frozen=True)
class GroundedTurn:
    """What grounding produced for this turn."""

    effective_prompt: str
    query: str | None = None
    retrieved: list = field(default_factory=list)
    # The rendered block, kept because the caller charges its tokens to the
    # turn for the next-prompt estimate (R15.12) — it is injected per turn and
    # never stored in history, so it cannot be recovered from the transcript.
    context_block: str = ""


def ground_turn(
    *,
    prompt: str,
    history: list,
    system_prompt: str,
    is_grounding_aware: bool,
    doc_index: Any,
    kb: Any,
    embedding_model: str,
    condense: Callable[[str, list], Any],
    warn: Callable[[str, str, str], None],
) -> GroundedTurn:
    if not is_grounding_aware:
        return GroundedTurn(effective_prompt=system_prompt)

    query = None
    retrieved: list = []
    context_block = ""

    kb_ready = kb is not None and not kb.is_empty
    if not doc_index.is_empty or kb_ready:
        # On a follow-up, rewrite the message into a standalone query so
        # vector search isn't handed an anaphoric fragment ("that role").
        # The first turn has no referents to resolve — use it verbatim
        # and skip the extra call. Condensing fails open to the prompt.
        if history:
            rewrite = condense(prompt, history[-QUERY_REWRITE_HISTORY_TURNS:])
            query = rewrite.query
            # Fail open, but tell the user we searched with the raw
            # message instead of a rewritten query.
            if rewrite.errored:
                warn("", "condense")
        else:
            query = prompt

        # Both retrievals fail open independently: answer with whatever
        # context could be fetched, but say so.
        if not doc_index.is_empty:
            try:
                retrieved += doc_index.retrieve(query)
            except Exception as exc:
                warn("", "retrieval", str(exc))
        if kb_ready:
            try:
                retrieved += kb.retrieve(query, embedding_model)
            except Exception as exc:
                # Distinct kind: the "retrieval" copy talks about
                # uploaded documents, which may not even exist on
                # a KB-only grounded turn.
                warn("", "kb_retrieval", str(exc))
        # Deliberately unguarded, unlike the two retrievals above. Formatting
        # is not an external call: it reads fields off chunks the sources just
        # produced, so a failure here means a broken context-block contract
        # (R15.8), not a flaky dependency. Swallowing it would ship a
        # confidently ungrounded reply — the failure this whole module exists
        # to make visible. Let it raise.
        context_block = format_context_block(retrieved)

    effective_prompt = fill_retrieved_context(system_prompt, context_block)
    return GroundedTurn(
        effective_prompt=effective_prompt,
        query=query,
        retrieved=retrieved,
        context_block=context_block,
    )
