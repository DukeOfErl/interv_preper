"""Opt-in end-to-end tests against the real GitHub remote MCP server.

Unlike ``test_github_mcp.py`` (which fakes the transport), these make real
network calls: one ``tools/list`` discovery and one ``tools/call``. They are
skipped unless ``GITHUB_PAT`` is set, so the default suite never depends on
the network or a token. Run just these with:  ``uv run pytest -m integration``.

The discovery test prints the server's full tool list — check it whenever
these fail: ``GITHUB_MCP_ALLOWED_TOOLS`` must name tools exactly as the
server declares them, and the server's names have drifted across releases.
"""

import asyncio
import os

import pytest

from interview_prep.config import GITHUB_MCP_ALLOWED_TOOLS
from interview_prep.github_mcp import GitHubMCP

pytestmark = pytest.mark.integration

PAT = os.getenv("GITHUB_PAT")
requires_pat = pytest.mark.skipif(
    not PAT, reason="GITHUB_PAT not set; skipping live GitHub MCP test"
)


@pytest.fixture(scope="module")
def discovered():
    client = GitHubMCP(pat=PAT)
    client.discover()
    return client


@requires_pat
def test_discovery_finds_every_allowlisted_tool(discovered):
    # Print the full live list so a drifted allowlist name is easy to
    # reconcile against what the server actually declares.
    all_names = sorted(t.name for t in asyncio.run(discovered._list_tools()))
    print("\nAll server tools:", all_names)
    print("Kept by allowlist:", sorted(discovered.tool_names))
    missing = set(GITHUB_MCP_ALLOWED_TOOLS) - discovered.tool_names
    assert not missing, (
        f"allowlisted tools not offered by the server: {missing}; "
        f"the server offers: {all_names}"
    )


@requires_pat
def test_call_reads_a_public_file(discovered):
    result = discovered.call(
        "get_file_contents",
        {"owner": "octocat", "repo": "Hello-World", "path": "README"},
    )
    assert not result.errored
    # The body arrives split out of the inline text, ready to be screened and
    # indexed rather than returned to the model whole.
    assert "Hello World" in result.file_text
    assert "Hello World" not in result.text
    assert result.source == "octocat/Hello-World/README"
    assert "README" in result.terms


@requires_pat
def test_directory_listing_stays_inline_and_yields_paths(discovered):
    result = discovered.call(
        "get_file_contents", {"owner": "octocat", "repo": "Hello-World", "path": "/"}
    )
    assert not result.errored
    assert result.file_text == ""
    assert "README" in result.terms
