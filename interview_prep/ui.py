"""Streamlit rendering helpers.

Except for ``render_api_key_input`` (which anchors itself above the sidebar
tabs), these helpers render into whatever container is active when they are
called — the entry point wraps each call in a sidebar tab (``with tab:``).
"""

from __future__ import annotations

import streamlit as st

from .config import DOCUMENT_TYPES, EVALUATION_DIMENSIONS, UPLOAD_FILE_TYPES
from .context import ContextUsage
from .pricing import ChatSpend, format_spend
from .prompts import PromptLibrary, PromptSource


def render_api_key_input() -> str:
    """Render a sidebar input for a session-scoped OpenRouter API key.

    Only meant to be shown when no key was found in the environment. The value
    is held in ``st.session_state["user_api_key"]`` for this browser session
    only — it is never written to disk. Returns the entered key (stripped).
    """
    with st.sidebar:
        st.subheader("API key")
        key = st.text_input(
            "OpenRouter API key",
            type="password",
            placeholder="sk-or-...",
            key="user_api_key",
            help=(
                "No OPENROUTER_API_KEY was found in your environment. Enter one "
                "to use the app. It is kept only for this session (in memory) "
                "and never saved to disk."
            ),
        )
    return (key or "").strip()


def render_prompt_selector(sources: list[PromptSource]) -> None:
    """Render the system-prompt source picker.

    The selectbox is bound to ``st.session_state["prompt_source_key"]``; picking
    a new source triggers a rerun and the entry point rebuilds the prompt from
    it. A folder icon marks multi-file (directory) sources.
    """
    def _label(key: str) -> str:
        source = next(s for s in sources if s.key == key)
        return f"📁 {source.label}" if source.is_directory else source.label

    st.selectbox(
        "Interviewer",
        options=[s.key for s in sources],
        format_func=_label,
        key="prompt_source_key",
        help=(
            "Pick a single prompt file or a folder of files that are "
            "concatenated in numeric-prefix order."
        ),
    )


def render_reasoning_selector(efforts: list[str]) -> None:
    """Render the reasoning-effort picker.

    Only call this when the active model is a reasoning model — the caller
    decides whether to show it. Bound to ``st.session_state["reasoning_effort"]``.
    """
    st.selectbox(
        "Effort",
        options=efforts,
        key="reasoning_effort",
        help=(
            "How hard the model thinks before answering. Higher effort can "
            "improve quality but adds latency and cost."
        ),
    )


def render_document_uploader(key: str = "doc_uploader"):
    """Render the drag-and-drop document uploader; returns the uploaded files.

    Only renders the widget — parsing, screening, and indexing are orchestrated
    by the entry point. The caller rotates ``key`` after processing a batch so
    the widget empties: it is a pure drop zone, and the authoritative list of
    what actually got in is the Ingested Documents panel (a rejected file
    lingering in the widget would read as accepted).
    """
    return st.file_uploader(
        "Documents (resume, job ad, cover letter)",
        type=UPLOAD_FILE_TYPES,
        accept_multiple_files=True,
        key=key,
        help=(
            "Drag and drop files here. Each document is scanned by the "
            "safety guardrail before it is used; documents that fail the "
            "scan are rejected. Processed files are listed under "
            "Ingested Documents."
        ),
    )


def render_embedding_selector(models: list[str]) -> None:
    """Render the embedding-model picker (bound to ``embedding_model``).

    Switching models re-embeds every ingested document — vectors from
    different models are not comparable.
    """
    st.selectbox(
        "Embedding model",
        options=models,
        key="embedding_model",
        help=(
            "Model used to embed document chunks for retrieval. Changing "
            "it re-embeds all uploaded documents."
        ),
    )


# Shown once per session after a repository turn run below "high" effort.
# A deep-dive is a multi-step tool workflow — decide what to open, list it, read
# a file, decide again — and reasoning effort is what buys that discipline.
# Below "high", models were observed guessing plausible paths instead of listing
# directories, and narrating tool calls instead of making them (ADR-0130).
GITHUB_EFFORT_HINT = (
    "**Tip for repository deep-dives.** Reading code takes several tool steps, "
    "and this works noticeably better with **Reasoning → Effort** set to "
    "**high** in the sidebar. At lower effort the interviewer may guess at file "
    "names rather than looking them up. (Choosing a more capable model will "
    "help too, and is coming.)"
)


