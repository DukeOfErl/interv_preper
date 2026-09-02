"""The role/permission model: a pure function of (role, permission).

Per ADR-0190, this is what a spend cap or a capability check would have to
hold in `chat_bot.py` alone — but the app has three callers (`chat_bot.py`,
`evals/`, `tests/`), and only one of them is the page. Living here, free of
Streamlit and any agent framework, means the check is the same object for all
three, and a test can ask the question directly rather than by clicking
around as a `dev`.

Every path that cannot decide — an unknown role name, an absent identity, a
malformed override, or any exception raised while resolving — lands on
`Role.USER`. That is the cheap direction to be wrong in: a permission wrongly
denied produces a complaint within minutes, a permission wrongly granted
produces nothing at all.
"""

from __future__ import annotations

from enum import Enum, auto

# The documented key an identity port is asked for. One seam, one function
# (`current_role`) — see ADR-0190; a URL parameter or `session_state` key is
# explicitly not this, per R20.8.
ROLE_ENV_VAR = "INTERVIEW_PREP_ROLE"


class Role(Enum):
    USER = auto()
    DEV = auto()


class Permission(Enum):
    VIEW_DIAGNOSTICS = auto()


# What each role holds. Dev holds everything defined so far; user holds
# nothing. A pair the table forgot must still be answered (and answered
# False), not raise — see `has`.
_GRANTS = {
    Role.DEV: {Permission.VIEW_DIAGNOSTICS},
    Role.USER: set(),
}

_ROLE_NAMES = {"user": Role.USER, "dev": Role.DEV}


def resolve_role(raw) -> Role:
    """Turn an untrusted value into a `Role`, defaulting to `Role.USER`.

    Case-insensitive and stripped so an operator typing `DEV` or leaving a
    trailing space is not silently denied — but nothing widens the gate: no
    unrecognized string ever becomes `DEV`.
    """
    try:
        if not isinstance(raw, str):
            return Role.USER
        return _ROLE_NAMES.get(raw.strip().lower(), Role.USER)
    except Exception:
        return Role.USER


def has(role, permission) -> bool:
    """Whether `role` holds `permission`. False on any input that isn't both."""
    if not isinstance(role, Role) or not isinstance(permission, Permission):
        return False
    return permission in _GRANTS.get(role, set())


def current_role(lookup) -> Role:
    """Resolve the current role from an identity port, on each call.

    `lookup` is a single-argument callable — an environment or Streamlit
    secrets read — asked for `ROLE_ENV_VAR`. Any failure, or a value the port
    cannot answer, resolves to `Role.USER`.
    """
    try:
        raw = lookup(ROLE_ENV_VAR)
    except Exception:
        return Role.USER
    return resolve_role(raw)
