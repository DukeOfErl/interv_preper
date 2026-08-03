"""Streamlit entry point for the interview-prep chatbot.

Run with:  uv run streamlit run chat_bot.py

This module only wires together the pieces in the ``interview_prep`` package
and drives the Streamlit chat loop.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import streamlit as st
from openai import APIError, AuthenticationError

from interview_prep.config import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    EMBEDDING_MODELS,
    GUARDRAIL_DOC_MODEL,
    KNOWLEDGEBASE_DB_PATH,
    KNOWLEDGEBASE_DIR,
    QUERY_REWRITE_HISTORY_TURNS,
    REASONING_EFFORTS,
    load_api_key,
)
from interview_prep.context import (
    compute_context_usage,
    estimate_prompt_tokens,
    estimate_text_tokens,
    model_supports_reasoning,
    predict_next_call_tokens,
)
from interview_prep.guardrails import JailbreakGuard
from interview_prep.ingest import (
    DocumentParseError,
    IngestedDocument,
    infer_doc_type,
    parse_document,
    should_ingest,
)
from interview_prep.knowledgebase import KnowledgeBase
from interview_prep.llm import InterviewLLM
from interview_prep.pricing import ChatSpend, get_model_pricing, turn_cost
from interview_prep.prompts import (
    PromptLibrary,
    default_source,
    discover_sources,
)
from interview_prep.query_rewrite import QueryCondenser
from interview_prep.retrieval import (
    DocumentIndex,
    fill_retrieved_context,
    format_context_block,
)
from interview_prep.tools import ToolBox
from interview_prep.web_research import WebResearcher
from interview_prep.ui import (
    warning_message,
    render_api_key_input,
    render_context_bar,
    render_document_uploader,
    render_documents_panel,
    render_embedding_selector,
    render_history,
    render_knowledgebase_panel,
    render_prompt_selector,
    render_reasoning_selector,
    render_retrieval_panel,
    render_sidebar,
    render_spend_metrics,
    render_tool_calls_panel,
    render_warnings_log,
    render_web_sources_panel,
)


def record_warning(name, kind, reason=""):
    """Record a warning in both places: the permanent Warnings-tab log, and a
    one-shot flash shown in the chat window on the next run.

    Used for document events (rejections, overwrites) and per-turn knowledge
    failures (retrieval / query-rewrite falling open) — anything the user
    should transparently know went wrong while the app kept running.
    """
    entry = {
        "name": name,
        "kind": kind,
        "reason": reason,
        "time": datetime.now().strftime("%H:%M:%S"),
    }
    st.session_state["warnings_log"].append(entry)
    st.session_state["flash_warnings"].append(warning_message(entry))


def sync_documents(api_key, container) -> DocumentIndex:
    """Render the document widgets into ``container`` and sync the vector index.

    Handles the three session-level document events: an embedding-model switch
    (rebuild the index and re-embed every stored document from its raw text —
    vectors from different models are not comparable), new uploads (parse →
    guardrail scan → index, fail-closed per document), and reruns (files
    already processed are recognized by ``file_id`` and skipped).
    """
    st.session_state.setdefault("embedding_model", DEFAULT_EMBEDDING_MODEL)
    st.session_state.setdefault("ingested_docs", [])
    st.session_state.setdefault("ingested_file_ids", set())
    st.session_state.setdefault("warnings_log", [])
    st.session_state.setdefault("flash_warnings", [])
    st.session_state.setdefault("last_retrieval", [])
    st.session_state.setdefault("last_query", "")
    st.session_state.setdefault("doc_uploader_nonce", 0)

    # The nonce rotates the widget key after each processed batch, emptying the
    # uploader — otherwise a rejected file would keep sitting in its file list,
    # looking accepted.
    with container:
        uploaded = render_document_uploader(
            key=f"doc_uploader_{st.session_state['doc_uploader_nonce']}"
        )

    docs = st.session_state["ingested_docs"]
    embedding_model = st.session_state["embedding_model"]
    index = st.session_state.get("doc_index")
    if index is None or index.embedding_model != embedding_model:
        index = DocumentIndex(api_key=api_key, embedding_model=embedding_model)
        if docs:
            with st.spinner("Re-embedding documents with the new model…"):
                for doc in docs:
                    index.add_document(doc)
        st.session_state["doc_index"] = index

    new_files = [
        f
        for f in (uploaded or [])
        if f.file_id not in st.session_state["ingested_file_ids"]
    ]
    guard = None  # built lazily on the first document that reaches the scan
    for file in new_files:
        st.session_state["ingested_file_ids"].add(file.file_id)
        try:
            text = parse_document(file.name, file.getvalue())
        except DocumentParseError as exc:
            record_warning(file.name, "error", str(exc))
            continue
        # Unlike the per-turn chat guardrail, document screening fails CLOSED:
        # a document enters the index only after a successful, clean scan. The
        # chat itself stays available either way. Documents are scanned in
        # overlapping windows — the classifier misses an injected line diluted
        # by pages of benign text when given the whole document at once.
        if guard is None:
            # Documents get the mid-size scan model — off the latency-critical
            # path, and reliable at much bigger windows than the chat-turn nano.
            guard = JailbreakGuard(api_key=api_key, model=GUARDRAIL_DOC_MODEL)
        with st.spinner(f"Scanning {file.name}…"):
            verdict = guard.check_document(text)
        if not should_ingest(verdict):
            if not verdict.allowed:
                record_warning(file.name, "flagged", verdict.reason)
            else:
                record_warning(file.name, "error", "the safety scan could not complete")
            continue
        doc = IngestedDocument(
            name=file.name, doc_type=infer_doc_type(file.name), text=text
        )
        with st.spinner(f"Indexing {file.name}…"):
            index.add_document(doc)
        # Re-uploading a file with the same name replaces the earlier version
        # (add_document already replaced its chunks) — worth a warning, since
        # the user may not have intended to lose the previous one.
        if any(d.name == doc.name for d in docs):
            record_warning(doc.name, "overwrite")
        docs[:] = [d for d in docs if d.name != doc.name]
        docs.append(doc)
    if new_files:
        # Batch processed — empty the drop zone so accepted files show up only
        # in Ingested Documents and rejected ones only as warnings.
        st.session_state["doc_uploader_nonce"] += 1
        st.rerun()
    return index


def sync_knowledgebase(api_key):
    """Open the persistent knowledge base and reconcile it with the seeds.

    The content sync runs once per session; the embedding-coverage sync also
    reruns when the sidebar embedding model changes (cached per-model vectors
    make switching back to a previously used model free). Fails open: on any
    error the session simply runs without the knowledge base, with a warning
    logged. The failure latch is per embedding model — it stops rerun retry
    loops, but a model switch grants one fresh attempt, so a transient error
    is not a session-wide death sentence.
    """
    kb = st.session_state.get("knowledgebase")
    model = st.session_state["embedding_model"]
    if st.session_state.get("kb_failed") == model:
        return None
    try:
        if kb is None:
            kb = KnowledgeBase(db_path=KNOWLEDGEBASE_DB_PATH, api_key=api_key)
        if st.session_state.get("kb_synced_model") != model:
            with st.spinner("Syncing the knowledge base…"):
                kb.sync(KNOWLEDGEBASE_DIR, model)
            st.session_state["kb_synced_model"] = model
        st.session_state["knowledgebase"] = kb
    except Exception as exc:
        st.session_state["kb_failed"] = model
        record_warning("", "knowledgebase", str(exc))
        return None
    return kb


def main() -> None:
    # Prefer a key from the environment/.env; otherwise let the user paste one
    # into the sidebar (kept in session only). Fail fast until we have a key.
    env_key = load_api_key()
    key_from_env = bool(env_key)
    api_key = env_key or render_api_key_input()
    if not api_key:
        st.info(
            "Enter your OpenRouter API key in the sidebar to get started. "
            "Alternatively, set OPENROUTER_API_KEY in a .env file (see "
            ".env.example) or your environment and restart."
        )
        st.stop()

    sources = discover_sources()
    if not sources:
        st.error(
            "No prompt sources found in prompts/, so interview prep is not "
            "available."
        )
        st.stop()

    st.session_state.setdefault("openai_model", DEFAULT_MODEL)
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("total_cost", 0.0)
    st.session_state.setdefault("last_tool_calls", [])
    st.session_state.setdefault("web_sources", [])
    st.session_state.setdefault("web_research_cache", {})
    st.session_state.setdefault("prompt_source_key", default_source(sources).key)
    # A stored key can go stale if a source is renamed/removed between runs;
    # reset it before the widget renders, since the selectbox requires its bound
    # value to be one of the current options.
    source_keys = {s.key for s in sources}
    if st.session_state["prompt_source_key"] not in source_keys:
        st.session_state["prompt_source_key"] = default_source(sources).key

    # Three sidebar tabs: everything the interviewee touches lives in Interview;
    # diagnostics (context, spend, prompt config, retrieval debug) in Developer;
    # the accumulated document-rejection log in Warnings.
    interview_tab, dev_tab, warnings_tab = st.sidebar.tabs(
        ["Interview", "Developer", "Warnings"]
    )

    model = st.session_state["openai_model"]
    # Reasoning effort is only meaningful for reasoning models, so the selector
    # is shown (and the param sent) only when the active model supports it.
    supports_reasoning = model_supports_reasoning(model, api_key)
    if supports_reasoning:
        st.session_state.setdefault("reasoning_effort", DEFAULT_REASONING_EFFORT)

    # Resolve the selected source from session state (set via setdefault above);
    # the selectbox rendered below stays bound to the same key, so changing it
    # still reruns and is reflected on that run.
    selected = next(
        s for s in sources if s.key == st.session_state["prompt_source_key"]
    )
    library = PromptLibrary.from_source(selected)
    if library.is_empty:
        st.error(
            "The selected prompt source has no usable markdown content, so "
            "interview prep is not available."
        )
        st.stop()

    system_prompt = library.system_prompt
    messages = st.session_state["messages"]

    usage = compute_context_usage(model, api_key, system_prompt, messages)
    pricing = get_model_pricing(model, api_key)
    next_tokens = predict_next_call_tokens(system_prompt, messages)
    spend = ChatSpend(
        total_cost=st.session_state["total_cost"],
        pricing=pricing,
        next_estimate=(
            turn_cost(pricing, *next_tokens) if next_tokens is not None else None
        ),
    )

    # Interview tab, top → bottom: interviewer picker, effort selector just below
    # it, the context-window usage gauge, then the spend figures (no header — the
    # labels speak for themselves), then a slot the grounding warning fills once
    # document state is known. All of this sits above the document drop zone.
    reasoning_effort = None
    with interview_tab:
        render_prompt_selector(sources)
        if supports_reasoning:
            render_reasoning_selector(REASONING_EFFORTS)
            reasoning_effort = st.session_state["reasoning_effort"]
        render_context_bar(usage)
        render_spend_metrics(spend)
        grounding_warning_slot = st.empty()

    # The uploader renders into the interview tab (below the above), then the
    # ingested panel renders right under it.
    doc_index = sync_documents(api_key, interview_tab)
    kb = sync_knowledgebase(api_key)
    if st.session_state["ingested_docs"] and not library.is_grounding_aware:
        grounding_warning_slot.warning(
            "⚠️ This prompt source is not grounding-aware — uploaded "
            "documents will not be used."
        )
    with interview_tab:
        render_documents_panel(st.session_state["ingested_docs"], doc_index)
        render_web_sources_panel(st.session_state["web_sources"])

    with dev_tab:
        render_embedding_selector(EMBEDDING_MODELS)
        render_sidebar(library, usage, spend)
        render_retrieval_panel(
            st.session_state["last_retrieval"], st.session_state["last_query"]
        )
        render_tool_calls_panel(st.session_state["last_tool_calls"])
        render_knowledgebase_panel(kb)

    with warnings_tab:
        render_warnings_log(st.session_state["warnings_log"])

    st.title("Interview preparation Chatbot")

    render_history(messages)

    # Flash warnings render at the BOTTOM of the conversation — just above the
    # chat input, where the user's attention is — because a banner at the top
    # scrolls out of view once the chat grows. Shown exactly once: queued on the
    # run that failed (which ends in a rerun), displayed on the next run, then
    # cleared. The durable record lives in the Warnings tab.
    for message in st.session_state["flash_warnings"]:
        st.warning(f"⚠️ {message}")
    st.session_state["flash_warnings"] = []

    has_user_prompt = any(m.get("role") == "user" for m in messages)
    placeholder = (
        ""
        if has_user_prompt
        else "For best results paste the relevant job ad and resume/CV here"
    )

    # One pass per turn: show the prompt, then run the jailbreak guardrail on a
    # background thread *concurrently* with the interview stream. Tokens paint as
    # they arrive; if the guardrail returns a jailbreak verdict (whenever it
    # lands), the stream is stopped, its text cleared, and nothing is persisted.
    # Fails open, so a classifier outage never blocks legitimate prompts.
    if prompt := st.chat_input(placeholder, key="main_chat_input"):
        with st.chat_message("user"):
            st.markdown(prompt)

        # Ground the turn: retrieve the top-k chunks for this prompt and fill
        # the {retrieved_context} slot. Only grounding-aware sources have the
        # slot; other sources get the system prompt untouched. Retrieval errors
        # degrade to an ungrounded turn rather than blocking the chat.
        context_block = ""
        if library.is_grounding_aware:
            kb_ready = kb is not None and not kb.is_empty
            if not doc_index.is_empty or kb_ready:
                # On a follow-up, rewrite the message into a standalone query so
                # vector search isn't handed an anaphoric fragment ("that role").
                # The first turn has no referents to resolve — use it verbatim
                # and skip the extra call. Condensing fails open to the prompt.
                if messages:
                    with st.spinner("Rephrasing your question for search…"):
                        rewrite = QueryCondenser(api_key=api_key).condense(
                            prompt, messages[-QUERY_REWRITE_HISTORY_TURNS:]
                        )
                    query = rewrite.query
                    # Fail open, but tell the user we searched with the raw
                    # message instead of a rewritten query.
                    if rewrite.errored:
                        record_warning("", "condense")
                else:
                    query = prompt
                st.session_state["last_query"] = query
                # Both retrievals fail open independently: answer with whatever
                # context could be fetched, but say so.
                retrieved = []
                with st.spinner("Retrieving document excerpts…"):
                    if not doc_index.is_empty:
                        try:
                            retrieved += doc_index.retrieve(query)
                        except Exception as exc:
                            record_warning("", "retrieval", str(exc))
                    if kb_ready:
                        try:
                            retrieved += kb.retrieve(
                                query, st.session_state["embedding_model"]
                            )
                        except Exception as exc:
                            # Distinct kind: the "retrieval" copy talks about
                            # uploaded documents, which may not even exist on
                            # a KB-only grounded turn.
                            record_warning("", "kb_retrieval", str(exc))
                st.session_state["last_retrieval"] = retrieved
                context_block = format_context_block(retrieved)
            effective_prompt = fill_retrieved_context(system_prompt, context_block)
        else:
            effective_prompt = system_prompt

        guard = JailbreakGuard(api_key=api_key)
        llm = InterviewLLM(
            api_key=api_key, model=model, reasoning_effort=reasoning_effort
        )
        pending = messages + [{"role": "user", "content": prompt}]
        reply_parts = []
        verdict = None
        stream_failed = False

        with st.chat_message("assistant"):
            slot = st.empty()

            # Tools are offered only to grounding-aware sources: the research
            # tool indexes its raw excerpts for retrieval, which presumes the
            # RAG pipeline the source opted into.
            toolbox = None
            if library.is_grounding_aware:

                def show_progress(message):
                    # A research hop streams no tokens for many seconds; keep
                    # the slot alive with a status line appended to whatever
                    # has already streamed.
                    streamed = "".join(reply_parts)
                    slot.markdown(f"{streamed}\n\n*{message}*" if streamed else f"*{message}*")

                def register_document(doc):
                    # Mirror sync_documents' replace-on-same-name semantics so
                    # the Documents panel and embedding-switch re-embeds see
                    # research documents like any upload.
                    docs = st.session_state["ingested_docs"]
                    docs[:] = [d for d in docs if d.name != doc.name]
                    docs.append(doc)

                toolbox = ToolBox(
                    researcher=WebResearcher(api_key=api_key),
                    guard=JailbreakGuard(api_key=api_key, model=GUARDRAIL_DOC_MODEL),
                    index=doc_index,
                    cache=st.session_state["web_research_cache"],
                    on_warning=record_warning,
                    on_progress=show_progress,
                    on_document=register_document,
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                guard_future = pool.submit(guard.check, prompt)
                try:
                    for token in llm.stream_reply(
                        effective_prompt, pending, toolbox=toolbox
                    ):
                        reply_parts.append(token)
                        slot.markdown("".join(reply_parts))
                        # Poll (never block) the guardrail; bail the moment it
                        # comes back with a jailbreak verdict.
                        if guard_future.done():
                            verdict = guard_future.result()
                            if not verdict.allowed:
                                break
                    else:
                        # Stream finished on its own — now wait for the verdict
                        # before committing anything.
                        verdict = guard_future.result()
                except AuthenticationError:
                    slot.empty()
                    stream_failed = True
                    hint = (
                        "Enter a different OpenRouter API key in the sidebar and "
                        "try again."
                        if not key_from_env
                        else "Check the OPENROUTER_API_KEY in your environment/"
                        ".env, then restart the app."
                    )
                    st.warning(
                        f"⚠️ That API key didn't work (authentication failed). "
                        f"{hint}"
                    )
                except APIError as exc:
                    slot.empty()
                    stream_failed = True
                    detail = getattr(exc, "message", None) or str(exc)
                    st.warning(
                        "⚠️ The request to OpenRouter failed, so no reply was "
                        f"generated. Please try again. ({detail})"
                    )

        if stream_failed:
            st.stop()

        if verdict is not None and not verdict.allowed:
            slot.empty()
            st.warning(
                "⚠️ That prompt was blocked by the safety guardrail"
                + (f": {verdict.reason}" if verdict.reason else ".")
            )
            st.stop()

        # Allowed: accrue this turn's ACTUAL spend (OpenRouter's reported cost),
        # falling back to reported token counts × price, then rough estimates.
        assistant_reply = "".join(reply_parts)
        st.session_state["last_tool_calls"] = llm.last_tool_calls
        if toolbox is not None and toolbox.citations:
            st.session_state["web_sources"] = list(toolbox.citations)
        cost = llm.last_cost
        if cost is None:
            pricing_now = get_model_pricing(model, api_key)
            if llm.last_usage is not None:
                cost = turn_cost(
                    pricing_now,
                    llm.last_usage.prompt_tokens or 0,
                    llm.last_usage.completion_tokens or 0,
                )
            else:
                cost = turn_cost(
                    pricing_now,
                    estimate_prompt_tokens(effective_prompt, pending),
                    estimate_text_tokens(assistant_reply),
                )
        # Tool sub-completions (web research) bill separately from the main
        # stream's usage chunks; the toolbox accumulated their reported cost.
        if toolbox is not None:
            cost += toolbox.extra_cost
        st.session_state["total_cost"] += cost

        # Record the reasoning tokens this turn spent so the next-prompt estimate
        # can account for them (they're billed as output but absent from content).
        # Also record the retrieved-context tokens this turn injected, so the
        # next-prompt estimate can account for them (the block is per-turn and
        # never part of the stored history).
        assistant_msg = {
            "role": "assistant",
            "content": assistant_reply,
            "reasoning_tokens": llm.last_reasoning_tokens or 0,
            "context_tokens": estimate_text_tokens(context_block),
        }
        # The guardrail fails open on any classifier error; flag the turn so the
        # UI can note that this prompt went through unscreened.
        if verdict is not None and verdict.errored:
            assistant_msg["guard_unavailable"] = True
        messages.append({"role": "user", "content": prompt})
        messages.append(assistant_msg)
        # A single rerun refreshes the sidebar (context usage + spend) now that
        # the turn is complete — no mid-turn recompute needed.
        st.rerun()


if __name__ == "__main__":
    main()
