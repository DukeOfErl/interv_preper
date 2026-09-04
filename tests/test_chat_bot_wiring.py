"""The auth gate as the app actually wires it (§ 21, ADR-0200).

`authorization.py` (the pure `authorize`/`Identity` model) is covered
thoroughly by `test_authorization.py`, and `permissions.py`'s role/permission
grants by `test_permissions.py`. Neither of those imports `chat_bot.py`, so
neither would notice if the *wiring* regressed: if `current_identity()` forgot
to check expiry, if `main()` read the API key before checking authorization,
or if `st.stop()` were dropped so an unauthorized visitor's script kept
running past the refusal page. That blind spot was the point of the WP1
red-team pass this file started from, and it is now measured explicitly for
the new authentication model.

These run offline *by construction*: the fixture refuses the socket, so the
app takes its documented degraded path (R14.2) and no assertion depends on a
model catalog or a price. `AppTest` has no login simulation, so `st.user` is
monkeypatched directly on the `streamlit` module — the technique this file
uses throughout.
"""

from __future__ import annotations

import pathlib

import pytest
import streamlit as st
from streamlit import config as st_config
from streamlit.testing.v1 import AppTest

from interview_prep import context

# Absolute, because the fixture runs each app from a scratch cwd. The app's own
# paths (prompts/, knowledgebase/) are module-relative, so that is safe.
APP = pathlib.Path(__file__).resolve().parent.parent / "chat_bot.py"

DIAGNOSTIC_TABS = {"Developer", "Warnings"}
USER_TABS = {"Interview", "Evaluations"}
# Enough to get past the fail-fast key check; never used against the API.
UNUSABLE_KEY = "sk-not-a-real-key-for-tests"


class FakeUser(dict):
    """A minimal stand-in for `st.user`, monkeypatched onto the module.

    `chat_bot.current_identity()` reads `user.is_logged_in`, `user.get(...)`
    (for `email`/`exp`) and, via `token_has_expired`, `getattr(user, "exp",
    None)` as a fallback. A dict with attribute access covers every shape it
    is read through.
    """

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


ANONYMOUS_USER = FakeUser(is_logged_in=False)


def logged_in(email, exp=None, email_verified=True):
    """A signed-in session, verified by default.

    `email_verified` is a real claim the adapter requires (R21.3), not a test
    convenience: without it the allowlist would match an address the signer
    never confirmed. It defaults to True here so the many tests about *roles*
    stay about roles, and is set False by the one test that is about
    verification itself.
    """
    fields = {
        "is_logged_in": True,
        "email": email,
        "email_verified": email_verified,
    }
    if exp is not None:
        fields["exp"] = exp
    return FakeUser(**fields)


def use_secrets_file(path):
    """Point Streamlit at `path` as the *only* secrets file.

    `secrets.files` has no environment route, so it has to be set through the
    config API. `st.secrets` then has to be reset, because it memoises the
    parsed file process-globally — that cache is what defeated every earlier
    attempt to canary this.
    """
    st_config.set_option("secrets.files", [str(path)], where_defined="test")
    st.secrets._reset()


