"""Streamlit entry point for the interview-prep chatbot.

Run with:  uv run streamlit run chat_bot.py

This module only wires together the pieces in the ``interview_prep`` package
and drives the Streamlit chat loop.
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime

import streamlit as st
from openai import APIError, AuthenticationError
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

from interview_prep.config import (
    ASSUMED_REPLY_TOKENS,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    EMBEDDING_MODELS,
    GUARDRAIL_DOC_MODEL,
    KNOWLEDGEBASE_DB_PATH,
    KNOWLEDGEBASE_DIR,
    REASONING_EFFORTS,
    load_api_key,
    load_github_pat,
)
from interview_prep.context import (
    compute_context_usage,
    estimate_prompt_tokens,
    estimate_text_tokens,
    model_supports_reasoning,
    predict_next_call_tokens,
)
from interview_prep.github_mcp import (
    GitHubMCP,
    unquoted_code_blocks,
    unverified_references,
)
from interview_prep.grounding import ground_turn
from interview_prep.guardrails import JailbreakGuard
from interview_prep.ingest import (
    DocumentParseError,
    IngestedDocument,
    infer_doc_type,
    parse_document,
    should_ingest,
)
from interview_prep.knowledgebase import KnowledgeBase
from interview_prep.agent import InterviewAgent
from interview_prep.pricing import ChatSpend, get_model_pricing, turn_cost
from interview_prep.prompts import (
    PromptLibrary,
    default_source,
    discover_sources,
)
from interview_prep.query_rewrite import QueryCondenser
from interview_prep.retrieval import DocumentIndex
from interview_prep.authorization import ANONYMOUS, authorize
from interview_prep.ledger_postgres import PostgresLedger
from interview_prep.permissions import Permission, has
from interview_prep.spend import (
    BufferedLedger,
    Budget,
    LedgerUnavailable,
    OverBudget,
    check_budget,
)
from interview_prep.policy import ContentPolicy
from interview_prep.tools import build_tools, looks_like_feedback
from interview_prep.web_research import WebResearcher
from interview_prep.ui import (
    warning_message,
    github_effort_hint,
    render_api_key_input,
    render_context_bar,
    render_document_uploader,
    render_documents_panel,
    render_embedding_selector,
    render_evaluations_tab,
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


ROLE_TABLE_KEY = "roles"
SPEND_KEY = "spend"
#: Where this run's `BufferedLedger` waits for `main`'s flush.
SPEND_BUFFER_KEY = "_spend_buffer"


class MissingLedger:
    """The store for a deployment that configured none.

    Not an uncapped app. R22.12 fails closed on a ledger that cannot be read,
    and a `[spend]` block that was never written is a ledger that cannot be
    read — the same refusal an unusable `[roles]` table already produces
    (R21.10), for the same reason: a deployment serving nobody is fixed in
    minutes, while one silently uncapping every account produces only a bill.

    It is a *ledger*, not a special case in the policy, so the page has exactly
    one refusal path to render and `spend.py` never learns that Streamlit
    secrets exist.
    """

    def total(self, email):
        raise LedgerUnavailable(
            "no [spend] block in secrets.toml, so spending cannot be counted"
        )

    def record(self, email, amount):
        raise LedgerUnavailable("no [spend] block in secrets.toml")


def spend_settings():
    """The operator's `[spend]` configuration, or None (R22.16)."""
    try:
        return st.secrets[SPEND_KEY]
    except Exception:
        return None


def current_budget(estimate):
    """The budget port's adapter: secrets in, one turn's `Budget` out.

    Built fresh every run rather than cached in session state, deliberately.
    The construction opens no connection, the allowlist is already re-read per
    run for the same reason (R21.7), and an operator who lowers the cap or
    fixes a connection string should see it take effect on the next
    interaction rather than after a restart.
    """
    settings = spend_settings()
    try:
        store = PostgresLedger(settings["connection_string"])
    except Exception:
        # Absent, unreadable, or missing its connection string — all the same
        # fact to a user, and all fail closed.
        store = MissingLedger()
        cap = 0.0
    else:
        try:
            cap = float(settings["cap_usd"])
        except Exception:
            # A cap that cannot be read is a cap of nothing: it refuses every
            # capped role and leaves `dev` (R22.9) able to sign in and fix it.
            cap = 0.0
    # Buffered, and rebuilt every run so no read outlives the turn it was made
    # for. Without this every paid client's guard is its own round trip and
    # every recorded cost is another — five or so per chat turn, and one per
    # *window* on a document scan, which measured at roughly 550 connections
    # for a 2 MB upload. A free-tier pooler refuses long before that, and
    # because the ledger fails closed (R22.12) the refusal takes the whole app
    # down rather than merely slowing it. `main` flushes it in a `finally`.
    ledger = BufferedLedger(store)
    st.session_state[SPEND_BUFFER_KEY] = ledger
    return Budget(ledger=ledger, cap=cap, estimate=estimate)