def github_effort_hint(mcp_calls, reasoning_effort):
    """The deep-dive tip to flash after this turn, or ``None``.

    Only when the turn actually used the GitHub tools and effort is below
    "high". Returns ``None`` when the active model has no reasoning support
    (``reasoning_effort is None``), since the tip names a sidebar control that
    is then not on screen.
    """
    if not mcp_calls or reasoning_effort is None or reasoning_effort == "high":
        return None
    return GITHUB_EFFORT_HINT


def warning_message(entry) -> str:
    """One warning line for a document event (used by the log and the flash)."""
    if entry["kind"] == "flagged":
        return f"**{entry['name']}** was rejected by the safety scan" + (
            f": {entry['reason']}" if entry["reason"] else "."
        )
    if entry["kind"] == "overwrite":
        return (
            f"**{entry['name']}** replaced a previously ingested document "
            "with the same name."
        )
    if entry["kind"] == "github unavailable":
        return (
            "Couldn't reach the GitHub tools, so the interview continues "
            "without them"
            + (f": {entry['reason']}" if entry["reason"] else ".")
        )
    if entry["kind"] == "unverified code":
        # Backticks, not bold: a path like ``src/pkg/__init__.py`` rendered as
        # markdown emphasis loses its underscores and reads as a different file.
        return (
            f"The reply referred to `{entry['name']}`, which never appeared "
            "in anything the interviewer actually read from GitHub. Treat that "
            "part as possibly invented — ask it to re-read the file."
        )
    if entry["kind"] == "unquoted code":
        return (
            "The reply showed a code block starting `"
            f"{entry['name']}` that does not match any file read from GitHub. "
            "It may be the interviewer's own illustration — but if it was "
            "presented as your code, treat it as invented."
        )
    if entry["kind"] == "github listing blocked":
        return (
            f"A repository listing for `{entry['name']}` was withheld by the "
            "safety scan"
            + (f": {entry['reason']}" if entry["reason"] else ".")
            + " File and folder names are read by the interviewer too."
        )
    if entry["kind"] == "github file blocked":
        return (
            f"`{entry['name']}` was withheld by the safety scan"
            + (f": {entry['reason']}" if entry["reason"] else ".")
        )
    if entry["kind"] == "retrieval":
        return (
            "Document retrieval failed — this reply was generated **without** "
            "document context. Your documents are still indexed; the next turn "
            "will try again."
        )
    if entry["kind"] == "evaluation":
        return (
            "The interviewer gave answer feedback without recording an "
            "evaluation card — this answer is missing from the Evaluations "
            "tab."
        )
    if entry["kind"] == "condense":
        return (
            "Couldn't rephrase your message into a search query — retrieved "
            "using your message as-is, which may match your documents less well."
        )
    if entry["kind"] == "kb_retrieval":
        return (
            "Knowledge-base retrieval failed — this reply was generated "
            "**without** the curated interview guidance. The next turn will "
            "try again."
        )
    if entry["kind"] == "knowledgebase":
        return (
            "The knowledge base couldn't be loaded — replies this session "
            "won't draw on the curated interview guidance"
            + (f" ({entry['reason']})." if entry["reason"] else ".")
        )
    return (
        f"**{entry['name']}** was not accepted because it couldn't be "
        f"processed ({entry['reason']}). You can try uploading it again."
    )


def render_warnings_log(entries) -> None:
    """The accumulated document-warnings log for the Warnings tab, newest first."""
    if not entries:
        st.caption("No warnings this session.")
        return
    for entry in reversed(entries):
        st.warning(f"⚠️ {entry['time']} — {warning_message(entry)}")


