"""The role gate as the app actually wires it (R20.10, R20.8).

`permissions.py` is covered thoroughly by `test_permissions.py`, but the
expression that *consults* it lives in `chat_bot.py` — which no other test
imports, and which coverage does not measure (`--cov=interview_prep`). A
regression that made the gate fail open, or made the identity adapter read
`session_state`, would leave every other test in the suite green. That blind
spot was found by the WP1 red-team pass, not by the tests.

These run offline. The app degrades gracefully without a usable API key
(R14.2), so a deliberately invalid one is enough to reach the sidebar, and no
assertion here depends on a model catalog or a price being fetched.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from interview_prep.permissions import ROLE_ENV_VAR

DIAGNOSTIC_TABS = {"Developer", "Warnings"}
# Enough to get past the fail-fast key check; never used against the API.
UNUSABLE_KEY = "sk-not-a-real-key-for-tests"


def run_app(monkeypatch, role, session_state=None, query_params=None):
    """Run the real entry point with a role configured, and return its app."""
    monkeypatch.setenv("OPENROUTER_API_KEY", UNUSABLE_KEY)
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    if role is None:
        monkeypatch.delenv(ROLE_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(ROLE_ENV_VAR, role)

    app = AppTest.from_file("chat_bot.py", default_timeout=120)
    for key, value in (session_state or {}).items():
        app.session_state[key] = value
    for key, value in (query_params or {}).items():
        app.query_params[key] = value
    app.run()
    assert not app.exception, [str(e.value)[:200] for e in app.exception]
    return app


def tab_labels(app):
    return [tab.label for tab in app.sidebar.tabs]


def test_a_dev_role_is_offered_the_diagnostic_tabs(monkeypatch):
    labels = tab_labels(run_app(monkeypatch, "dev"))
    assert DIAGNOSTIC_TABS <= set(labels), labels


@pytest.mark.parametrize("role", ["user", "admin", "developer", "DEV ROLE", "", None])
def test_no_other_role_is(monkeypatch, role):
    # Including the plausible near-misses: an operator who typed "admin" or
    # "developer" gets a user's sidebar, not a developer's.
    labels = tab_labels(run_app(monkeypatch, role))
    assert set(labels) == {"Interview", "Evaluations"}, labels


def test_the_browser_cannot_promote_itself(monkeypatch):
    """R20.8: nothing the client can influence is an identity.

    `session_state` is writable by any page code and `query_params` comes
    straight off the URL, so both are tried here under the names a promotion
    attempt would plausibly use.
    """
    app = run_app(
        monkeypatch,
        "user",
        session_state={
            ROLE_ENV_VAR: "dev",
            "role": "dev",
            "current_role": "dev",
            "show_diagnostics": True,
        },
        query_params={"role": "dev"},
    )
    assert set(tab_labels(app)) == {"Interview", "Evaluations"}, tab_labels(app)


def test_a_demotion_takes_effect_on_the_next_run(monkeypatch):
    """R20.9: the role is re-read from the port each run, not cached.

    A `dev` session whose configuration changes must lose the tabs without a
    restart — otherwise a revoked role outlives its revocation.
    """
    app = run_app(monkeypatch, "dev")
    assert DIAGNOSTIC_TABS <= set(tab_labels(app))

    monkeypatch.setenv(ROLE_ENV_VAR, "user")
    app.run()
    assert not app.exception, [str(e.value)[:200] for e in app.exception]
    assert set(tab_labels(app)) == {"Interview", "Evaluations"}, tab_labels(app)
