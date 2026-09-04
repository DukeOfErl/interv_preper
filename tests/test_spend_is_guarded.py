"""Every paid client refuses without an authorized identity (R21.12, ADR-0200).

ADR-0200 counted the money and found **six** clients that spend the operator's
OpenRouter credit: the agent, the guardrail, the query condenser, the web
researcher, and the two embedding sites. It then said, in as many words, that
guarding only the agent "would have guarded one of six while looking like it
guarded spending, which is the exact failure shape this project keeps finding."

The first implementation guarded one of six. The code-review pass caught the
ADR contradicting the code — `grep -rn "identity" interview_prep/*.py` returned
nothing outside `agent.py` and `authorization.py`. This file is the assertion
that closes it, and it is written as a table over all six rather than six
separate tests so that adding a seventh client without a guard is a visible
omission rather than an invisible one.

WHERE THE GUARD SITS, and why it is not unconditional: every one of these
accepts an injected client or embeddings object for tests. When a fake is
injected, no money moves, so no identity is required — the guard fires exactly
where the *real* paid client would be constructed from the operator's key. A
caller that builds its own OpenAI client and injects it has already taken the
key into its own hands and is past anything this module could enforce.
"""

from __future__ import annotations

import pathlib

import pytest

from interview_prep.authorization import ANONYMOUS, Identity, Unauthorized, authorize
from interview_prep.permissions import Role

AUTHORIZED = authorize(
    "operator@example.com",
    table={"operator@example.com": "dev"},
    email_verified=True,
)

REFUSED = [
    pytest.param(ANONYMOUS, id="anonymous"),
    pytest.param(Identity(email="stranger@example.com"), id="not-allowlisted"),
    pytest.param(None, id="omitted"),
    pytest.param(True, id="truthy"),
    pytest.param(Role.DEV, id="bare-role"),
    pytest.param("dev", id="role-string"),
]


def build_guardrail(identity):
    from interview_prep.guardrails import JailbreakGuard

    return JailbreakGuard(api_key="k", identity=identity)


def build_condenser(identity):
    from interview_prep.query_rewrite import QueryCondenser

    return QueryCondenser(api_key="k", identity=identity)


def build_researcher(identity):
    from interview_prep.web_research import WebResearcher

    return WebResearcher(api_key="k", identity=identity)


def build_index(identity):
    from interview_prep.retrieval import DocumentIndex

    return DocumentIndex(api_key="k", embedding_model="e", identity=identity)


def build_knowledgebase(identity, tmp_path):
    from interview_prep.knowledgebase import KnowledgeBase

    return KnowledgeBase(
        db_path=str(tmp_path / "kb.db"), api_key="k", identity=identity
    )


def build_agent(identity):
    from interview_prep.agent import InterviewAgent

    agent = InterviewAgent(api_key="k", model="m", identity=identity)
    # The agent defers its refusal to the turn, because constructing one is
    # free — `create_agent` is what costs. Drive it to the boundary.
    return agent.stream_reply("sys", [{"role": "user", "content": "go"}])


PAID_CLIENTS = [
    pytest.param(build_guardrail, id="guardrail"),
    pytest.param(build_condenser, id="query-condenser"),
    pytest.param(build_researcher, id="web-researcher"),
    pytest.param(build_index, id="document-embeddings"),
    pytest.param(build_agent, id="agent"),
]


@pytest.mark.parametrize("build", PAID_CLIENTS)
@pytest.mark.parametrize("identity", REFUSED)
def test_a_paid_client_refuses_without_an_authorized_identity(build, identity):
    with pytest.raises(Unauthorized):
        build(identity)


@pytest.mark.parametrize("identity", REFUSED)
def test_the_knowledge_base_refuses_without_an_authorized_identity(
    identity, tmp_path
):
    # Separate only because it needs a scratch database path.
    with pytest.raises(Unauthorized):
        build_knowledgebase(identity, tmp_path)


# --- the other direction: the gate must open --------------------------------
#
# Every test above passes just as well against a guard that refuses everyone,
# which would leave the app unusable and this file green.


@pytest.mark.parametrize("build", PAID_CLIENTS)
def test_an_authorized_identity_is_admitted(build):
    assert build(AUTHORIZED) is not None