def render_over_budget(decision) -> None:
    """R22.13: told no, told which no, and told whose problem it is.

    The two refusals are not interchangeable. "You have spent your budget" is
    about the user and is final; "the ledger is unreachable" is a deployment
    fault the user can neither cause nor fix, and telling them they overspent
    would send them to argue with the wrong person about a number that is not
    theirs.
    """
    if getattr(decision, "reason", None) == "unpriced":
        st.error(
            "⚠️ This app cannot read the price of the model it would use, so "
            "it is refusing to spend on something it cannot cost. **This is "
            "temporary and is not a limit you have reached** — try again in a "
            "few minutes."
        )
        return
    if getattr(decision, "reason", None) == "ledger_unavailable":
        st.error(
            "⚠️ This app cannot reach the ledger it records spending in, so it "
            "is refusing to spend anything. **This is a deployment fault, not "
            "a problem with your account** — nothing you have done has used up "
            "a budget. Please tell the operator."
        )
        return
    st.error(
        "⚠️ You have reached the spend limit for this account, so no further "
        "interview turns can be taken. Your transcript above is unaffected. "
        "Ask the operator if you need more."
    )


def role_table():
    """The operator's allowlist: authorized email -> role name (R21.7).

    Read from Streamlit secrets, which is where a Cloud deployment sets it and
    where a local `.streamlit/secrets.toml` sets it too. Absence is not an
    error here — it is a refusal, decided by `authorize` rather than by this
    reader, so that "no table" and "table that authorizes nobody" take exactly
    the same path (R21.10).
    """
    try:
        return st.secrets[ROLE_TABLE_KEY]
    except Exception:
        return None


def user_claim(user, name):
    """One claim off `st.user`, whichever access shape it presents."""
    try:
        if hasattr(user, "get"):
            return user.get(name)
        return getattr(user, name, None)
    except Exception:
        return None


def signed_in(user):
    """Whether Streamlit has an authenticated session for this browser (R21.2).

    Separate from *authorized* on purpose, and the gate needs both: a visitor
    who is not signed in must be sent to `st.login()`, while one who is signed
    in but refused must not be — `st.login()` redirects unconditionally, so
    sending an already-authenticated user there loops forever instead of
    showing a message.

    This deliberately does **not** re-check the ID token's `exp` claim, and an
    earlier version's doing so was a category error worth recording. An ID
    token is not a session: it is a one-time signed assertion that the person
    proved their identity at `iat`, and `exp` bounds how long a relying party
    should accept it *as proof of a fresh login*. The standard flow — which
    Streamlit already implements — verifies it once at the OAuth callback and
    then mints its own session, `_streamlit_user`, a signed cookie with
    `Max-Age` of 30 days. `st.user` reads that cookie once at session start, so
    `exp` is frozen at login and never refreshes. Re-checking it every run
    therefore converted Google's ~1-hour token lifetime into a hard 1-hour cap
    that dropped a candidate's transcript, documents and evaluations mid-
    interview, which no service behaves like.

    What that check was reaching for is revocation, and `role_table()` already
    provides it, immediately and better: the allowlist is re-read from secrets
    on *every* run (R21.7, R20.9), so removing an address locks that person out
    on their next message regardless of any cookie. Accepted trade-off: a
    stolen browser session authenticates for up to 30 days without touching
    Google. Bounding that needs an absolute session age from `iat`, a
    deliberate policy rather than a side effect of a token lifetime, and is
    deferred (R21.20).
    """
    return bool(getattr(user, "is_logged_in", False))


