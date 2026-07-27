"""Per-provider data-privacy policy: what we enforce, and what we can only state.

The app sends the user's resume and cover letter to an LLM provider, so "is this
being trained on?" is a real question. There is no uniform answer, because
providers differ in whether the question is even answerable per request:

* **OpenRouter** routes to a pool that *includes* providers which train on
  submitted data, and it exposes a request parameter to exclude them. Privacy is
  therefore something we can **enforce** on every call — and must, since the
  default is permissive.
* **OpenAI / Anthropic direct** do not train on API traffic by default and offer
  no request parameter to say so; the guarantee lives in their terms. The most we
  can do is **state** it, with the date we last checked.
* **Anything else** (a self-hosted gateway, an unknown proxy) we know nothing
  about, and silence would read as safety. It is flagged instead.

Those three postures are the :class:`PrivacyLevel` values.

**This is a gate, not a notice.** The first two levels may send; the third raises
:class:`PrivacyNotEnsuredError`, and it raises at *client construction*, so no
client capable of transmitting user content exists for a provider we cannot vouch
for. A warning shown after the resume has been embedded is a post-mortem — the
disclosure has already happened and cannot be taken back. This matches the
fail-closed document screening of ADR-0040, and reverses the disclose-only stance
of ADR-0100 (see ADR-0110).

Request paths call :func:`require_privacy_extra_body`; the UI calls
:func:`policy_for` and :func:`privacy_extra_body`, which do not raise because the
sidebar must be able to describe a posture the app is refusing to send to. One
registry backs both, so what we send and what the UI claims cannot drift.

Adding a provider is a new entry in ``PROVIDER_POLICIES``, not a code change.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .config import (
    ANTHROPIC_BASE_URL,
    OPENAI_BASE_URL,
    OPENROUTER_BASE_URL,
    PRIVACY_POLICY_CHECKED_DATE,
)


class PrivacyNotEnsuredError(RuntimeError):
    """Raised when user content would go to a provider we cannot vouch for.

    Deliberately an exception rather than a flag: telling the user afterwards
    that their resume went somewhere unvetted is a post-mortem, not a control.
    Every client that transmits user content raises this at construction, so an
    unensured provider cannot produce a usable client at all — a call site added
    later inherits the protection instead of having to remember it.
    """


class PrivacyLevel:
    """How the provider's no-training posture is established."""

    # We send a request parameter that excludes training providers.
    ENFORCED = "enforced"
    # The provider's published terms say they don't train; nothing to send.
    PROVIDER_STATED = "provider_stated"
    # Unrecognized provider — no basis for any claim either way.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PrivacyPolicy:
    """The data-privacy posture for one provider, and how to apply it."""

    provider: str  # display name
    level: str  # one of the PrivacyLevel values
    detail: str  # one sentence, shown in the sidebar tooltip
    # Request parameters that enforce the posture, in OpenAI-SDK ``extra_body``
    # shape. Empty for providers that expose no such parameter.
    extra_body: dict = field(default_factory=dict)

    @property
    def is_ensured(self) -> bool:
        """True when the posture rests on more than an unverified assumption."""
        return self.level != PrivacyLevel.UNKNOWN


