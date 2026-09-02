"""Turn grounding: condense, retrieve, build the context block.

This was 45 lines inside `chat_bot.main()`, reachable only by running the app,
and it carries the turn's fail-open policy: a condensation failure still
searches (with the raw message), and each retrieval source fails independently
so a broken knowledge base cannot cost you the document excerpts. None of that
was asserted anywhere — `chat_bot.py` lines 448-730 had no coverage at all.

Extracted here so the policy is testable without a browser, following the
project's injectable-client convention (R13.2): the condenser and the warning
sink are passed in, so this module imports no Streamlit. The two spinners the
original showed are presentation and stay with the caller.
"""

from __future__ import annotations

import pytest

from interview_prep.grounding import ground_turn
from interview_prep.retrieval import RetrievedChunk


def chunk(text, source="resume.pdf", doc_type="resume", chunk_id=0):
    """A real `RetrievedChunk`, not a stand-in.

    An earlier version of this file used bare strings here. `format_context_block`
    reads `.doc_type/.topic/.source/.chunk_id/.text` unconditionally, so the
    implementer was forced to wrap it in `except Exception` just to satisfy the
    doubles — a production guard that would have silently turned a broken context
    block into an ungrounded turn. A double that does not match the real
    interface buys nothing and costs exactly that.
    """
    return RetrievedChunk(
        text=text, source=source, doc_type=doc_type, chunk_id=chunk_id, score=0.9
    )


SYSTEM_PROMPT = "You are an interviewer.\n\n{retrieved_context}\n\nBegin."
UNGROUNDED_PROMPT = "You are an interviewer. Begin."


class FakeIndex:
    def __init__(self, chunks=(), exc=None, empty=False):
        self.chunks, self._exc, self.is_empty = list(chunks), exc, empty
        self.queries = []

    def retrieve(self, query):
        self.queries.append(query)
        if self._exc:
            raise self._exc
        return self.chunks


class FakeKB:
    def __init__(self, chunks=(), exc=None, empty=False):
        self.chunks, self._exc, self.is_empty = list(chunks), exc, empty
        self.calls = []

    def retrieve(self, query, embedding_model):
        self.calls.append((query, embedding_model))
        if self._exc:
            raise self._exc
        return self.chunks


class Rewrite:
    def __init__(self, query, errored=False):
        self.query, self.errored = query, errored


def recorder():
    """A warning sink shaped like `chat_bot.record_warning`."""
    seen = []

    def warn(name, kind, reason=""):
        seen.append({"name": name, "kind": kind, "reason": reason})

    warn.seen = seen
    return warn


def run(
    *,
    prompt="tell me about that role",
    history=(),
    system_prompt=SYSTEM_PROMPT,
    grounding_aware=True,
    index=None,
    kb=None,
    condense=None,
    warn=None,
    embedding_model="emb-1",
):
    warn = warn or recorder()
    return (
        ground_turn(
            prompt=prompt,
            history=list(history),
            system_prompt=system_prompt,
            is_grounding_aware=grounding_aware,
            doc_index=index if index is not None else FakeIndex(empty=True),
            kb=kb,
            embedding_model=embedding_model,
            condense=condense or (lambda p, h: Rewrite(p)),
            warn=warn,
        ),
        warn,
    )


# --- opting out -------------------------------------------------------------


def test_a_source_without_the_placeholder_is_left_byte_identical():
    # R15.8/R20-era contract: a non-grounding-aware source must behave exactly
    # as if RAG did not exist — no retrieval, no substitution, no warning.
    index = FakeIndex(chunks=[chunk("never used")])
    result, warn = run(
        grounding_aware=False, system_prompt=UNGROUNDED_PROMPT, index=index
    )
    assert result.effective_prompt == UNGROUNDED_PROMPT
    assert index.queries == []
    assert result.retrieved == []
    assert warn.seen == []


def test_nothing_to_retrieve_from_still_fills_the_slot():
    # An empty index and no KB: the placeholder must still be substituted, or
    # the model is handed a literal "{retrieved_context}".
    result, _ = run(index=FakeIndex(empty=True), kb=None)
    assert "{retrieved_context}" not in result.effective_prompt
    assert result.retrieved == []


# --- query condensation ------------------------------------------------------


def test_the_first_turn_is_searched_verbatim_without_condensing():
    # No history means no referents to resolve, so the extra model call is
    # skipped rather than made and discarded.
    calls = []

    def condense(p, h):
        calls.append((p, h))
        return Rewrite("should not be used")

    result, _ = run(history=(), index=FakeIndex(), condense=condense)
    assert calls == []
    assert result.query == "tell me about that role"