def current_identity():
    """The identity port's adapter (R21.15) — one seam, now a real one.

    Reads the authenticated session rather than an environment variable. The
    port's shape is unchanged, which is the whole point of having had one:
    everything downstream still asks a pure function a question.

    Deliberately *not* consulted: the query string and `session_state`. R20.8
    still holds — anything the browser can influence is not an identity, and
    that is more true now that the answer decides who spends the operator's
    money.
    """
    user = getattr(st, "user", None)
    if not signed_in(user):
        return ANONYMOUS
    # `email_verified` is required, not decorative (R21.3). Without it the
    # allowlist checks an address the signer never confirmed, and on any IdP
    # that lets a user self-assert one at registration a stranger can be
    # issued a validly signed token *as* an allowlisted person. Normalising the
    # provider's spelling of the claim is this adapter's job; `authorize`
    # demands the decided boolean and will not guess.
    return authorize(
        user_claim(user, "email"),
        table=role_table(),
        email_verified=user_claim(user, "email_verified") is True,
    )


def sign_in() -> None:
    """Start the OIDC flow. Only ever from a click — never from a script run."""
    try:
        st.login()
    except Exception as exc:
        # A missing `[auth]` block or an absent Authlib is a deployment fault,
        # not a user error, and saying so beats an unexplained traceback.
        st.session_state["sign_in_error"] = str(exc)


def render_sign_in() -> None:
    """The whole app, for a visitor who has not signed in (R21.2).

    `st.login()` is behind a button, and must stay there. Calling it in the
    script body — as the first version did — redirects on every anonymous run,
    which breaks three things at once:

    * **Sign-out cannot work.** `st.logout()` enqueues a redirect to
      `/auth/logout`, the same run then falls through to here and enqueues a
      second redirect to `/auth/login`, and the browser applies the last one.
      The provider still holds its own session, so the person is signed
      straight back in. The Sign-out button that R21.21 exists for was
      unusable, on this page and on the not-authorized page both.
    * **The copy below is never read**, because the page navigates away before
      it paints.
    * **Cancelling at the provider loops.** The bounce back to `/` immediately
      redirects to the provider again, with no state in which to stop.

    Streamlit's own documented pattern puts `st.login()` behind a widget for
    exactly this reason. Found by the WP2 code-review, not by the tests: the
    sign-out test stubs `st.logout` with a no-op, so the follow-on rerun never
    reached this function.
    """
    st.title("Interview Prep")
    st.write(
        "This app runs mock job interviews. Sign in to continue — access is "
        "limited to accounts the operator has authorized."
    )
    st.button("Sign in with Google", type="primary", on_click=sign_in)
    error = st.session_state.get("sign_in_error")
    if error:
        st.error(
            "Sign-in is not configured on this deployment, so it cannot be "
            f"used yet. ({error})"
        )


def render_not_authorized(identity) -> None:
    """R21.13: refused, told why, told which account, offered a way out.

    Two refusals with different causes and different fixers, so they get
    different words. "Not on the allowlist" is for the operator to fix in
    secrets. "Provider did not assert verification" cannot be fixed there at
    all — the allowlist is working correctly and the provider is the problem —
    and the first version of this page sent the operator to edit `[roles]`
    anyway, where nothing they did would help.
    """
    st.title("Not authorized")
    if getattr(identity, "refusal", None) == "unverified":
        st.write(
            "You are signed in, but the identity provider did not confirm that "
            "this address is verified, so it cannot be matched against the "
            "authorized list. **This is a deployment setting, not a problem "
            "with your account** — adding the address to the list will not "
            "change it."
        )
        st.caption(
            "This app requires the `email_verified` claim, because an "
            "unverified address proves nothing about who owns it. Google "
            "provides it; some providers (Microsoft Entra ID among them) do "
            "not send it at all."
        )
    else:
        st.write(
            "You are signed in, but this account is not on the authorized list "
            "for this app. If you believe it should be, ask the operator to add "
            "the address below."
        )
    st.code(identity.email or "(no email address on this account)")
    st.write("Signed in with the wrong account? Sign out and try another.")
    st.button("Sign out", on_click=st.logout)