def write_roles(tmp_path, table, name="secrets.toml"):
    """Write a `[roles]` secrets file mapping email -> role name.

    Quoted keys, because an email contains characters (`@`, `.`) that are not
    bare TOML keys.
    """
    lines = ["[roles]"]
    for email, role in table.items():
        lines.append(f'"{email}" = "{role}"')
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n")
    use_secrets_file(path)
    return path


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    """Neutralise every documented and undocumented identity source.

    Found in review, carried over from the old role-gate tests: `role_lookup`
    (this module's predecessor) read `st.secrets` first, which resolves
    `~/.streamlit/secrets.toml` as well as the repo's, so machine-local state
    outside this checkout could silently decide these assertions. The same
    risk applies to `role_table()` here.

    What the scratch cwd definitely fixes, verified by experiment: `.env` and
    `./.streamlit/secrets.toml` both resolve relative to cwd, so neither the
    repo's nor the developer's is visible here.

    What it does **not** fix is `~/.streamlit/secrets.toml`, and the earlier
    attempt to redirect it — setting `STREAMLIT_SECRETS_FILES` — was inert.
    Measured on streamlit 1.58.0: with that variable set,
    `config.get_option("secrets.files")` still returns the two default paths
    with `where_defined == "<default>"`. That option simply has no environment
    route. The option is now set directly and the secrets singleton reset,
    which is verified rather than assumed (`test_the_missing_roles_table_...`
    below depends on this fixture actually starting from an empty table).
    """
    original_secrets_files = st_config.get_option("secrets.files")
    empty_secrets = tmp_path / "secrets.toml"
    empty_secrets.write_text("")
    use_secrets_file(empty_secrets)
    (tmp_path / ".env").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", UNUSABLE_KEY)
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.setattr(st, "user", ANONYMOUS_USER)

    # These tests claimed to be offline and were not: the unusable key still
    # sent a real catalog request, which merely happened to fail soft. That
    # made them slow and flaky. Refuse the socket outright, so the app takes
    # its documented degraded path (R14.2) deterministically and nothing here
    # depends on the network.
    def no_network(*args, **kwargs):
        raise OSError("network disabled in tests")

    monkeypatch.setattr("interview_prep.context.request.urlopen", no_network)
    context.fetch_openrouter_models.clear()

    yield monkeypatch

    # `set_option` is process-global and `monkeypatch` cannot undo it, so the
    # redirect has to be lifted by hand or it leaks into every later test.
    st_config.set_option(
        "secrets.files", original_secrets_files, where_defined="test"
    )
    st.secrets._reset()


def run_app(monkeypatch, user, session_state=None, query_params=None):
    """Run the real entry point with `st.user` set to `user`, and return its app.

    `user` is set on the `streamlit` module itself (not just this test
    module's `st` alias) because `chat_bot.py` does its own `import streamlit
    as st` — the two names must refer to the same module object for the patch
    to be visible there, which `monkeypatch.setattr(st, ...)` gives for free
    since `st` here *is* that module.
    """
    monkeypatch.setattr(st, "user", user)
    app = AppTest.from_file(str(APP), default_timeout=120)
    for key, value in (session_state or {}).items():
        app.session_state[key] = value
    for key, value in (query_params or {}).items():
        app.query_params[key] = value
    app.run()
    assert not app.exception, [str(e.value)[:200] for e in app.exception]
    return app


def tab_labels(app):
    return [tab.label for tab in app.sidebar.tabs]


# --- 1: an anonymous visitor gets the sign-in page, not the chat ------------


def test_an_anonymous_visitor_is_not_offered_the_chat(isolated_config):
    app = run_app(isolated_config, ANONYMOUS_USER)
    assert tab_labels(app) == []
    assert len(app.get("chat_input")) == 0


# --- 2 & 3: an unauthorized visitor is refused, and refused completely ------


def test_an_unauthorized_email_gets_the_not_authorized_page(isolated_config, tmp_path):
    write_roles(tmp_path, {"operator@example.com": "dev"})
    app = run_app(isolated_config, logged_in("stranger@example.com"))
    # Told which address they signed in as (R21.9) — asserted structurally
    # (a widget carries exactly that value) rather than pinned to wording.
    codes = [c.value for c in app.get("code")]
    assert "stranger@example.com" in codes


def test_an_unauthorized_visitor_reaches_no_paid_surface(isolated_config, tmp_path):
    write_roles(tmp_path, {"operator@example.com": "dev"})
    app = run_app(isolated_config, logged_in("stranger@example.com"))
    assert tab_labels(app) == []
    assert len(app.get("chat_input")) == 0
    assert len(app.get("file_uploader")) == 0
    assert len(app.get("selectbox")) == 0


# --- 4 & 5: role grants the right tabs --------------------------------------


