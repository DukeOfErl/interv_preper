"""The spend cap as a user meets it, in the real entry point (§ 22).

`test_spend_cap.py` proves the rule and `test_ledger_postgres.py` proves the
adapter. Neither imports `chat_bot.py`, so neither would notice the failures
that live only in the wiring:

  * the page reading the ledger but never acting on it — every assertion in
    those two files stays green while the chat box sits there, uncapped;
  * the two refusals collapsing into one message somewhere between the policy
    and the screen (R22.13), which tells a user they overspent on a day the
    database was down;
  * the gate refusing *everyone*, which is what a fail-closed control does when
    it is wrong, and which no "is it refused?" test can distinguish from
    working. That direction gets its own tests below.

Offline by construction, like `test_chat_bot_wiring.py`: the socket is refused
and the ledger is a fake injected in place of the Postgres adapter, so nothing
here needs a database, a credential or a network (R22.15).
"""

from __future__ import annotations

import pathlib

import pytest
import streamlit as st
from streamlit import config as st_config
from streamlit.testing.v1 import AppTest

from interview_prep.authorization import Identity
from interview_prep.permissions import Role
from interview_prep import context, ledger_postgres
from interview_prep.config import DEFAULT_EMBEDDING_MODEL
from interview_prep.spend import InMemoryLedger, LedgerUnavailable, check_budget

APP = pathlib.Path(__file__).resolve().parent.parent / "chat_bot.py"
UNUSABLE_KEY = "sk-not-a-real-key-for-tests"
CAP = 5.00
USER = "candidate@example.com"
DEV = "operator@example.com"


class FakeUser(dict):
    """A minimal stand-in for `st.user` (same technique as the wiring tests)."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


def logged_in(email):
    return FakeUser(is_logged_in=True, email=email, email_verified=True)


class DownLedger:
    """A store that is up, as far as the operator knows, and is not."""

    def total(self, email):
        raise LedgerUnavailable("connection refused")

    def record(self, email, amount):
        raise LedgerUnavailable("connection refused")


def write_secrets(tmp_path, roles, spend=True):
    lines = ["[roles]"] + [f'"{email}" = "{role}"' for email, role in roles.items()]
    if spend:
        lines += [
            "",
            "[spend]",
            'connection_string = "postgresql://example/postgres"',
            f"cap_usd = {CAP}",
        ]
    path = tmp_path / "secrets.toml"
    path.write_text("\n".join(lines) + "\n")
    st_config.set_option("secrets.files", [str(path)], where_defined="test")
    st.secrets._reset()
    return path


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """Neutralise the network, the developer's own secrets, and the catalog."""
    original = st_config.get_option("secrets.files")
    empty = tmp_path / "secrets.toml"
    empty.write_text("")
    st_config.set_option("secrets.files", [str(empty)], where_defined="test")
    st.secrets._reset()
    (tmp_path / ".env").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", UNUSABLE_KEY)
    monkeypatch.delenv("GITHUB_PAT", raising=False)

    def no_network(*args, **kwargs):
        raise OSError("network disabled in tests")

    monkeypatch.setattr("interview_prep.context.request.urlopen", no_network)
    context.fetch_openrouter_models.clear()

    yield monkeypatch

    st_config.set_option("secrets.files", original, where_defined="test")
    st.secrets._reset()


def run_app(monkeypatch, email, ledger, session_state=None):
    """Run the real entry point as `email`, with `ledger` behind the port.

    The adapter class is replaced on its own module, which is where
    `chat_bot.py` looks it up each time `AppTest` re-executes the script. The
    connection string in secrets is therefore never dialled.
    """
    monkeypatch.setattr(st, "user", logged_in(email))
    monkeypatch.setattr(
        ledger_postgres, "PostgresLedger", lambda connection_string: ledger
    )
    app = AppTest.from_file(str(APP), default_timeout=120)
    # The knowledge base embeds on sync, which would reach for the network;
    # its per-model failure latch skips it without touching the derived DB.
    app.session_state["embedding_model"] = DEFAULT_EMBEDDING_MODEL
    app.session_state["kb_failed"] = DEFAULT_EMBEDDING_MODEL
    for key, value in (session_state or {}).items():
        app.session_state[key] = value
    app.run()
    assert not app.exception, [str(e.value)[:300] for e in app.exception]
    return app


