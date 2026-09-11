"""Who may use this app at all — the allowlist, as a pure function (§ 21).

Deliberately separate from `permissions.py`, which grades what an authorized
role may *do*. This module answers the prior question: whether there is a role
at all (R21.14). Folding the two together would make "nobody signed in" and
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
  loses privilege (R21.9). A typo in secrets should cost a sidebar tab, not
  lock a legitimate user out of an account they were granted.
* An **absent, unreadable or non-mapping table** authorizes nobody (R21.10) —
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


class Unauthorized(Exception):
    """Raised when a paid operation is attempted without an authorized identity.

    Lives here rather than in `agent.py` because it is raised by six different
    paid clients, and none of them should have to import an agent framework to
    refuse. Deliberately an exception rather than a polite empty result: every
    path that can reach it is a programming or configuration fault, and a
    caller that swallows it silently is the failure this guard exists to
    prevent.
    """


def require_authorized(identity, what="this operation"):
    """Refuse `what` unless `identity` is an authorized `Identity` (R21.12).

    Called by every client that spends the operator's OpenRouter credit — the
    agent, the guardrail, the query condenser, the web researcher, and the two
    embedding sites. ADR-0200 counted six and the first implementation guarded
    one, which is the failure shape this project keeps finding: a control that
    looks like it covers spending while covering a sixth of it.

    `isinstance` rather than a truth test, per R21.13: the caller must pass
    something that *says* it is authorized. A bare `True`, a `Role`, or a
    truthy dict is not that, and accepting one would make the guard
    satisfiable by accident.
    """
    if isinstance(identity, Identity) and identity.is_authorized:
        return
    who = getattr(identity, "email", None) or "an unidentified caller"
    raise Unauthorized(
        f"refusing to spend on behalf of {who}: {what} has no authorized "
        "identity. Pass `identity=` an authorized `Identity` from "
        "`interview_prep.authorization.authorize`."
    )


@dataclass(frozen=True)
class Identity:
    """A decided identity: who they are, and whether they may be here.

    Frozen because this object *is* the proof of authorization handed to the
    agent (R21.13). A caller that could set `role` after the fact would be able
    to promote itself past the allowlist, which is the whole control.

    `role is None` means refused. It is not `Role.USER`: an unauthorized
    visitor and a least-privileged authorized one must not be the same value,
    or the agent cannot tell them apart at the point it decides to spend.
    """

    email: str | None = None
    role: Role | None = None
    #: Why a refusal happened, for wording alone — never for deciding access.
    #: `"unverified"` means the provider signed the person in but did not
    #: assert the address is verified, which is a *deployment* problem: the
    #: allowlist cannot help, and telling the operator to edit it (as the first
    #: version did) sends them after a fault that is not there. Google emits
    #: `email_verified`; Microsoft Entra ID does not emit it at all, so on that
    #: provider every user is refused and only this field explains it.
    refusal: str | None = None

    @property
    def is_authorized(self) -> bool:
        return self.role is not None


#: The identity of a visitor who has not signed in, or whose session was
#: refused. Authorized for nothing; carries no address.
ANONYMOUS = Identity()


def normalise_email(value):
    """An email reduced to its comparison form, or None if there isn't one.

    Public because two other modules need *this* rule and not a second one like
    it: the spend ledger keys its rows on the same comparison form the
    allowlist matches on, and a ledger that normalised differently would give
    one person two rows and twice the cap. Reaching for it through a private
    name was an invitation to reimplement it instead.

    Casing and surrounding whitespace are not a security boundary (R21.8);
    treating them as one only produces refusals nobody can explain. Anything
    that is not a usable string — no email claim, a proxy returning an object,
    a provider handing back `None` — is not an identity this app can act on
    (R21.3), and becomes None here rather than being coerced into one.
    """
    if not isinstance(value, str):
        return None
    normalised = value.strip().lower()
    return normalised or None


#: The name the rest of this module was written against.
_normalise = normalise_email


def authorize(email, *, table, email_verified=False) -> Identity:
    """Decide an authenticated email against the operator's allowlist.

    `table` maps authorized email to role name. **Presence is authorization
    and absence is refusal** (R21.7) — one source of truth, so an allowlist and
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
        # (R21.13), which is also how an operator notices someone trying to
        # sign in as them — and says *why*, because "not verified" and "not on
        # the list" need different fixes by different people.
        return Identity(email=normalised, refusal="unverified")

    # An unusable table authorizes nobody (R21.10). `items()` rather than a
    # lookup, because the table's own keys need normalising too — an operator
    # will paste a capitalised address into secrets sooner or later — and
    # because it forces anything that merely *looks* mapping-shaped to prove
    # it here, where the failure is contained.
    try:
        entries = list(table.items())
    except Exception:
        return Identity(email=normalised, refusal="not_allowlisted")

    for key, role_name in entries:
        if _normalise(key) == normalised:
            # Authorized. An unrecognised role name costs privilege, not
            # access (R21.9): `resolve_role` already fails closed to USER.
            return Identity(email=normalised, role=resolve_role(role_name))

    return Identity(email=normalised, refusal="not_allowlisted")