# To see the UNKNOWN (red) state in the live app, comment out the OpenRouter
# entry below — the transport still points at the real endpoint, so the app keeps
# working while the lookup misses. Do *not* instead edit ``OPENROUTER_BASE_URL``:
# it is both the registry key and the lookup key, so changing it renames both in
# lockstep and the app goes on claiming privacy while every request fails. The
# guard in ``test_privacy`` fails the suite if that constant stops being a real
# OpenRouter endpoint, so the trap can't be sprung silently.
#
# OpenRouter's ``data_collection: "deny"`` excludes providers that may store or
# train on prompts. It is honored by both /chat/completions and /embeddings.
# Deliberately *not* ``zdr: true`` as well: zero-data-retention endpoints are a
# much smaller pool (it would silently break most model choices) and zdr is
# undocumented for /embeddings.
PROVIDER_POLICIES = {
    OPENROUTER_BASE_URL: PrivacyPolicy(
        provider="OpenRouter",
        level=PrivacyLevel.ENFORCED,
        detail=(
            "Every request sends `data_collection: deny`, so OpenRouter will not "
            "route it to a provider that may store or train on your data. A "
            "model served only by such providers will fail rather than leak. "
            "Your OpenRouter account privacy settings (separate toggles for paid "
            "and free models) are the broader control this complements."
        ),
        extra_body={"provider": {"data_collection": "deny"}},
    ),
    OPENAI_BASE_URL: PrivacyPolicy(
        provider="OpenAI",
        level=PrivacyLevel.PROVIDER_STATED,
        detail=(
            "OpenAI states that API data is not used to train their models "
            "unless you opt in. There is no request parameter to enforce this, "
            "so the guarantee rests on their published terms. Requests do send "
            "`store: false` to keep responses out of your account's stored "
            "history."
        ),
        # store=false is about retention rather than training, but it is the one
        # privacy lever the API actually exposes per request, so we pull it.
        extra_body={"store": False},
    ),
    ANTHROPIC_BASE_URL: PrivacyPolicy(
        provider="Anthropic",
        level=PrivacyLevel.PROVIDER_STATED,
        detail=(
            "Anthropic states that API inputs and outputs are not used to train "
            "their models, and are deleted after a short retention window. "
            "There is no request parameter to enforce this, so the guarantee "
            "rests on their published terms."
        ),
    ),
}


def policy_for(base_url) -> PrivacyPolicy:
    """The privacy policy for ``base_url``; a flagged UNKNOWN one if unregistered.

    Fails *loud*, never safe: an unrecognized endpoint gets a policy the UI
    renders as a warning, because "we didn't check" must not look like "you're
    protected".
    """
    policy = PROVIDER_POLICIES.get(base_url)
    if policy is not None:
        return policy
    return PrivacyPolicy(
        provider=base_url or "unknown provider",
        level=PrivacyLevel.UNKNOWN,
        detail=(
            "This provider is not one whose data-usage policy the app knows, so "
            "nothing can be enforced or promised about training on your "
            "documents. Check the provider's terms before uploading anything "
            "you would not want used for training."
        ),
    )


def privacy_extra_body(base_url) -> dict:
    """The ``extra_body`` parameters that apply ``base_url``'s policy.

    Returns a deep copy: callers merge these into request bodies they also put
    their own keys into, and the registry's dicts are shared module state.
    """
    return copy.deepcopy(policy_for(base_url).extra_body)


def require_privacy_extra_body(base_url) -> dict:
    """:func:`privacy_extra_body`, but refuses an unensured provider.

    This is what **request paths** use; the non-raising accessor above is for the
    UI, which has to be able to describe a posture it will not send to. Raising
    here is what makes the policy a gate rather than a notice: ``ENFORCED`` and
    ``PROVIDER_STATED`` both have a real no-training basis (a request parameter,
    or published terms with a verified-on date), while ``UNKNOWN`` has none, and
    an upload is not undoable.
    """
    policy = policy_for(base_url)
    if not policy.is_ensured:
        raise PrivacyNotEnsuredError(
            f"Refusing to send data to {policy.provider}: its data-usage policy "
            "is unknown, so it cannot be guaranteed that your documents and "
            "messages will not be used for training. Register the endpoint in "
            "privacy.PROVIDER_POLICIES if you know its policy."
        )
    return copy.deepcopy(policy.extra_body)


# Privacy params the /embeddings endpoint accepts. OpenRouter's ``provider``
# routing object is honored there; OpenAI's ``store`` belongs to the chat/response
# endpoints only and would be rejected, so it is filtered out rather than sent.
EMBEDDING_SAFE_KEYS = {"provider"}


def require_embedding_privacy_extra_body(base_url) -> dict:
    """:func:`require_privacy_extra_body`, narrowed to what /embeddings accepts."""
    body = require_privacy_extra_body(base_url)
    return {k: v for k, v in body.items() if k in EMBEDDING_SAFE_KEYS}


def status_label(policy: PrivacyPolicy) -> str:
    """The one-line sidebar label — kept terse enough to fit on a single line."""
    if policy.level == PrivacyLevel.ENFORCED:
        return "No data-training risk"
    if policy.level == PrivacyLevel.PROVIDER_STATED:
        return f"Privacy provider-stated on {PRIVACY_POLICY_CHECKED_DATE}"
    return "Privacy not ensured"