def in_script_thread(callback):
    """Let a callback touch Streamlit from a worker thread.

    ``create_agent`` runs its model and tool nodes on a ThreadPoolExecutor, and
    Streamlit's script context is thread-local: an ``st.*`` call from one of
    those threads raises ``NoSessionContext``. Observed live — every GitHub
    tool call came back to the model as "failed after 3 attempts", and the
    status line never moved, because painting the progress slot was what threw.

    Progress no longer needs this: tools emit it through
    ``runtime.stream_writer`` and it is rendered from the stream, on this
    thread. Warnings and document registrations still do, because the policy
    middleware raises them from inside ``wrap_tool_call`` — on the worker
    thread. Routing those through the stream too would remove the last of this,
    and is the obvious next simplification.
    """
    ctx = get_script_run_ctx()

    def wrapper(*args, **kwargs):
        add_script_run_ctx(threading.current_thread(), ctx)
        return callback(*args, **kwargs)

    return wrapper


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


def sync_documents(api_key, container, identity, budget) -> DocumentIndex:
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
    # Same one-shot mechanism as flash_warnings, for guidance rather than
    # failure (currently the deep-dive reasoning-effort tip, shown once).
    st.session_state.setdefault("flash_notices", [])
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
    previous = st.session_state.get("doc_index")
    index = previous
    if index is None or index.embedding_model != embedding_model:
        index = DocumentIndex(
            api_key=api_key,
            embedding_model=embedding_model,
            identity=identity,
            budget=budget,
        )
        try:
            if docs:
                with st.spinner("Re-embedding documents with the new model…"):
                    for doc in docs:
                        index.add_document(doc)
        except OverBudget as exc:
            # The one `add_document` call that was not wrapped. Switching the
            # model re-embeds the whole corpus (R15.5) and the cap can be
            # reached partway through, which leaves an index holding some
            # documents and not others — retrieval over that is quietly worse
            # rather than broken, which is the expensive kind of wrong. So the
            # switch is abandoned rather than half-applied: the selector goes
            # back to the model whose index is still intact, and the user is
            # told why the model they picked did not take.
            record_warning("embedding model", "budget", str(exc))
            if previous is not None:
                st.session_state["embedding_model"] = previous.embedding_model
                index = previous
            else:
                # No intact index to fall back to (nothing was embedded before
                # this switch). Keep the partial one rather than dropping the
                # documents entirely; the warning is what tells the user the
                # corpus is incomplete.
                st.session_state["doc_index"] = index
        else:
            st.session_state["doc_index"] = index
    # Handed *this* turn's budget, every run. The index lives in session state
    # and is rebuilt only when the embedding model changes, so the `Budget` it
    # was constructed with carries the estimate of the turn it was born in —
    # and from turn two onward the cap would be checked against a figure for a
    # prompt the user has already sent (R22.6). Invisible when wrong: no error,
    # no warning, just a check asking about the wrong turn.
    index.budget = budget

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
            guard = JailbreakGuard(
                api_key=api_key,
                model=GUARDRAIL_DOC_MODEL,
                identity=identity,
                budget=budget,
            )
        try:
            with st.spinner(f"Scanning {file.name}…"):
                verdict = guard.check_document(text)
        except OverBudget as exc:
            # Scanning and indexing both spend, so a refusal here is ordinary
            # rather than exceptional — the cap can be reached mid-session, and
            # a pricing outage refuses a scan outright (R22.7). The file is
            # un-remembered so that retrying after a top-up, or after the
            # catalog comes back, actually re-processes it instead of silently
            # skipping a file the user believes was accepted.
            st.session_state["ingested_file_ids"].discard(file.file_id)
            record_warning(file.name, "budget", str(exc))
            continue
        if not should_ingest(verdict):
            if not verdict.allowed:
                record_warning(file.name, "flagged", verdict.reason)
            else:
                record_warning(file.name, "error", "the safety scan could not complete")
            continue
        doc = IngestedDocument(
            name=file.name, doc_type=infer_doc_type(file.name), text=text
        )
        try:
            with st.spinner(f"Indexing {file.name}…"):
                index.add_document(doc)
        except OverBudget as exc:
            st.session_state["ingested_file_ids"].discard(file.file_id)
            record_warning(file.name, "budget", str(exc))
            continue
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


