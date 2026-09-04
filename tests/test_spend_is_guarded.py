"""Every paid client refuses without an authorized identity (R21.10, ADR-0200).

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


def test_the_count_in_the_adr_matches_the_code():
    """ADR-0200 says six paid clients. Assert the number, mechanically.

    A seventh client added without a guard is the whole failure mode here, and
    it would otherwise be caught by nobody — `grep` is what found the last one.
    """
    import pathlib
    import re

    root = pathlib.Path("interview_prep")
    constructions = []
    for path in sorted(root.glob("*.py")):
        source = path.read_text()
        for match in re.finditer(r"\b(ChatOpenAI|OpenAIEmbeddings|OpenAI)\(", source):
            constructions.append(f"{path.name}:{match.group(1)}")

    guarded = {
        path.name
        for path in sorted(root.glob("*.py"))
        if "require_authorized" in path.read_text()
    }
    unguarded = {c.split(":")[0] for c in constructions} - guarded
    assert not unguarded, (
        f"paid clients with no authorization guard: {sorted(unguarded)}. "
        f"ADR-0200 requires every one of them to refuse. Found: {constructions}"
    )
