"""Who may use this app at all — the allowlist, as a pure function (§ 21).

Deliberately separate from `permissions.py`, which grades what an authorized
role may *do*. This module answers the prior question: whether there is a role
at all (R21.12). Folding the two together would make "nobody signed in" and
"signed in with few rights" the same state, and they fail in opposite
directions — one must block a paid turn outright, the other must merely hide a
tab.

**OIDC answers *who is this*; it does not answer *may they be here*.** Any
Google account holder on earth can complete a Google sign-in, and every turn of
this app spends the operator's own OpenRouter credit. So a successful login is
never sufficient grounds to serve someone: authentication produces an email,
and this module decides — from an explicit table the operator maintains —
whether that email is served.

Two failures are graded in opposite directions on purpose:

* An authorized email whose **role name** is unrecognized keeps access and
  loses privilege (R21.7). A typo in secrets should cost a sidebar tab, not
  lock a legitimate user out of an account they were granted.
* An **absent, unreadable or non-mapping table** authorizes nobody (R21.8) —
  including the operator. This is the one place in this codebase where failing
  closed is the *expensive* direction, and it is chosen knowingly: a
  misconfigured deployment that serves no one fails loudly and corrects itself
  within minutes, while one that silently admits the world to a funded API key
  produces no error, no log line, and a bill.

Like `permissions.py`, this imports neither Streamlit nor any agent framework
(R20.4), so the same check serves the page, the agent path and the tests. The
Streamlit adapter that reads `st.user` and `st.secrets` lives in `chat_bot.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .permissions import Role, resolve_role


@dataclass(frozen=True)
class Identity:
    """A decided identity: who they are, and whether they may be here.

    Frozen because this object *is* the proof of authorization handed to the
    agent (R21.11). A caller that could set `role` after the fact would be able
    to promote itself past the allowlist, which is the whole control.

    `role is None` means refused. It is not `Role.USER`: an unauthorized
    visitor and a least-privileged authorized one must not be the same value,
    or the agent cannot tell them apart at the point it decides to spend.
    """

    email: str | None = None
    role: Role | None = None

    @property
    def is_authorized(self) -> bool:
        return self.role is not None


#: The identity of a visitor who has not signed in, or whose session was
#: refused. Authorized for nothing; carries no address.
ANONYMOUS = Identity()


def _normalise(value):
    """An email reduced to its comparison form, or None if there isn't one.

    Casing and surrounding whitespace are not a security boundary (R21.6);
    treating them as one only produces refusals nobody can explain. Anything
    that is not a usable string — no email claim, a proxy returning an object,
    a provider handing back `None` — is not an identity this app can act on
    (R21.3), and becomes None here rather than being coerced into one.
    """
    if not isinstance(value, str):
        return None
    normalised = value.strip().lower()
    return normalised or None


def authorize(email, *, table, email_verified=False) -> Identity:
    """Decide an authenticated email against the operator's allowlist.

    `table` maps authorized email to role name. **Presence is authorization
    and absence is refusal** (R21.5) — one source of truth, so an allowlist and
    a role map cannot drift apart.

    `email_verified` must be exactly `True` — the OIDC claim of the same name,
    which R21.3 requires and which the first version of this function ignored.
    It **defaults to False**, so a caller that has not heard of verification
    fails closed instead of inheriting the permissive behaviour.

    Why an address alone is not enough: `st.login` is provider-agnostic and the
    provider is a secrets setting, not a code constant. On any IdP where a user
    can self-assert an address at registration, a stranger can register *as* an
    allowlisted address and receive a validly signed token for it. Every
    signature, origin and expiry check then passes, and the session is
    indistinguishable from the real person's. The unverified claim is the only
    thing that distinguishes them, so it is not optional.

    Not truthiness: a provider returning the string `"true"` is returning a
    string, and guessing what it meant is how a bypass gets built. Normalising
    provider quirks is the adapter's job (`chat_bot.current_identity`); this
    function requires the decided boolean.

    Never raises. Every path that cannot decide returns an unauthorized
    identity, because this is called on the way to spending money and an
    exception escaping it would be handled by whichever caller happened to be
    first.
    """
    normalised = _normalise(email)
    if normalised is None:
        return ANONYMOUS

    if email_verified is not True:
        # Carried, not discarded: the page names the address it refused
        # (R21.9), which is also how an operator notices someone trying to
        # sign in as them.
        return Identity(email=normalised)

    # An unusable table authorizes nobody (R21.8). `items()` rather than a
    # lookup, because the table's own keys need normalising too — an operator
    # will paste a capitalised address into secrets sooner or later — and
    # because it forces anything that merely *looks* mapping-shaped to prove
    # it here, where the failure is contained.
    try:
        entries = list(table.items())
    except Exception:
        return Identity(email=normalised)

    for key, role_name in entries:
        if _normalise(key) == normalised:
            # Authorized. An unrecognised role name costs privilege, not
            # access (R21.7): `resolve_role` already fails closed to USER.
            return Identity(email=normalised, role=resolve_role(role_name))

    return Identity(email=normalised)
