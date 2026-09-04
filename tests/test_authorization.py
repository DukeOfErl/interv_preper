"""Authorization: who may use the app at all (§ 21, ADR-0200).

Separate from `test_permissions.py` on purpose, because the two answer
different questions and fail in opposite directions. Permissions grade what an
authorized role may *do*; authorization decides whether there is a role at all
(R21.12). Collapsing them would make "nobody signed in" and "signed in with
few rights" the same state.

The asymmetry in R21.7/R21.8 is the substance of this file and is asserted from
both sides, because a rule that classifies moves records in both directions and
only the intended one gets looked at:

  * an authorized email with a **typo'd role** keeps access and loses privilege
  * an **unreadable table** authorizes nobody, including the operator

Every turn of this app spends the operator's own credit, so a wrongly granted
authorization has a direct cost and produces no error, no log line, and output
that looks entirely ordinary.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from interview_prep.authorization import (
    ANONYMOUS,
    Identity,
    authorize,
)
from interview_prep.permissions import Permission, Role, has

TABLE = {
    "operator@example.com": "dev",
    "candidate@example.com": "user",
}


class Exploding:
    """A mapping whose lookup raises, like a misbehaving secrets proxy."""

    def __contains__(self, key):
        raise RuntimeError("no membership for you")

    def get(self, *args):
        raise RuntimeError("no lookup for you")

    def items(self):
        raise RuntimeError("no items for you")


# --- the happy paths --------------------------------------------------------


def test_an_allowlisted_operator_is_authorized_as_dev():
    identity = authorize("operator@example.com", table=TABLE, email_verified=True)
    assert identity.is_authorized
    assert identity.role is Role.DEV
    assert identity.email == "operator@example.com"
    assert has(identity.role, Permission.VIEW_DIAGNOSTICS)


def test_an_allowlisted_candidate_is_authorized_as_user():
    identity = authorize("candidate@example.com", table=TABLE, email_verified=True)
    assert identity.is_authorized
    assert identity.role is Role.USER
    assert not has(identity.role, Permission.VIEW_DIAGNOSTICS)


# --- absence is refusal (R21.5) ---------------------------------------------


def test_an_email_absent_from_the_table_is_refused():
    # The whole point of the allowlist: a perfectly valid Google account that
    # the operator never authorized must not reach a paid turn.
    identity = authorize("stranger@example.com", table=TABLE)
    assert not identity.is_authorized
    assert identity.role is None
    # The address is still carried, so the page can show it (R21.9) and the
    # person can tell they signed in with the wrong account.
    assert identity.email == "stranger@example.com"


@pytest.mark.parametrize(
    "email",
    [None, "", "   ", 0, False, [], {}, object(), Exploding()],
    ids=[
        "none", "empty", "whitespace", "zero", "false",
        "list", "dict", "object", "exploding",
    ],
)
def test_no_usable_email_is_refused(email):
    """R21.3: an identity the allowlist cannot be applied to is not an identity.

    An authenticated session with no email claim reaches here, and so does
    anything the provider or the proxy hands back that is not a string.
    """
    identity = authorize(email, table=TABLE)
    assert not identity.is_authorized
    assert identity.role is None
    # Found by fault seeding: making `_normalise` return "" instead of None for
    # a non-string survived every assertion above, because `role` still came
    # out None and `is_authorized` was therefore right by coincidence. The
    # blank string would then reach `chat_bot.render_not_authorized` and the
    # agent's refusal message, both of which use `or` to fall back — so a
    # refused caller would be described as "" rather than named.
    assert identity.email is None


# --- R21.6: casing and whitespace are not a security boundary ---------------


@pytest.mark.parametrize(
    "email",
    [
        "Operator@Example.com",
        "OPERATOR@EXAMPLE.COM",
        "  operator@example.com  ",
        "\toperator@example.com\n",
    ],
)
def test_email_matching_ignores_case_and_surrounding_whitespace(email):
    identity = authorize(email, table=TABLE, email_verified=True)
    assert identity.is_authorized
    assert identity.role is Role.DEV


def test_the_tables_own_keys_are_normalised_too():
    """Both sides, not just the input — an operator will paste a capitalised
    address into secrets sooner or later, and it must not lock them out."""
    identity = authorize("operator@example.com", table={"  Operator@Example.COM ": "dev"}, email_verified=True)
    assert identity.is_authorized
    assert identity.role is Role.DEV


# --- R21.7: a bad role costs privilege, not access --------------------------


@pytest.mark.parametrize(
    "role_value",
    ["admin", "developer", "DEV ROLE", "", "   ", None, 7, [], object()],
    ids=["admin", "developer", "dev-role", "empty", "blank",
         "none", "int", "list", "object"],
)
def test_an_unrecognised_role_keeps_access_and_loses_privilege(role_value):
    """The cheap direction, chosen deliberately.

    Someone the operator authorized and paid for should not be locked out by a
    typo in a role name — but nor should a typo be a promotion. Both halves are
    asserted, because this is exactly the kind of rule where only the intended
    direction gets checked.
    """
    identity = authorize("candidate@example.com", table={"candidate@example.com": role_value}, email_verified=True)
    assert identity.is_authorized, "a typo'd role must not cost access"
    assert identity.role is Role.USER, "a typo'd role must not grant privilege"


# --- R21.8: an unusable table authorizes nobody -----------------------------


@pytest.mark.parametrize(
    "table",
    [None, {}, "", "dev", 0, [], ["operator@example.com"], object(), Exploding()],
    ids=["none", "empty", "empty-string", "string", "zero",
         "list", "list-of-emails", "object", "exploding"],
)
def test_an_absent_or_unusable_table_authorizes_nobody(table):
    """The one expensive fail-closed in the codebase, and it is deliberate.

    A misconfigured deployment serving nobody is a loud, immediate,
    self-correcting failure. The alternative — a deployment that silently
    admits the world to the operator's OpenRouter credit — is none of those.
    """
    identity = authorize("operator@example.com", table=table)
    assert not identity.is_authorized
    assert identity.role is None


def test_a_table_naming_everyone_still_requires_a_match():
    # Guard against a "*" or "all" entry being read as a wildcard by accident:
    # nothing in the design says it is one, so it must match literally.
    identity = authorize("stranger@example.com", table={"*": "dev"})
    assert not identity.is_authorized


# --- the anonymous identity -------------------------------------------------


def test_the_anonymous_identity_is_refused_and_holds_nothing():
    assert not ANONYMOUS.is_authorized
    assert ANONYMOUS.role is None
    assert ANONYMOUS.email is None


def test_an_identity_is_immutable():
    """It is passed into the agent as the proof of authorization (R21.11), so
    a caller must not be able to promote one after it was decided."""
    identity = authorize("candidate@example.com", table=TABLE, email_verified=True)
    with pytest.raises(Exception):
        identity.role = Role.DEV


# --- R21.12: authorization is not a permission ------------------------------


def test_an_unauthorized_identity_holds_no_permission():
    identity = authorize("stranger@example.com", table=TABLE)
    for permission in Permission:
        assert not has(identity.role, permission)


# --- R21.16 / R20.4: the module stays pure ----------------------------------


def test_the_module_imports_no_ui_or_agent_framework():
    """Same contract as `permissions.py` (R20.4): callable from a unit test,
    the agent path and the page alike. If this module ever imports Streamlit,
    the guard stops being reachable from the two callers that are not the page.
    """
    source = pathlib.Path("interview_prep/authorization.py").read_text()
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])

    forbidden = {"streamlit", "langchain", "langgraph", "openai"}
    assert not (imported & forbidden), imported & forbidden


# --- R21.3: the email must be VERIFIED, not merely claimed ------------------
#
# Found by the WP2 red-team, and it was a spec-to-code mismatch rather than an
# oversight in the spec: R21.3 says "the verified email claim" and the first
# implementation read only `email`, discarding the `email_verified` claim that
# sits beside it in the same token.
#
# The attack it enables is the whole allowlist, defeated: `st.login` is
# provider-agnostic and the provider is a secrets setting, not a code
# constant. On any IdP where a user can self-assert an address at registration
# — Auth0/Okta database connections with verification off, Keycloak with
# "Verify Email" disabled, an unverified Google Workspace domain — a stranger
# registers as an allowlisted address, receives a **validly signed** token
# carrying `email_verified: false`, and is admitted as that person. Every
# signature, origin and expiry check passes; the rendered app and every log
# line are indistinguishable from the real operator's session.


@pytest.mark.parametrize(
    "verified",
    [False, None, "false", "no", 0, "", [], object()],
    ids=["false", "none", "false-string", "no", "zero", "empty", "list", "object"],
)
def test_an_unverified_email_is_refused_however_it_says_so(verified):
    identity = authorize(
        "operator@example.com", table=TABLE, email_verified=verified
    )
    assert not identity.is_authorized, "an unverified claim must not match the allowlist"
    assert identity.role is None


def test_only_a_literal_true_counts_as_verified():
    # Not truthiness: a provider returning the string "true" is returning a
    # string, and guessing what it meant is how a bypass gets built. The
    # adapter normalises; this function requires the real thing.
    assert authorize("operator@example.com", table=TABLE, email_verified=True).is_authorized
    assert not authorize(
        "operator@example.com", table=TABLE, email_verified="true"
    ).is_authorized


def test_verification_is_required_by_default():
    """The default must be the safe one.

    A caller that has not heard of `email_verified` — the adapter before this
    fix, or a future second adapter — must fail closed rather than inherit the
    old permissive behaviour.
    """
    assert not authorize("operator@example.com", table=TABLE).is_authorized


def test_an_unverified_address_is_still_reported_back():
    # So `render_not_authorized` can name it (R21.9) and the operator can see
    # that someone tried to sign in as an allowlisted address.
    identity = authorize("operator@example.com", table=TABLE, email_verified=False)
    assert identity.email == "operator@example.com"
