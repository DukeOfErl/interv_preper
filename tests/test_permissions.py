"""The role/permission model, tested as a security control in its own right.

Per ADR-0190 the check is a pure function of (role, permission) rather than a
UI condition, because `evals/` and `tests/` already reach the agent without
passing through `chat_bot.py` — a guard in the page holds for one of three
callers. Testing it here is the point of extracting it.

Most of this file is about the *wrong* inputs. A permission wrongly denied
produces a complaint within minutes; a permission wrongly granted raises
nothing, logs nothing, and looks ordinary. Every path that cannot decide must
therefore land on `USER`, and that is asserted per input shape rather than
once in general.
"""

from __future__ import annotations

import ast
import itertools
import pathlib

import pytest

from interview_prep.permissions import (
    ROLE_ENV_VAR,
    Permission,
    Role,
    current_role,
    has,
    resolve_role,
)


class Exploding:
    """A value whose string conversion raises, like a misbehaving secrets proxy."""

    def __str__(self):
        raise RuntimeError("no string for you")

    __repr__ = __str__


def lookup_returning(value):
    """A minimal identity port: records what it was asked for, answers once."""

    calls = []

    def lookup(key):
        calls.append(key)
        return value

    lookup.calls = calls
    return lookup


# --- resolve_role: turning an untrusted string into a role -------------------


def test_canonical_role_names_resolve():
    assert resolve_role("user") is Role.USER
    assert resolve_role("dev") is Role.DEV


def test_role_names_are_matched_case_insensitively_and_stripped():
    # An operator setting the env var will type DEV or leave a trailing space,
    # and neither should silently produce a role they did not ask for. This
    # cannot widen the gate: no unrecognized string becomes DEV either way.
    for raw in ("DEV", "Dev", " dev ", "\tdev\n"):
        assert resolve_role(raw) is Role.DEV


def test_an_absent_or_empty_role_is_a_user():
    for raw in (None, "", "   "):
        assert resolve_role(raw) is Role.USER


def test_an_unknown_role_name_is_a_user():
    # Notably "admin" and "developer" — plausible things to type that must not
    # be generously interpreted as DEV.
    for raw in ("admin", "developer", "root", "dev2", "devs", "us er"):
        assert resolve_role(raw) is Role.USER


def test_a_role_that_is_not_a_string_is_a_user():
    for raw in (0, 1, True, [], {}, ["dev"], {"role": "dev"}, object()):
        assert resolve_role(raw) is Role.USER


def test_a_role_whose_string_conversion_explodes_is_a_user():
    # Fail closed on an exception, not just on an unrecognized value. Note this
    # case is caught by the isinstance guard and never reaches the handler —
    # see the next test for the input that does.
    assert resolve_role(Exploding()) is Role.USER


def test_a_string_that_raises_while_being_normalised_is_a_user():
    """The exception handler in `resolve_role`, actually exercised.

    Found in review: every other hostile input here is rejected by the
    `isinstance(raw, str)` guard first, so deleting the whole `try/except`
    left all 22 tests green — a false all-clear of exactly the kind R20.11
    exists to prevent. A `str` subclass is the shape that passes the guard
    and then fails inside it, which is reachable in practice from any
    secrets or settings backend that returns a proxy string type.
    """

    class HostileString(str):
        def strip(self, *args):
            raise RuntimeError("normalisation exploded")

    assert resolve_role(HostileString("dev")) is Role.USER


# --- has: the permission check ----------------------------------------------


def test_dev_can_view_diagnostics():
    assert has(Role.DEV, Permission.VIEW_DIAGNOSTICS) is True


def test_a_user_cannot_view_diagnostics():
    assert has(Role.USER, Permission.VIEW_DIAGNOSTICS) is False


def test_every_role_permission_pair_is_decided():
    # A pair the table forgot must not raise (or a new permission would take
    # the app down); it must be answered, and answered False.
    for role, permission in itertools.product(Role, Permission):
        assert isinstance(has(role, permission), bool)


def test_a_role_missing_from_the_grant_table_is_denied(monkeypatch):
    """R20.3: a role the table forgot is answered False, not raised on.

    The table is consulted only after the isinstance guard, so every current
    Role is present in it and this branch is unreachable by ordinary means —
    which is why it has to be constructed. The realistic way to arrive here is
    a role added to the enum and forgotten in the table, and the cost of
    getting it wrong is the whole app failing on a KeyError.

    Found by fault seeding: replacing the table's `.get(role, set())` with
    `_GRANTS[role]` broke nothing, because no test could reach the default.
    """
    from interview_prep import permissions

    monkeypatch.delitem(permissions._GRANTS, Role.DEV)
    assert has(Role.DEV, Permission.VIEW_DIAGNOSTICS) is False


def test_only_dev_holds_any_permission():
    # The whole matrix, stated from the requirement rather than read off the
    # implementation: dev holds everything defined so far, user holds nothing.
    for permission in Permission:
        assert has(Role.DEV, permission) is True
        assert has(Role.USER, permission) is False


@pytest.mark.parametrize(
    "role, permission",
    [
        ("dev", Permission.VIEW_DIAGNOSTICS),  # a string that looks like a role
        (Role.DEV, "view_diagnostics"),  # a string that looks like a permission
        (None, None),
        (Role.DEV, None),
        (object(), object()),
    ],
)
def test_has_refuses_arguments_that_are_not_roles_and_permissions(role, permission):
    # A caller that passes the raw string instead of the enum must be denied,
    # not accidentally granted by a truthy comparison.
    assert has(role, permission) is False


# --- current_role: the identity port ----------------------------------------


def test_the_port_reads_the_role_from_its_lookup():
    assert current_role(lookup_returning("dev")) is Role.DEV


def test_the_port_consults_the_documented_key():
    lookup = lookup_returning("dev")
    current_role(lookup)
    assert lookup.calls == [ROLE_ENV_VAR]


def test_a_lookup_that_returns_nothing_is_a_user():
    assert current_role(lookup_returning(None)) is Role.USER


def test_a_lookup_that_raises_is_a_user():
    # A missing secrets file or an unconfigured backend must not grant DEV,
    # and must not take the app down either.
    def exploding_lookup(key):
        raise KeyError(key)

    assert current_role(exploding_lookup) is Role.USER


def test_a_lookup_returning_an_unknown_value_is_a_user():
    assert current_role(lookup_returning("superuser")) is Role.USER


# --- the architectural rule (R20.4), asserted mechanically -------------------


def test_the_module_imports_no_ui_or_agent_framework():
    """R20.4: callable from a test, the agent path and the page alike.

    Asserted against the source rather than by trying an import, because the
    packages are installed — an import would succeed and prove nothing.
    """
    source = pathlib.Path(
        __import__("interview_prep.permissions", fromlist=["_"]).__file__
    ).read_text()

    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden = {
        "streamlit",
        "langchain",
        "langchain_core",
        "langchain_openai",
        "langgraph",
        "openai",
    }
    assert not (imported & forbidden), (
        f"permissions.py must stay framework-free; found {sorted(imported & forbidden)}"
    )