def test_the_knowledge_base_admits_an_authorized_identity(tmp_path):
    assert build_knowledgebase(AUTHORIZED, tmp_path) is not None


# --- the guard is about spending, not about ceremony ------------------------


def test_an_injected_client_needs_no_identity():
    """A fake client spends nothing, so it must not require an identity.

    This is what keeps the guard honest rather than merely noisy: it fires
    where the operator's key would build a real paid client, not on every
    construction. Dozens of existing tests inject fakes precisely so they
    never touch the network, and demanding an identity from them would be
    ceremony that teaches everyone to pass one reflexively.
    """
    from interview_prep.guardrails import JailbreakGuard
    from interview_prep.query_rewrite import QueryCondenser

    assert JailbreakGuard(api_key="k", client=object()) is not None
    assert QueryCondenser(api_key="k", client=object()) is not None


PAID_CLIENT_CONSTRUCTORS = ("ChatOpenAI", "OpenAIEmbeddings", "OpenAI")

#: ADR-0200 counted the money and found six. Asserted as a number, not just as
#: a set difference, so a seventh construction is a visible failure even if it
#: lands in a module that already guards one.
EXPECTED_PAID_CLIENTS = 6


def paid_client_constructions():
    """Every place `interview_prep` builds a client that spends money."""
    import re

    # Absolute, because pytest can be invoked from any cwd. The first version
    # of this used `pathlib.Path("interview_prep")`, so running from anywhere
    # but the repo root globbed nothing, found zero constructions, and passed
    # green having checked absolutely nothing — the same silent-pass shape this
    # whole file exists to prevent. Every other path-sensitive test on this
    # branch was made absolute for exactly this reason.
    root = pathlib.Path(__file__).resolve().parent.parent / "interview_prep"
    found = []
    for path in sorted(root.glob("*.py")):
        source = path.read_text()
        for line_no, line in enumerate(source.splitlines(), 1):
            for name in PAID_CLIENT_CONSTRUCTORS:
                if re.search(rf"(?<![\w.]){name}\(", line):
                    found.append((path.name, line_no, name, line.strip()))
    return found


def test_every_paid_client_construction_is_guarded():
    """ADR-0200's claim, asserted mechanically rather than in prose.

    The ADR says six paid clients must refuse without an authorized identity,
    and the first implementation guarded one — which `grep` found and no test
    did. This is the standing check.

    It looks at each CONSTRUCTION, not each module. Checking per module (the
    first version did) is satisfied by `agent.py`'s `require_authorized`
    re-export line alone, so a second, unguarded `OpenAI(...)` added to any
    module that already imports the name would pass silently.
    """
    constructions = paid_client_constructions()

    assert len(constructions) == EXPECTED_PAID_CLIENTS, (
        f"ADR-0200 says {EXPECTED_PAID_CLIENTS} paid clients; found "
        f"{len(constructions)}: {[(f, n, k) for f, n, k, _ in constructions]}. "
        "A new one needs an authorization guard AND an entry in this file's "
        "PAID_CLIENTS table — or the ADR needs amending."
    )

    root = pathlib.Path(__file__).resolve().parent.parent / "interview_prep"
    unguarded = []
    for filename, line_no, name, text in constructions:
        source = (root / filename).read_text()
        # The guard must be called in the same module, not merely imported.
        if "require_authorized(" not in source.replace(
            "from .authorization import", ""
        ):
            unguarded.append(f"{filename}:{line_no} {name}")
    assert not unguarded, (
        f"paid clients with no authorization guard: {unguarded}. "
        "ADR-0200 requires every one of them to refuse."
    )


def test_the_construction_scan_actually_finds_something():
    """The scan must prove it can see, or its all-clear means nothing.

    A path bug, a renamed package or a changed constructor name would all make
    `paid_client_constructions()` return an empty list, and an empty list
    satisfies every "nothing unguarded" assertion trivially.
    """
    constructions = paid_client_constructions()
    assert constructions, "the scan found no paid clients at all — it is broken"
    modules = {f for f, _, _, _ in constructions}
    assert "agent.py" in modules, modules