def test_an_authorized_dev_sees_the_diagnostic_tabs(isolated_config, tmp_path):
    write_roles(tmp_path, {"dev@example.com": "dev"})
    app = run_app(isolated_config, logged_in("dev@example.com"))
    assert DIAGNOSTIC_TABS <= set(tab_labels(app))


def test_an_authorized_user_sees_only_interview_and_evaluations(isolated_config, tmp_path):
    write_roles(tmp_path, {"dev@example.com": "dev", "candidate@example.com": "user"})
    app = run_app(isolated_config, logged_in("candidate@example.com"))
    assert set(tab_labels(app)) == USER_TABS


# --- 6: R21.4 — a session is NOT capped by the ID token's lifetime ----------


def test_a_stale_exp_claim_does_not_end_the_session(isolated_config, tmp_path):
    """The inverse of what this test used to assert, and deliberately so.

    An earlier version re-checked the ID token's `exp` on every run and treated
    a past value as anonymous. That was a category error: an ID token is a
    one-time assertion that the person authenticated at `iat`, not a session.
    Streamlit verifies it once at the OAuth callback and then mints its own
    30-day `_streamlit_user` cookie, which `st.user` reads once at session
    start — so `exp` is frozen at login. Re-checking it turned Google's ~1-hour
    token lifetime into a hard 1-hour cap that destroyed a candidate's
    transcript, documents and evaluations mid-interview.

    Revocation, which that check was reaching for, is the allowlist's job and
    already immediate: `role_table()` is re-read every run (see the test
    below).
    """
    write_roles(tmp_path, {"dev@example.com": "dev"})
    stale = logged_in("dev@example.com", exp=1)  # long past, and irrelevant
    app = run_app(isolated_config, stale)
    assert DIAGNOSTIC_TABS <= set(tab_labels(app)), tab_labels(app)


def test_removing_an_address_from_the_table_locks_it_out_on_the_next_run(
    isolated_config, tmp_path
):
    """R21.5/R20.9: the allowlist is the revocation path, and it is per-run.

    This is what makes dropping the `exp` check safe. A 30-day cookie does not
    outlive the operator's decision, because the table is consulted again on
    every single interaction.

    Scope, stated so nobody reads more into a green run than it earns:
    `write_roles` resets the `st.secrets` cache itself, so what this proves is
    that *the gate* re-reads the table and holds no cached role. Whether
    `st.secrets` picks up an edited file is Streamlit's mechanism, not ours —
    it watches the file locally, and on Streamlit Cloud a secrets edit restarts
    the app. Neither is asserted here.
    """
    write_roles(tmp_path, {"dev@example.com": "dev"})
    user = logged_in("dev@example.com")
    app = run_app(isolated_config, user)
    assert DIAGNOSTIC_TABS <= set(tab_labels(app))

    # The operator removes them; the browser session is untouched.
    write_roles(tmp_path, {"someone-else@example.com": "dev"}, name="secrets.toml")
    app.run()
    assert not app.exception, [str(e.value)[:200] for e in app.exception]
    assert tab_labels(app) == [], tab_labels(app)


# --- 7: R21.8 — a missing/unreadable table authorizes nobody, dev included --


def test_a_missing_roles_table_authorizes_nobody_including_a_would_be_dev(
    isolated_config,
):
    # isolated_config already points secrets.files at an empty file, so
    # `st.secrets["roles"]` is absent — no [roles] section was ever written.
    app = run_app(isolated_config, logged_in("dev@example.com"))
    assert tab_labels(app) == []
    codes = [c.value for c in app.get("code")]
    assert "dev@example.com" in codes


# --- 8: R20.8 — session_state / query_params cannot promote -----------------


