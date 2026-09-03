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
import streamlit as st
from streamlit import config as st_config
from streamlit.testing.v1 import AppTest

from interview_prep import context
from interview_prep.permissions import ROLE_ENV_VAR

# Absolute, because the fixture runs each app from a scratch cwd. The app's own
# paths (prompts/, knowledgebase/) are module-relative, so that is safe.
APP = pathlib.Path(__file__).resolve().parent.parent / "chat_bot.py"

DIAGNOSTIC_TABS = {"Developer", "Warnings"}
# Enough to get past the fail-fast key check; never used against the API.
UNUSABLE_KEY = "sk-not-a-real-key-for-tests"


def use_secrets_file(path):
    """Point Streamlit at `path` as the *only* secrets file.

    `secrets.files` has no environment route, so it has to be set through the
    config API. `st.secrets` then has to be reset, because it memoises the
    parsed file process-globally — that cache is what defeated every earlier
    attempt to canary this.
    """
    st_config.set_option("secrets.files", [str(path)], where_defined="test")
    st.secrets._reset()


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

    What it does **not** fix is `~/.streamlit/secrets.toml`, and the earlier
    attempt to redirect it — setting `STREAMLIT_SECRETS_FILES` — was inert.
    Measured on streamlit 1.58.0: with that variable set,
    `config.get_option("secrets.files")` still returns the two default paths
    with `where_defined == "<default>"`. That option simply has no environment
    route. The failure was silent in both directions: on a machine whose home
    secrets file named `dev`, 8 of these 9 tests failed against a correct gate;
    on one naming `user`, every `test_no_other_role_is` case passed *regardless
    of what the gate did* — masking the exact fail-open regression this file
    exists to catch.

    The option is now set directly and the secrets singleton reset, which is
    verified rather than assumed: pointing `secrets.files` at a file containing
    `INTERVIEW_PREP_ROLE = "dev"` makes `st.secrets.get(...)` return `"dev"`,
    and re-pointing it at an empty file returns `None`. So the redirect is known
    to be live in both directions, and `test_the_role_can_come_from_secrets`
    below keeps it that way — if it ever goes inert again, that test fails
    instead of these silently reading the developer's machine.
    """
    original_secrets_files = st_config.get_option("secrets.files")
    secrets = tmp_path / "secrets.toml"
    secrets.write_text("")
    use_secrets_file(secrets)
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

    yield monkeypatch

    # `set_option` is process-global and `monkeypatch` cannot undo it, so the
    # redirect has to be lifted by hand or it leaks into every later test.
    st_config.set_option(
        "secrets.files", original_secrets_files, where_defined="test"
    )
    st.secrets._reset()


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


def test_the_role_can_come_from_secrets(isolated_config, tmp_path):
    """R20.7's first source — how a Streamlit Cloud deployment sets the role.

    `role_lookup` reads `st.secrets` before the environment, and until now only
    the environment branch was ever exercised. This also keeps the fixture's
    redirect honest: it is the one test that *needs* `secrets.files` to point
    at the scratch file, so if that mechanism goes inert again (as
    `STREAMLIT_SECRETS_FILES` silently was) this fails loudly, instead of the
    other tests quietly reading the developer's `~/.streamlit/secrets.toml`.
    """
    secrets = tmp_path / "from_secrets.toml"
    secrets.write_text(f'{ROLE_ENV_VAR} = "dev"\n')
    use_secrets_file(secrets)

    # No environment role at all: the tabs can only come from the secrets file.
    labels = tab_labels(run_app(isolated_config, None))
    assert DIAGNOSTIC_TABS <= set(labels), labels


def test_secrets_outrank_the_environment(isolated_config, tmp_path):
    """R20.7 states the order; nothing asserted it.

    A deployment that sets `dev` in secrets must not be demoted by a stray
    `INTERVIEW_PREP_ROLE=user` in the process environment.
    """
    secrets = tmp_path / "outranks.toml"
    secrets.write_text(f'{ROLE_ENV_VAR} = "dev"\n')
    use_secrets_file(secrets)

    labels = tab_labels(run_app(isolated_config, "user"))
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
