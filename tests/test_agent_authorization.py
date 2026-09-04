"""The agent refuses to spend without an authorized identity (R21.12, R21.13).

This is the half of the guard that does not live in the page. `chat_bot.py`
refuses early so the message is clean, but `evals/` and `tests/` reach the
agent without ever touching Streamlit — so a refusal that lives only in
`chat_bot.main()` protects one of the app's three callers, and nothing
announces the day a fourth appears.

The test that matters most here is the last one: it *proposes a new caller*,
which is the free audit of every guard written for the first. A caller that
constructs an `InterviewAgent` and forgets about authorization entirely must
fail closed, not spend.

Deliberately not asserted: how the page words the refusal. That is presentation
and lives in `test_chat_bot_wiring.py`.
"""

from __future__ import annotations

import pytest

from interview_prep.agent import InterviewAgent, Unauthorized
from interview_prep.authorization import ANONYMOUS, Identity, authorize
from interview_prep.permissions import Role

AUTHORIZED = authorize(
    "candidate@example.com",
    table={"candidate@example.com": "user"},
    email_verified=True,
)


class ExplodingModel:
    """A model that fails loudly if anything reaches it.

    The assertion is not merely that an exception was raised — it is that the
    refusal happened *before* the model was called. A guard that raises after
    spending has already cost the operator the turn it was meant to prevent.
    """

    called = False

    def bind_tools(self, *args, **kwargs):
        return self

    def bind(self, *args, **kwargs):
        # Present deliberately. Fault seeding found that a bypassed guard died
        # on `AttributeError: no attribute 'bind'` deep inside `create_agent`
        # before ever reaching `invoke`/`stream` — so the seeded defects were
        # caught by an incomplete double rather than by the assertion written
        # for them. A double that crashes for the wrong reason is a test that
        # will stop working the moment the framework's internals change.
        ExplodingModel.called = True
        raise AssertionError("the model was bound without an authorized identity")

    def invoke(self, *args, **kwargs):  # pragma: no cover - must never run
        ExplodingModel.called = True
        raise AssertionError("the model was called without an authorized identity")

    def stream(self, *args, **kwargs):  # pragma: no cover - must never run
        ExplodingModel.called = True
        raise AssertionError("the model was called without an authorized identity")


def drive(identity, **kwargs):
    agent = InterviewAgent(
        api_key="k", model="m", chat_model=ExplodingModel(), identity=identity, **kwargs
    )
    return "".join(agent.stream_reply("sys", [{"role": "user", "content": "go"}]))


@pytest.fixture(autouse=True)
def reset_model():
    ExplodingModel.called = False
    yield
    assert not ExplodingModel.called, "a refused turn still reached the model"


# --- refusal ----------------------------------------------------------------


@pytest.mark.parametrize(
    "identity",
    [
        ANONYMOUS,
        Identity(),
        Identity(email="stranger@example.com"),
        Identity(email="stranger@example.com", role=None),
        None,
    ],
    ids=["anonymous", "empty", "refused-with-email", "explicit-none", "omitted"],
)
def test_an_unauthorized_identity_cannot_take_a_turn(identity):
    with pytest.raises(Unauthorized):
        drive(identity)


def test_constructing_the_agent_without_an_identity_at_all_is_refused():
    """The new-caller audit: someone adds a script, copies the constructor from
    an old example, and never learns the keyword exists. It must not spend."""
    agent = InterviewAgent(api_key="k", model="m", chat_model=ExplodingModel())
    with pytest.raises(Unauthorized):
        "".join(agent.stream_reply("sys", [{"role": "user", "content": "go"}]))


@pytest.mark.parametrize(
    "impostor",
    ["dev", Role.DEV, True, 1, {"role": "dev"}, object()],
    ids=["role-string", "bare-role", "true", "one", "dict", "object"],
)
def test_something_that_merely_looks_authorized_is_not(impostor):
    """R21.13 says the caller passes something that says, in as many words,
    that it is authorized. A truthy value is not that."""
    with pytest.raises(Unauthorized):
        drive(impostor)


def test_the_refusal_names_the_address_it_refused():
    # So an operator reading a traceback from `evals/` can tell which account
    # was rejected, rather than only that something was.
    with pytest.raises(Unauthorized) as excinfo:
        drive(Identity(email="stranger@example.com"))
    assert "stranger@example.com" in str(excinfo.value)


def test_the_refusal_is_raised_before_the_stream_is_consumed():
    """`stream_reply` is a generator, so a check in its body would not run
    until the first token was pulled — and a caller that builds the generator
    and abandons it would look guarded while a caller that iterates it is the
    only one actually checked. Raise on the call, not on the first `next`.
    """
    agent = InterviewAgent(
        api_key="k", model="m", chat_model=ExplodingModel(), identity=ANONYMOUS
    )
    with pytest.raises(Unauthorized):
        agent.stream_reply("sys", [{"role": "user", "content": "go"}])


# --- admission --------------------------------------------------------------


def test_an_authorized_identity_is_admitted():
    """The other direction, and the one a fail-closed suite forgets.

    Every test above passes just as well against a guard that refuses
    *everyone* — which would leave the app unusable and the suite green. This
    asserts the gate opens. It stops at the refusal boundary rather than
    driving a full turn (that is `test_agent.py`'s job): the generator must
    come back unraised, and the model is never pulled.
    """
    agent = InterviewAgent(
        api_key="k", model="m", chat_model=ExplodingModel(), identity=AUTHORIZED
    )
    stream = agent.stream_reply("sys", [{"role": "user", "content": "go"}])
    assert stream is not None