def test_a_follow_up_is_condensed_before_searching():
    index = FakeIndex()
    result, _ = run(
        history=[{"role": "user", "content": "I applied to Acme"}],
        index=index,
        condense=lambda p, h: Rewrite("the Acme role"),
    )
    assert result.query == "the Acme role"
    assert index.queries == ["the Acme role"]


def test_a_failed_condensation_still_searches_and_says_so():
    # Fails open: the raw prompt is searched rather than the turn being lost,
    # but the user is told the search used their words verbatim.
    index = FakeIndex()
    result, warn = run(
        history=[{"role": "user", "content": "earlier"}],
        index=index,
        condense=lambda p, h: Rewrite(p, errored=True),
    )
    assert index.queries == ["tell me about that role"]
    assert [w["kind"] for w in warn.seen] == ["condense"]


# --- independent fail-open per source ---------------------------------------


def test_a_broken_document_index_does_not_cost_the_knowledge_base():
    kb = FakeKB(chunks=[chunk("kb chunk", source="kb.md", doc_type="knowledge base")])
    result, warn = run(
        index=FakeIndex(exc=RuntimeError("index down")), kb=kb
    )
    assert [c.text for c in result.retrieved] == ["kb chunk"]
    assert [w["kind"] for w in warn.seen] == ["retrieval"]
    assert "index down" in warn.seen[0]["reason"]


def test_a_broken_knowledge_base_does_not_cost_the_documents():
    result, warn = run(
        index=FakeIndex(chunks=[chunk("doc chunk")]),
        kb=FakeKB(exc=RuntimeError("kb down")),
    )
    assert [c.text for c in result.retrieved] == ["doc chunk"]
    # A distinct kind: the "retrieval" copy talks about uploaded documents,
    # which may not exist at all on a KB-only grounded turn.
    assert [w["kind"] for w in warn.seen] == ["kb_retrieval"]


def test_both_sources_failing_leaves_an_ungrounded_but_working_turn():
    result, warn = run(
        index=FakeIndex(exc=RuntimeError("a")), kb=FakeKB(exc=RuntimeError("b"))
    )
    assert result.retrieved == []
    assert "{retrieved_context}" not in result.effective_prompt
    assert sorted(w["kind"] for w in warn.seen) == ["kb_retrieval", "retrieval"]


def test_both_sources_are_merged_when_both_work():
    result, warn = run(
        index=FakeIndex(chunks=[chunk("doc")]),
        kb=FakeKB(chunks=[chunk("kb", source="kb.md", doc_type="knowledge base")]),
    )
    assert [c.text for c in result.retrieved] == ["doc", "kb"]
    assert warn.seen == []


# --- the knowledge base is consulted correctly -------------------------------


def test_an_empty_knowledge_base_is_not_queried():
    kb = FakeKB(chunks=[chunk("never")], empty=True)
    run(index=FakeIndex(), kb=kb)
    assert kb.calls == []


def test_the_knowledge_base_is_queried_with_the_active_embedding_model():
    # Vectors from different models are not comparable, so the model in force
    # for this turn has to be the one passed through.
    kb = FakeKB()
    run(index=FakeIndex(empty=True), kb=kb, embedding_model="emb-2")
    assert kb.calls == [("tell me about that role", "emb-2")]


@pytest.mark.parametrize("kb", [None, "empty"])
def test_no_usable_knowledge_base_and_an_empty_index_skips_retrieval(kb):
    index = FakeIndex(empty=True)
    result, warn = run(index=index, kb=None if kb is None else FakeKB(empty=True))
    assert index.queries == []
    assert result.retrieved == []
    assert warn.seen == []


def test_query_is_none_exactly_when_no_retrieval_was_attempted():
    """The discriminator `chat_bot` uses to decide whether to touch session state.

    On a turn that searched nothing, the Developer tab's last-retrieval panel
    must keep showing the previous real retrieval rather than being blanked, so
    the caller needs to tell "searched and found nothing" from "did not search".
    """
    unaware, _ = run(grounding_aware=False, system_prompt=UNGROUNDED_PROMPT)
    assert unaware.query is None

    nothing_to_search, _ = run(index=FakeIndex(empty=True), kb=None)
    assert nothing_to_search.query is None

    searched, _ = run(index=FakeIndex(chunks=[chunk("doc")]))
    assert searched.query == "tell me about that role"


def test_the_rendered_block_is_returned_for_token_accounting():
    """R15.12: the caller charges the injected block's tokens to the turn.

    The block is per-turn and never enters stored history, so if grounding did
    not hand it back there would be no way to recover it — the estimate would
    silently undercount every grounded turn.
    """
    result, _ = run(index=FakeIndex(chunks=[chunk("resume text here")]))
    assert "resume text here" in result.context_block
    assert result.context_block in result.effective_prompt

    ungrounded, _ = run(grounding_aware=False, system_prompt=UNGROUNDED_PROMPT)
    assert ungrounded.context_block == ""
