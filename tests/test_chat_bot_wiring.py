"""The role gate as the app actually wires it (R20.10, R20.8).

`permissions.py` is covered thoroughly by `test_permissions.py`, but the
expression that *consults* it lives in `chat_bot.py`, which no other test
imports. A regression that made the gate fail open, or made the identity
adapter read `session_state`, would leave every other test in the suite green.
That blind spot was found by the WP1 red-team pass, not by the tests — and it
was invisible in the coverage report, because `chat_bot.py` was outside
`--cov=interview_prep` and so absent from the denominator rather than showing
as uncovered. It is now measured explicitly.

These run offline *by construction*: the fixture refuses the socket, so the app
takes its documented degraded path (R14.2) and no assertion depends on a model
catalog or a price. An earlier version merely passed an unusable key and let a
real request fail soft, which was slow, flaky, and not actually offline.
"""

from __future__ import annotations

import pathlib

import pytest
from streamlit.testing.v1 import AppTest

from interview_prep import context
from interview_prep.permissions import ROLE_ENV_VAR

# Absolute, because the fixture runs each app from a scratch cwd. The app's own
# paths (prompts/, knowledgebase/) are module-relative, so that is safe.
APP = pathlib.Path(__file__).resolve().parent.parent / "chat_bot.py"

DIAGNOSTIC_TABS = {"Developer", "Warnings"}
# Enough to get past the fail-fast key check; never used against the API.
UNUSABLE_KEY = "sk-not-a-real-key-for-tests"


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    """Neutralise both documented role sources before each run.

    Found in review: the app loads `.env` (via `load_role`), so `delenv` alone
    does not make the role absent — a developer following the README and
    putting `INTERVIEW_PREP_ROLE=dev` in `.env` failed the "no role" case. And
    `role_lookup` reads `st.secrets` *first*, which resolves
    `~/.streamlit/secrets.toml` as well as the repo's, so machine-local state
    outside this checkout decided 8 of these 9 assertions.

    What the scratch cwd definitely fixes, verified by experiment: `.env` and
    `./.streamlit/secrets.toml` both resolve relative to cwd, so neither the
    repo's nor the developer's is visible here.

    What it does not: a `~/.streamlit/secrets.toml` would still be found.
    `STREAMLIT_SECRETS_FILES` is set below to redirect that, but this is
    **unverified** — `st.secrets` caches process-globally, which contaminated
    every attempt to canary it, and $HOME is not writable in this sandbox. If
    that file exists and names a role, `test_no_other_role_is` is what will
    fail, with a tab-label diff. Treat such a failure as this fixture leaking,
    not as a regression in the gate.
    """
    secrets = tmp_path / "secrets.toml"
    secrets.write_text("")
    monkeypatch.setenv("STREAMLIT_SECRETS_FILES", str(secrets))
    (tmp_path / ".env").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", UNUSABLE_KEY)
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.delenv(ROLE_ENV_VAR, raising=False)

    # These tests claimed to be offline and were not: the unusable key still
    # sent a real catalog request, which merely happened to fail soft. That
    # made them slow and flaky (a truncated response under coverage
    # instrumentation is what surfaced the IncompleteRead bug in
    # `fetch_openrouter_models`). Refuse the socket outright, so the app takes
    # its documented degraded path (R14.2) deterministically and nothing here
    # depends on the network.
    def no_network(*args, **kwargs):
        raise OSError("network disabled in tests")

    monkeypatch.setattr("interview_prep.context.request.urlopen", no_network)
    context.fetch_openrouter_models.clear()
    return monkeypatch


def run_app(monkeypatch, role, session_state=None, query_params=None):
    """Run the real entry point with a role configured, and return its app."""
    if role is not None:
        monkeypatch.setenv(ROLE_ENV_VAR, role)

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


def test_a_dev_role_is_offered_the_diagnostic_tabs(isolated_config):
    labels = tab_labels(run_app(isolated_config, "dev"))
    assert DIAGNOSTIC_TABS <= set(labels), labels


@pytest.mark.parametrize("role", ["user", "admin", "developer", "DEV ROLE", "", None])
def test_no_other_role_is(isolated_config, role):
    # Including the plausible near-misses: an operator who typed "admin" or
    # "developer" gets a user's sidebar, not a developer's.
    labels = tab_labels(run_app(isolated_config, role))
    assert set(labels) == {"Interview", "Evaluations"}, labels


def test_the_browser_cannot_promote_itself(isolated_config):
    """R20.8: nothing the client can influence is an identity.

    `session_state` is writable by any page code and `query_params` comes
    straight off the URL, so both are tried here under the names a promotion
    attempt would plausibly use.
    """
    app = run_app(
        isolated_config,
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


def test_a_demotion_takes_effect_on_the_next_run(isolated_config):
    """R20.9: the role is re-read from the port each run, not cached.

    A `dev` session whose configuration changes must lose the tabs without a
    restart — otherwise a revoked role outlives its revocation.
    """
    app = run_app(isolated_config, "dev")
    assert DIAGNOSTIC_TABS <= set(tab_labels(app))

    isolated_config.setenv(ROLE_ENV_VAR, "user")
    app.run()
    assert not app.exception, [str(e.value)[:200] for e in app.exception]
    assert set(tab_labels(app)) == {"Interview", "Evaluations"}, tab_labels(app)