def errors(app):
    return " ".join(element.value for element in app.error)


def metrics(app):
    return {element.label: element.value for element in app.sidebar.metric}


def spent(email, amount):
    ledger = InMemoryLedger()
    ledger.record(email, amount)
    return ledger


# --- the gate opens: a user in credit is served -----------------------------
#
# First, and deliberately. Every refusal test below passes against a page that
# refuses everybody, and a spend cap that has locked out the whole userbase
# looks exactly like one that is working.


def test_a_user_in_credit_still_gets_the_chat(offline, tmp_path):
    write_secrets(tmp_path, {USER: "user"})
    app = run_app(offline, USER, spent(USER, 1.00))
    assert len(app.get("chat_input")) == 1
    assert not errors(app)


def test_a_user_in_credit_is_shown_what_is_left(offline, tmp_path):
    """R22.17. The figure is the cap minus what the *ledger* holds, not what
    this session happens to have spent — a returning user starts a new session
    with an empty `total_cost` and the same depleted budget.
    """
    write_secrets(tmp_path, {USER: "user"})
    app = run_app(offline, USER, spent(USER, 1.25))
    # 5.00 - 1.25, in `format_spend`'s dollars-at-or-above-a-dollar form.
    assert metrics(app)["Budget left"] == "$3.75"


# --- R22.8: at the cap, the turn does not happen ----------------------------


def test_a_user_at_the_cap_is_refused_the_chat(offline, tmp_path):
    write_secrets(tmp_path, {USER: "user"})
    app = run_app(offline, USER, spent(USER, CAP))
    assert len(app.get("chat_input")) == 0


def test_a_user_at_the_cap_is_not_offered_the_uploader_either(offline, tmp_path):
    """Ingestion scans and embeds, so an uploader left on the page is a second
    way to spend past the cap — the one that does not look like a turn.
    """
    write_secrets(tmp_path, {USER: "user"})
    app = run_app(offline, USER, spent(USER, CAP))
    assert len(app.get("file_uploader")) == 0


def test_the_cap_counts_this_identity_and_not_another(offline, tmp_path):
    """A ledger keyed on the wrong address caps the wrong person, and every
    single-user test passes while everyone shares one budget.
    """
    write_secrets(tmp_path, {USER: "user"})
    app = run_app(offline, USER, spent("someone-else@example.com", CAP))
    assert len(app.get("chat_input")) == 1


# --- R22.9: dev is exempt from the cap, not from the ledger -----------------


def test_a_dev_past_the_cap_is_still_served(offline, tmp_path):
    write_secrets(tmp_path, {DEV: "dev"})
    app = run_app(offline, DEV, spent(DEV, CAP * 100))
    assert len(app.get("chat_input")) == 1


def test_a_dev_is_shown_no_budget_figure(offline, tmp_path):
    """An uncapped role has nothing to show (R22.17), and a number there would
    imply a limit that does not exist.
    """
    write_secrets(tmp_path, {DEV: "dev"})
    app = run_app(offline, DEV, spent(DEV, 1.00))
    assert "Budget left" not in metrics(app)


def test_a_dev_is_refused_when_the_ledger_is_down(offline, tmp_path):
    """The operator is the one person who must notice the outage, and an app
    that kept serving them would spend against a store recording nothing.
    """
    write_secrets(tmp_path, {DEV: "dev"})
    app = run_app(offline, DEV, DownLedger())
    assert len(app.get("chat_input")) == 0


# --- R22.13: the two refusals are not interchangeable -----------------------


def test_the_at_cap_refusal_talks_about_the_user_s_limit(offline, tmp_path):
    write_secrets(tmp_path, {USER: "user"})
    text = errors(run_app(offline, USER, spent(USER, CAP))).lower()
    assert "limit" in text
    assert "ledger" not in text


def test_the_ledger_outage_refusal_does_not_blame_the_user(offline, tmp_path):
    """The user has spent nothing and the deployment is broken. Telling them
    they hit their limit sends them to the wrong person for a fix.
    """
    write_secrets(tmp_path, {USER: "user"})
    text = errors(run_app(offline, USER, DownLedger())).lower()
    assert "ledger" in text
    assert "reached the spend limit" not in text