def test_the_browser_cannot_promote_itself(isolated_config, tmp_path):
    """Nothing the client can influence is an identity.

    `session_state` is writable by any page code and `query_params` comes
    straight off the URL, so both are planted here under the names a
    promotion attempt would plausibly use — including the names the old,
    now-deleted role mechanism used.
    """
    write_roles(tmp_path, {"dev@example.com": "dev", "candidate@example.com": "user"})
    app = run_app(
        isolated_config,
        logged_in("candidate@example.com"),
        session_state={
            "role": "dev",
            "current_role": "dev",
            "show_diagnostics": True,
            "INTERVIEW_PREP_ROLE": "dev",
        },
        query_params={"role": "dev"},
    )
    assert set(tab_labels(app)) == USER_TABS


# --- 9: R21.6 — casing is not a security boundary ---------------------------


def test_email_casing_does_not_prevent_authorization(isolated_config, tmp_path):
    write_roles(tmp_path, {"dev@example.com": "dev"})
    app = run_app(isolated_config, logged_in("DEV@Example.COM"))
    assert DIAGNOSTIC_TABS <= set(tab_labels(app))


# --- R21.3 at the wiring level ----------------------------------------------


def test_an_unverified_allowlisted_address_is_refused_by_the_page(
    isolated_config, tmp_path
):
    """The red-team's HIGH finding, asserted where a user would meet it.

    `authorize` refuses an unverified claim (tested in test_authorization.py);
    this asserts the *adapter* actually reads the claim and passes it on. The
    two are separable and only one of them was wrong: the pure function was
    fine once told, but `current_identity` never looked at `email_verified`, so
    an attacker holding a validly signed token for an allowlisted address they
    do not own was admitted as its owner.
    """
    write_roles(tmp_path, {"dev@example.com": "dev"})
    app = run_app(
        isolated_config, logged_in("dev@example.com", email_verified=False)
    )
    assert tab_labels(app) == [], tab_labels(app)
    assert app.chat_input == []


# --- R21.21: a working session can be ended from the app --------------------


def sidebar_buttons(app):
    return [b.label for b in app.sidebar.button]


def test_a_signed_in_user_is_offered_sign_out(isolated_config, tmp_path):
    """Without this, leaving means clearing a cookie by hand.

    Streamlit's session is a 30-day cookie that outlives the browser tab
    (R21.6), so "I closed it" is not signing out. On a shared machine that is
    an exposure, not an inconvenience — the next person gets the interview,
    the uploaded resume, and the operator's API budget.
    """
    write_roles(tmp_path, {"candidate@example.com": "user"})
    app = run_app(isolated_config, logged_in("candidate@example.com"))
    assert "Sign out" in sidebar_buttons(app), sidebar_buttons(app)


def test_a_dev_is_offered_sign_out_too(isolated_config, tmp_path):
    # Not a diagnostic: it belongs to being signed in, not to holding a
    # permission, so it must not sit behind VIEW_DIAGNOSTICS.
    write_roles(tmp_path, {"dev@example.com": "dev"})
    app = run_app(isolated_config, logged_in("dev@example.com"))
    assert "Sign out" in sidebar_buttons(app), sidebar_buttons(app)


def test_the_sign_out_button_calls_streamlit_logout(isolated_config, tmp_path):
    """Asserts the wiring, not just the widget.

    A button that renders and does nothing looks identical in a screenshot and
    identical in a label assertion, and is the more likely mistake here.
    """
    write_roles(tmp_path, {"candidate@example.com": "user"})
    called = []
    isolated_config.setattr(st, "logout", lambda: called.append(True))

    app = run_app(isolated_config, logged_in("candidate@example.com"))
    [button] = [b for b in app.sidebar.button if b.label == "Sign out"]
    button.click().run()
    assert called, "the Sign out button rendered but never called st.logout"


def test_an_anonymous_visitor_is_not_offered_sign_out(isolated_config):
    # Nothing to sign out of; offering it would imply a session that is not
    # there. The sign-in page is its own screen.
    app = run_app(isolated_config, ANONYMOUS_USER)
    assert "Sign out" not in sidebar_buttons(app), sidebar_buttons(app)