def render_documents_panel(docs, index) -> None:
    """Ingestion panel: one re-typeable, removable row per ingested document."""
    st.subheader("Ingested Documents")
    if not docs:
        st.caption("No documents ingested yet.")
    for doc in list(docs):
        name_col, rm_col = st.columns([5, 1])
        name_col.text(doc.name)
        if rm_col.button("✕", key=f"doc_rm_{doc.name}", help=f"Remove {doc.name}"):
            index.remove_document(doc.name)
            docs.remove(doc)
            st.rerun()
        new_type = st.selectbox(
            f"Type of {doc.name}",
            options=DOCUMENT_TYPES,
            index=DOCUMENT_TYPES.index(doc.doc_type),
            key=f"doc_type_{doc.name}",
            label_visibility="collapsed",
        )
        if new_type != doc.doc_type:
            doc.doc_type = new_type
            index.set_doc_type(doc.name, new_type)
        st.caption(
            f"{doc.n_chunks} chunk(s), ~{doc.token_estimate:,} tokens"
        )


def render_retrieval_panel(last_retrieval, last_query="") -> None:
    """Show the query and chunks used for the most recent retrieval."""
    with st.expander("Last Retrieval", expanded=False):
        if last_query:
            st.caption("Search query (rewritten from the message):")
            st.code(last_query, language="text")
        if not last_retrieval:
            st.caption("No retrieval has run yet.")
        for chunk in last_retrieval:
            topic = getattr(chunk, "topic", "")
            st.markdown(
                f"**{chunk.doc_type}{f' — {topic}' if topic else ''}: "
                f"{chunk.source}** "
                f"(part {chunk.chunk_id + 1}, score {chunk.score:.3f})"
            )
            st.code(chunk.text, language="markdown")


def render_tool_calls_panel(last_tool_calls) -> None:
    """Show the tools the model chose to call on the most recent turn."""
    with st.expander("Last Tool Calls", expanded=False):
        if not last_tool_calls:
            st.caption("The model has not called a tool yet.")
        for call in last_tool_calls:
            st.markdown(f"**{call['name']}**")
            st.code(call["arguments"] or "{}", language="json")
            st.caption("returned:")
            st.code(call["result"], language="markdown")


def render_web_sources_panel(citations) -> None:
    """List the web sources behind the most recent research call.

    The in-reply citation links depend on the model following its citation
    instructions; this panel is the reliable fallback, rendered straight from
    the provider's annotations.
    """
    with st.expander("Last Web Sources", expanded=False):
        if not citations:
            st.caption("No web research has run yet.")
        for cite in citations:
            title = cite.title or cite.url
            st.markdown(f"[{title}]({cite.url})")
            if cite.content:
                excerpt = cite.content[:300]
                if len(cite.content) > 300:
                    excerpt += "…"
                st.caption(excerpt)


def render_knowledgebase_panel(kb) -> None:
    """Summarize the persistent knowledge base (Developer tab)."""
    with st.expander("Knowledge Base", expanded=False):
        if kb is None:
            st.caption("Knowledge base unavailable this session (see Warnings).")
            return
        docs = kb.documents()
        if not docs:
            st.caption("The knowledge base is empty — add seeds to knowledgebase/.")
            return
        for doc in docs:
            st.markdown(f"**{doc.name}** — {doc.category}")
            st.caption(f"{doc.n_chunks} chunk(s)")


def summarize_cards(cards):
    """Per-dimension mean scores across the cards, in rubric order.

    Computed in Python, deliberately never asked of the model — an aggregate
    the model states can disagree with its own per-answer cards; an aggregate
    we compute cannot. Returns {} for no cards.
    """
    if not cards:
        return {}
    return {
        dimension: sum(c["scores"][dimension] for c in cards) / len(cards)
        for dimension in EVALUATION_DIMENSIONS
    }


def _scores_line(scores, fmt="{:.0f}") -> str:
    """One compact ``Rel 4 · Str 3 · …`` line from a scores mapping.

    Labels are the first three letters of each dimension name — compact
    enough for the narrow sidebar, and derived so a new dimension needs no
    extra bookkeeping.
    """
    return " · ".join(
        f"{d[:3].title()} {fmt.format(scores[d])}" for d in EVALUATION_DIMENSIONS
    )