def test_a_ledger_outage_leaves_the_transcript_visible(offline, tmp_path):
    """Losing sight of the interview is not part of the penalty for an outage
    the user did not cause.
    """
    write_secrets(tmp_path, {USER: "user"})
    history = [
        {"role": "user", "content": "What should I say about my weaknesses?"},
        {"role": "assistant", "content": "Name one you are actively working on."},
    ]
    app = run_app(
        offline, USER, DownLedger(), session_state={"messages": history}
    )
    rendered = " ".join(element.value for element in app.markdown)
    assert "actively working on" in rendered


# --- the two clients that outlive the turn ----------------------------------


def test_the_document_index_is_handed_this_turn_s_budget(offline, tmp_path):
    """R22.6 turns on the estimate, and two clients can hold a stale one.

    The document index and the knowledge base live in session state and are
    rebuilt only on an embedding-model switch, so the `Budget` they were
    constructed with carries the estimate of the turn they were born in. From
    turn two onward the cap would be checked against a figure for a prompt the
    user has already sent — with no error and no warning, just a check asking
    about the wrong turn.

    The second run here has a conversation behind it, so the page's estimate is
    measured from history rather than derived from the prompt alone, and the
    two differ. Against the stale shape the index still reports the first.
    """
    write_secrets(tmp_path, {USER: "user"})
    # A priced catalog, so both estimates are real numbers rather than the
    # zero an unreachable catalog produces for everything.
    offline.setattr(
        "interview_prep.pricing.fetch_openrouter_models",
        lambda api_key: [
            {
                "id": "gpt-5-mini",
                "pricing": {"prompt": "0.0000004", "completion": "0.0000016"},
            }
        ],
    )
    app = run_app(offline, USER, InMemoryLedger())
    first = app.session_state["doc_index"].budget.estimate

    app.session_state["messages"] = [
        {"role": "user", "content": "Tell me about the role." * 50},
        {"role": "assistant", "content": "What draws you to it?" * 50},
    ]
    app.run()
    assert not app.exception, [str(e.value)[:300] for e in app.exception]
    second = app.session_state["doc_index"].budget.estimate

    # The whole assertion: the index built on run one is carrying run two's
    # figure. Against the stale shape these are equal, because it kept the
    # `Budget` it was constructed with.
    assert first != second

    # The knowledge base gets the same line for the same reason, and is not
    # asserted here: this fixture latches it off so the run stays offline.
    # Stated rather than left as an apparent oversight.


# --- R22.12 at the deployment level -----------------------------------------


def test_an_unconfigured_ledger_refuses_rather_than_uncapping(offline, tmp_path):
    """No `[spend]` block is a ledger that cannot be read, not the absence of a
    cap — the same direction an unusable `[roles]` table already fails in
    (R21.10), and what `secrets.toml.example` promises. The failure it prevents
    is silent: an app that quietly serves everyone without counting looks
    perfectly healthy until the bill arrives.
    """
    write_secrets(tmp_path, {USER: "user"}, spend=False)
    app = run_app(offline, USER, DownLedger())
    assert len(app.get("chat_input")) == 0
    assert "ledger" in errors(app).lower()


# --- the buffered ledger survives the run that failed to flush --------------