def sync_knowledgebase(api_key, identity, budget):
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
            kb = KnowledgeBase(
                db_path=KNOWLEDGEBASE_DB_PATH,
                api_key=api_key,
                identity=identity,
                budget=budget,
            )
        if st.session_state.get("kb_synced_model") != model:
            with st.spinner("Syncing the knowledge base…"):
                kb.sync(KNOWLEDGEBASE_DIR, model)
            st.session_state["kb_synced_model"] = model
        st.session_state["knowledgebase"] = kb
        # Same reason as the document index: opened once per session, so the
        # current turn's budget has to be handed to it on every run.
        kb.budget = budget
    except OverBudget as exc:
        # Not latched, unlike every other failure here. `kb_failed` disables
        # the knowledge base for the rest of the session and is only cleared by
        # an embedding-model switch, which is right for a broken database and
        # wrong for a refusal that a top-up — or the next turn — undoes.
        record_warning("", "knowledgebase", str(exc))
        return None
    except Exception as exc:
        st.session_state["kb_failed"] = model
        record_warning("", "knowledgebase", str(exc))
        return None
    return kb


def _run() -> None:
    # Authentication and authorization first, before a key is even read
    # (R21.2, R21.11). Every turn spends the operator's own credit, so an
    # unauthorized visitor must not reach a model selector, an uploader, or a
    # chat box — and `st.stop()` here means they reach none of them. The agent
    # refuses independently (R21.12); this exists so the refusal is legible,
    # not so it is enforced.
    identity = current_identity()
    if not signed_in(getattr(st, "user", None)):
        render_sign_in()
        st.stop()
    if not identity.is_authorized:
        # Signed in and refused — including a session with no email claim at
        # all, which `render_not_authorized` says so. Deliberately not the
        # sign-in page: that redirects, and redirecting an authenticated
        # visitor loops forever.
        render_not_authorized(identity)
        st.stop()

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
    st.session_state.setdefault("evaluation_cards", [])
    # Everything GitHub tools really returned this session; replies are checked
    # against it so an invented file or function can be flagged.
    st.session_state.setdefault("github_seen_terms", set())
    # Verbatim text of every repository file read, so quoted code can be
    # checked against what was actually fetched.
    st.session_state.setdefault("github_code_corpus", [])
    st.session_state.setdefault("github_hint_shown", False)
    st.session_state.setdefault("prompt_source_key", default_source(sources).key)
    # A stored key can go stale if a source is renamed/removed between runs;
    # reset it before the widget renders, since the selectbox requires its bound
    # value to be one of the current options.
    source_keys = {s.key for s in sources}
    if st.session_state["prompt_source_key"] not in source_keys:
        st.session_state["prompt_source_key"] = default_source(sources).key

    # Sidebar tabs: everything the interviewee touches lives in Interview; the
    # accumulated per-answer evaluation cards in Evaluations; diagnostics
    # (context, spend, prompt config, retrieval debug) in Developer; the
    # accumulated document-rejection log in Warnings.
    #
    # The last two are offered only to a role holding VIEW_DIAGNOSTICS (R20.10).
    # Not rendering them is presentation, not protection, and no operation
    # behind them is separately guarded today — none currently needs to be,
    # since they only display state. The one with a real side effect is the
    # embedding-model selector (switching it re-embeds every document, at
    # cost), which is protected only by not being drawn; when a capability
    # here needs a real guard it belongs in the operation, per R20.5. The
    # warnings a user can act on still flash in the chat body either way
    # (R20.6).
    # Who you are, before what you can do — account chrome sits at the top of
    # the sidebar, above the tabs, so it is the first thing visible and stays
    # visible whichever tab is open. Not in the main window: that is the
    # interview, and this would cost it vertical space permanently.
    #
    # One row, not three stacked elements. Stacked (caption, button, divider)
    # it pushed the tabs far enough down that the sidebar needed scrolling
    # before showing anything the person came for — chrome earning more space
    # than the content. The divider is gone for the same reason: its margins
    # cost more than the separation was worth.
    #
    # Sign-out belongs to being signed in, not to holding a permission
    # (R21.21): every authorized identity gets it, dev and user alike. The
    # address stays visible rather than hidden behind the button, because a
    # sign-out control with no account name is a coin flip once more than one
    # account is in play.
    account, action = st.sidebar.columns([2, 1], vertical_alignment="center")
    account.caption(f"{identity.email}")
    action.button("Sign out", on_click=st.logout, use_container_width=True)

    show_diagnostics = has(identity.role, Permission.VIEW_DIAGNOSTICS)
    tab_labels = ["Interview", "Evaluations"]
    if show_diagnostics:
        tab_labels += ["Developer", "Warnings"]
    sidebar_tabs = st.sidebar.tabs(tab_labels)
    interview_tab, evaluations_tab = sidebar_tabs[0], sidebar_tabs[1]
    dev_tab, warnings_tab = sidebar_tabs[2:4] if show_diagnostics else (None, None)

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

    # The cap, read fresh from the store on every run (R22.11) — an in-process
    # total would be a cache of a number another container may have moved.
    # Decided against the *next-prompt estimate*, not against the total, so the
    # turn that would cross the cap is the one refused (R22.6).
    estimate = spend.next_estimate
    if estimate is None and pricing.is_known:
        # R10.6: nothing has been measured yet, which is true of the first turn
        # of *every* session while the ledger's total is a lifetime one. Rather
        # than fall back to the policy's flat floor, price what is actually
        # known — the composed prompt and the history, at this model's rate —
        # so the figure follows the configured model instead of a constant
        # typed in beside it. Only when the rate is actually known: an
        # unreachable catalog prices everything at zero, and a zero estimate
        # would read as "this turn is free" rather than "we could not price
        # it", which is what the policy's floor is for.
        estimate = turn_cost(
            pricing,
            estimate_prompt_tokens(system_prompt, messages),
            ASSUMED_REPLY_TOKENS,
        )
    # Show the figure the cap was decided against, not the one that was
    # missing. R10.6 shows "N/A" before any completed exchange, which was
    # honest while nothing depended on it — but the cap now refuses turns on
    # the strength of the recomputed estimate above, and a user refused on a
    # number the sidebar declines to show cannot check the arithmetic or
    # understand why their next turn will not run (R22.22).
    spend = replace(spend, next_estimate=estimate)
    budget = current_budget(estimate)
    decision = check_budget(
        identity, ledger=budget.ledger, cap=budget.cap, estimate=budget.estimate
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
        render_spend_metrics(spend, decision)
        grounding_warning_slot = st.empty()

    # The uploader renders into the interview tab (below the above), then the
    # ingested panel renders right under it.
    # Refused before anything paid is reachable, and before the uploader:
    # ingesting a document scans and embeds it, which spends (R22.8 — the
    # refusal is hard, not a quieter version of the app). The transcript is
    # rendered first, because losing sight of the interview is not part of the
    # penalty for running out of budget.
    if not decision.allowed:
        st.title("Interview preparation Chatbot")
        render_history(messages)
        render_over_budget(decision)
        st.stop()

    doc_index = sync_documents(api_key, interview_tab, identity, budget)
    kb = sync_knowledgebase(api_key, identity, budget)
    if st.session_state["ingested_docs"] and not library.is_grounding_aware:
        grounding_warning_slot.warning(
            "⚠️ This prompt source is not grounding-aware — uploaded "
            "documents will not be used."
        )
    with interview_tab:
        render_documents_panel(st.session_state["ingested_docs"], doc_index)
        render_web_sources_panel(st.session_state["web_sources"])

    with evaluations_tab:
        render_evaluations_tab(st.session_state["evaluation_cards"])

    if show_diagnostics:
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
    for message in st.session_state["flash_notices"]:
        st.info(message)
    st.session_state["flash_notices"] = []

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
        # Grounding policy — which query is searched, and what a failing source
        # costs the turn — lives in `interview_prep.grounding` so it can be
        # tested without a browser (tests/test_grounding.py). What stays here is
        # presentation: the spinners, and writing the result to session state.
        def condense_with_spinner(text, history):
            with st.spinner("Rephrasing your question for search…"):
                return QueryCondenser(
                    api_key=api_key, identity=identity, budget=budget
                ).condense(text, history)

        # A budget that ran out between the page's check and this call (a
        # second tab, a second container) refuses here instead of degrading the
        # turn to an ungrounded one — R22.8 makes the refusal hard, and a
        # quietly worse answer is the downgrade it forbids.
        try:
            with st.spinner("Retrieving document excerpts…"):
                grounded = ground_turn(
                    prompt=prompt,
                    history=messages,
                    system_prompt=system_prompt,
                    is_grounding_aware=library.is_grounding_aware,
                    doc_index=doc_index,
                    kb=kb,
                    embedding_model=st.session_state["embedding_model"],
                    condense=condense_with_spinner,
                    warn=record_warning,
                )
        except OverBudget as exc:
            render_over_budget(exc.decision)
            st.stop()
        effective_prompt = grounded.effective_prompt
        # A `None` query means nothing was searched, so the Developer tab keeps
        # showing the last real retrieval rather than being blanked by a turn
        # that never looked anything up.
        if grounded.query is not None:
            st.session_state["last_query"] = grounded.query
            st.session_state["last_retrieval"] = grounded.retrieved

        guard = JailbreakGuard(api_key=api_key, identity=identity, budget=budget)
        llm = InterviewAgent(
            api_key=api_key,
            model=model,
            reasoning_effort=reasoning_effort,
            identity=identity,
            budget=budget,
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
            tools, policy, mcp_names = (), None, set()
            if library.is_grounding_aware:

                def show_progress(message):
                    # A research hop streams no tokens for many seconds; keep
                    # the slot alive with a status line appended to whatever
                    # has already streamed. Called from the stream loop below,
                    # on this thread — tools emit progress through
                    # runtime.stream_writer rather than touching Streamlit.
                    streamed = "".join(reply_parts)
                    slot.markdown(f"{streamed}\n\n*{message}*" if streamed else f"*{message}*")

                def register_document(doc):
                    # Mirror sync_documents' replace-on-same-name semantics so
                    # the Documents panel and embedding-switch re-embeds see
                    # research documents like any upload.
                    docs = st.session_state["ingested_docs"]
                    docs[:] = [d for d in docs if d.name != doc.name]
                    docs.append(doc)

                # GitHub portfolio tools (MCP) join the same tool list. The
                # tools/list discovery round-trip runs once per session; its
                # converted schemas are cached in session state. A discovery
                # failure warns and caches an empty list, so the session
                # degrades to no GitHub tools without retrying every turn.
                github_mcp = None
                pat = load_github_pat()
                if pat:
                    if "github_mcp_specs" not in st.session_state:
                        show_progress("Connecting to the GitHub tools…")
                        try:
                            st.session_state["github_mcp_specs"] = GitHubMCP(
                                pat=pat
                            ).discover()
                        except Exception as exc:
                            record_warning("GitHub tools", "github unavailable", str(exc))
                            st.session_state["github_mcp_specs"] = []
                    cached_specs = st.session_state["github_mcp_specs"]
                    if cached_specs:
                        github_mcp = GitHubMCP(pat=pat, specs=cached_specs)

                policy = ContentPolicy(
                    guard=JailbreakGuard(
                        api_key=api_key,
                        model=GUARDRAIL_DOC_MODEL,
                        identity=identity,
                        budget=budget,
                    ),
                    index=doc_index,
                    # Raised from inside wrap_tool_call, i.e. off the script
                    # thread — see in_script_thread.
                    on_warning=in_script_thread(record_warning),
                    on_document=in_script_thread(register_document),
                )
                tools = build_tools(
                    researcher=WebResearcher(
                        api_key=api_key, identity=identity, budget=budget
                    ),
                    cache=st.session_state["web_research_cache"],
                    mcp=github_mcp,
                )
                mcp_names = github_mcp.tool_names if github_mcp else set()

            # The model reasons before it speaks, so the first token can take
            # many seconds; keep the slot alive until it lands (the first
            # streamed token repaints it).
            slot.markdown(
                f"*Reasoning with {reasoning_effort} effort…*"
                if reasoning_effort
                else "*Waiting for the model's reply…*"
            )

            with ThreadPoolExecutor(max_workers=1) as pool:
                guard_future = pool.submit(guard.check, prompt)
                try:
                    for token in llm.stream_reply(
                        effective_prompt,
                        pending,
                        tools=tools,
                        policy=policy,
                        mcp_names=mcp_names,
                        seen_terms=st.session_state["github_seen_terms"],
                        code_corpus=st.session_state["github_code_corpus"],
                        on_warning=in_script_thread(record_warning),
                        on_progress=show_progress if tools else None,
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
                except OverBudget as exc:
                    # The cap caught up with the turn mid-flight: the page
                    # checked before it started, and another tab or container
                    # spent the rest in between (R22.7's one-turn window).
                    slot.empty()
                    stream_failed = True
                    render_over_budget(exc.decision)
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

        # This turn's spend, whatever became of the turn (R22.4). Both
        # `st.stop()`s below end the run before the accounting block further
        # down, so anything recorded there is lost on exactly the two paths
        # where spend has already happened and produced no output. The ledger
        # is not at risk — every client records its own, the agent included,
        # from a `finally` — but the sidebar figure is, and a blocked turn
        # showing no cost is how a user concludes the guardrail is free.
        st.session_state["total_cost"] += (llm.turn_cost_usd or 0.0) + llm.extra_cost

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
        # Persist the answer, not the preamble: text a hop emitted before
        # calling a tool is status ("let me look that up") — and is where a
        # model sometimes writes raw tool-call JSON instead of prose. It was
        # painted live; repaint the slot so what stays matches what is stored.
        # ``answered`` rather than a truthiness check: a legitimately empty
        # final hop must not fall back to the raw stream, which is exactly the
        # preamble and typed-tool-call JSON this keeps out of the transcript.
        assistant_reply = (
            llm.answer_text if llm.answered else "".join(reply_parts)
        )
        if assistant_reply != "".join(reply_parts):
            slot.markdown(assistant_reply)
        st.session_state["last_tool_calls"] = llm.last_tool_calls

        # Fabrication check: a model that failed to read a repo has been
        # observed inventing plausible files and functions instead of saying
        # so. Anything the reply names that no tool result ever contained is
        # surfaced to the user (Warnings tab + a flash in the chat).
        for reference in unverified_references(
            assistant_reply, st.session_state["github_seen_terms"]
        ):
            record_warning(reference, "unverified code")
        # And code it quotes must appear verbatim in a file we really fetched.
        for preview in unquoted_code_blocks(
            assistant_reply, st.session_state["github_code_corpus"]
        ):
            record_warning(preview, "unquoted code")

        # A deep-dive is a multi-step tool workflow, and it degrades badly below
        # "high" effort — tell the user once, after a turn that actually used
        # the GitHub tools, so the advice arrives with the evidence for it.
        if tools and not st.session_state["github_hint_shown"]:
            hint = github_effort_hint(llm.mcp_calls, reasoning_effort)
            if hint:
                st.session_state["flash_notices"].append(hint)
                st.session_state["github_hint_shown"] = True
        if llm.citations:
            st.session_state["web_sources"] = list(llm.citations)
        # Evaluation cards commit only on an allowed, completed turn — the
        # turn's graph state accumulated them during the stream, but a turn the
        # guardrail blocks persists nothing, cards included.
        if tools:
            st.session_state["evaluation_cards"].extend(llm.evaluations)
            if looks_like_feedback(assistant_reply) and not llm.evaluations:
                # The reply reads like scored answer feedback, but no card was
                # recorded — the model skipped the record_evaluation call. The
                # user-facing text lives in ui.warning_message.
                record_warning("", "evaluation")
        # The whole R22.3 ladder — actual cost, else reported tokens, else
        # estimated ones — lives in the agent now, because it is the only
        # object on every exit path from a turn. The page's copy of it sat
        # below the two `st.stop()`s above, so a blocked turn priced at rungs
        # two or three recorded nothing at all (R22.18: what is left here is a
        # display of this chat's contribution to a durable total, never the
        # authority on it).

        # Record the reasoning tokens this turn spent so the next-prompt estimate
        # can account for them (they're billed as output but absent from content).
        # Also record the retrieved-context tokens this turn injected, so the
        # next-prompt estimate can account for them (the block is per-turn and
        # never part of the stored history).
        assistant_msg = {
            "role": "assistant",
            "content": assistant_reply,
            "reasoning_tokens": llm.last_reasoning_tokens or 0,
            "context_tokens": estimate_text_tokens(grounded.context_block),
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


def main() -> None:
    """Run one Streamlit script run, and always settle the spend it made.

    The flush is in a `finally` because the ordinary ways this script ends are
    exceptions: `st.stop()` on every refusal path and `st.rerun()` after a
    completed turn both raise, and a plain call at the end of `_run` would be
    reached by neither. Spend buffered during a run and never written is spend
    the next turn is decided without — under-counting, the expensive direction.

    A failed flush is not raised. By the time anything is pending the money is
    already gone, and killing the run would cost the user their answer without
    saving the operator a cent; `BufferedLedger` keeps the amount for the next
    attempt, and the store is read again before the next turn is allowed, so an
    outage still stops the spending within a turn (R22.12).
    """
    try:
        _run()
    finally:
        buffered = st.session_state.get(SPEND_BUFFER_KEY)
        if buffered is not None:
            buffered.flush()


if __name__ == "__main__":
    main()