def render_evaluations_tab(cards) -> None:
    """The Evaluations tab: computed summary on top, then one card per answer.

    Cards render newest first (like the Warnings log). Scores stay always
    visible; the verbal feedback folds into a collapsed expander so several
    cards can be scanned at once.
    """
    if not cards:
        st.caption(
            "No evaluations yet — cards appear here once the mock interview "
            "starts and answers get scored."
        )
        return
    means = summarize_cards(cards)
    st.subheader(f"Interview so far ({len(cards)} answer(s))")
    st.markdown(_scores_line(means, fmt="{:.1f}"))
    st.divider()
    for number, card in reversed(list(enumerate(cards, start=1))):
        average = sum(card["scores"].values()) / len(card["scores"])
        st.markdown(f"**Q{number} · {card['question_type']} · avg {average:.1f}**")
        st.caption(card["question"])
        st.markdown(_scores_line(card["scores"]))
        if card["verbal_feedback"]:
            with st.expander("Feedback", expanded=False):
                st.markdown(card["verbal_feedback"])
        st.divider()


def render_spend_metrics(spend: ChatSpend) -> None:
    """The two spend figures (total + next-prompt estimate); no pricing line."""
    total_col, next_col = st.columns(2)
    total_col.metric("Total this chat", format_spend(spend.total_cost))
    next_col.metric(
        "Est. next prompt",
        "N/A" if spend.next_estimate is None else format_spend(spend.next_estimate),
    )


def render_context_bar(usage: ContextUsage) -> None:
    """Compact context-window usage gauge for the Interview tab (label + bar).

    The full model-context breakdown (model, window, source, percentages) lives
    in the Developer tab; this is only the gauge, labelled above.
    """
    st.caption("Context window usage")
    st.progress(usage.progress)


def render_sidebar(
    library: PromptLibrary,
    usage: ContextUsage,
    spend: ChatSpend,
) -> None:
    """Render the model-context, spend, and prompt-config sections."""
    st.subheader("Model Context")
    st.text(f"Model: {usage.model}")
    st.text(f"Context window: {usage.window_label}")
    st.caption(f"Context source: {usage.source}")

    used_percentage = usage.used_percentage
    if used_percentage is not None:
        st.progress(usage.progress)
        st.caption(
            f"Estimated used: {used_percentage:.1f}% "
            f"({usage.used_tokens:,}/{usage.window:,} tokens)"
        )
    else:
        st.caption(
            f"Estimated used: unknown ({usage.used_tokens:,} tokens so far)"
        )

    st.subheader("Spend")
    render_spend_metrics(spend)
    pricing = spend.pricing
    if pricing.is_known:
        st.caption(
            f"{pricing.model} rates: "
            f"\\${pricing.prompt_price * 1_000_000:,.2f} / 1M input tokens, "
            f"\\${pricing.completion_price * 1_000_000:,.2f} / 1M output tokens"
        )
    else:
        st.caption(f"Pricing unavailable for {pricing.model}")

    st.subheader("Prompt Config")
    if library.source is not None:
        kind = "folder" if library.source.is_directory else "file"
        st.caption(
            f"Active source: **{library.source.label}** ({kind}) — "
            f"{len(library.files)} markdown file(s), in this order:"
        )
    else:
        st.caption("Markdown prompt files used by the chatbot")
    if library.is_grounding_aware:
        st.caption(
            "✅ Grounding-aware: retrieved document context is injected "
            "into this prompt."
        )
    for prompt_file in library.files:
        status = "found" if prompt_file.exists else "missing"
        st.text(f"- {prompt_file.name} ({status})")

    with st.expander("Prompt Preview", expanded=False):
        for prompt_file in library.files:
            st.markdown(f"**{prompt_file.name}**")
            if prompt_file.content:
                st.code(library.preview(prompt_file), language="markdown")
            elif prompt_file.exists:
                st.code("(empty file)", language="markdown")
            else:
                st.warning("File not found")


def render_history(messages) -> None:
    """Replay the stored chat history on rerun."""
    for entry in messages:
        with st.chat_message(entry["role"]):
            st.markdown(entry["content"])
            if entry.get("guard_unavailable"):
                st.caption(
                    "⚠️ Safety check was unavailable — this prompt wasn't screened."
                )