def test_spend_held_by_a_failed_flush_survives_into_the_next_run(monkeypatch):
    """The wiring half of `BufferedLedger`'s retry, which the object's own test
    cannot reach.

    `BufferedLedger.flush` keeps the pending amount when a write fails, so the
    next flush can retry it — and `test_a_failed_flush_keeps_the_spend_rather_than_losing_it`
    proves that on the object. But `current_budget` built a *fresh* buffer on
    every run, so the run that would have retried started with an empty
    `_pending` and the amount was gone. The unit test passed, the property did
    not hold, and one transient pooler error under-counted a turn permanently.

    This asserts the seam instead: the same buffer is carried across runs, and
    what it is still owed goes with it.
    """
    import chat_bot

    store = _UnwritableStore()
    monkeypatch.setattr(chat_bot, "PostgresLedger", lambda dsn: store)
    monkeypatch.setattr(
        chat_bot, "spend_settings", lambda: {"connection_string": "x", "cap_usd": 5.0}
    )

    first = chat_bot.current_budget(estimate=0.01)
    first.record(_IDENTITY, 0.40)
    assert first.ledger.flush() is False, "the store refused, as arranged"
    assert first.ledger.pending_total == pytest.approx(0.40)

    # A new run asks for a budget again.
    second = chat_bot.current_budget(estimate=0.01)
    assert second.ledger is first.ledger, "the buffer must be reused, not rebuilt"
    assert second.ledger.pending_total == pytest.approx(0.40), (
        "spend held by the failed flush was dropped at the run boundary"
    )

    # And when the store recovers, the held amount lands.
    store.writable = True
    assert second.ledger.flush() is True
    assert store.totals["candidate@example.com"] == pytest.approx(0.40)


class _UnwritableStore:
    """A store that reads fine and refuses every write until told otherwise."""

    def __init__(self):
        self.totals = {}
        self.writable = False

    def total(self, email):
        return self.totals.get(email, 0.0)

    def record(self, email, amount):
        if not self.writable:
            raise LedgerUnavailable("pooler said no")
        self.totals[email] = self.totals.get(email, 0.0) + amount


_IDENTITY = Identity(email="candidate@example.com", role=Role.USER)


def test_an_unreadable_cap_is_worded_as_a_deployment_fault(monkeypatch):
    """R22.13, by the back door.

    `cap_usd` missing or non-numeric used to set `cap = 0.0` and nothing else,
    so `decide` refused with "at_cap" and the page told a user who had spent
    nothing that they had reached their spend limit and should ask the operator
    for more. The operator's typo, reported as the user's fault, sending them
    to argue about a budget that does not exist.
    """
    import chat_bot

    monkeypatch.setattr(chat_bot, "PostgresLedger", lambda dsn: _UnwritableStore())
    monkeypatch.setattr(
        chat_bot,
        "spend_settings",
        lambda: {"connection_string": "x", "cap_usd": "five dollars"},
    )
    budget = chat_bot.current_budget(estimate=0.01)
    decision = check_budget(
        _IDENTITY, ledger=budget.ledger, cap=budget.cap, estimate=budget.estimate
    )
    assert not decision.allowed
    assert decision.reason == "ledger_unavailable", (
        "a misconfigured cap must not read as the user having overspent"
    )


# --- an estimate that cannot be priced is unmeasured, never free ------------


def test_a_pricing_outage_does_not_admit_a_user_who_is_nearly_out(offline, tmp_path):
    """R22.21, and the hole it was written to close, reopened by a detail.

    The `offline` fixture is exactly the outage: no network, catalog cache
    cleared. `get_model_pricing` then reports zero rates, so
    `turn_cost(pricing, …)` returns **0.0 rather than None** — and the fallback
    that exists for an unmeasured estimate only fired on `None`. `decide` was
    handed a projected cost of nothing, which fits inside any remaining budget,
    so every turn was admitted and the cap silently degraded to "refuse once
    already over" — precisely what R22.6 forbids.

    The user here has a third of a cent left against a five dollar cap. A
    correctly-priced turn costs more than that, and an unpriceable one must be
    treated as costing *more*, not less.
    """
    write_secrets(tmp_path, {USER: "user"})
    app = run_app(offline, USER, spent(USER, 4.9967))
    assert len(app.get("chat_input")) == 0, (
        "a turn was admitted on an estimate of $0.00 during a pricing outage"
    )
    assert "spend limit" in errors(app).lower()


def test_a_pricing_outage_still_serves_a_user_with_room(offline, tmp_path):
    """The other direction of the same rule, because a change to a classifier
    moves records both ways and only the intended one gets looked at.

    Falling back to the flat floor must not refuse someone who can plainly
    afford it, or a catalog blip would lock out every account at once — which
    is the failure the floor exists to avoid, not to cause.
    """
    write_secrets(tmp_path, {USER: "user"})
    app = run_app(offline, USER, spent(USER, 0.10))
    assert len(app.get("chat_input")) == 1
