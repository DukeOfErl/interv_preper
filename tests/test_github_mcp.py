"""Offline tests for the GitHub MCP client (no network, no real server).

The async transport methods (``_list_tools`` / ``_call_tool``) are replaced
with fakes returning SimpleNamespace stand-ins for the SDK's pydantic types;
everything above them — allowlist filtering, schema conversion, result
splitting, argument overrides, the fabrication check — is exercised for real.
"""

import json
from types import SimpleNamespace

from interview_prep.github_mcp import (
    GITHUB_CONSENT_POLICY,
    GitHubMCP,
    _drop_directory_sizes,
    unquoted_code_blocks,
    unverified_references,
)


def make_tool(name, description="Does a thing.", schema=None):
    return SimpleNamespace(
        name=name,
        description=description,
        input_schema=schema
        or {"type": "object", "properties": {"owner": {"type": "string"}}},
    )


def make_client(tools=(), call_result=None, allowed=("alpha", "beta")):
    client = GitHubMCP(pat="pat-123", allowed_tools=allowed)

    async def fake_list_tools():
        return list(tools)

    async def fake_call_tool(name, arguments):
        fake_call_tool.calls.append((name, arguments))
        return call_result

    fake_call_tool.calls = []
    client._list_tools = fake_list_tools
    client._call_tool = fake_call_tool
    return client


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def resource_block(text, uri="repo://x"):
    return SimpleNamespace(
        type="resource", resource=SimpleNamespace(uri=uri, text=text)
    )


def server_result(*content, is_error=False):
    return SimpleNamespace(content=list(content), is_error=is_error)


# --- discovery -------------------------------------------------------------


def test_discover_keeps_only_allowlisted_tools():
    client = make_client(
        tools=[make_tool("alpha"), make_tool("create_issue"), make_tool("beta")]
    )
    specs = client.discover()
    assert [s["function"]["name"] for s in specs] == ["alpha", "beta"]
    assert client.tool_names == {"alpha", "beta"}


def test_discover_converts_to_chat_completions_shape():
    schema = {"type": "object", "properties": {"repo": {"type": "string"}}}
    client = make_client(tools=[make_tool("alpha", "Reads a file.", schema)])
    (spec,) = client.discover()
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "alpha"
    assert spec["function"]["parameters"] is schema


def test_discover_appends_consent_policy_to_descriptions():
    client = make_client(tools=[make_tool("alpha", "Reads a file.")])
    (spec,) = client.discover()
    assert spec["function"]["description"].startswith("Reads a file.")
    assert GITHUB_CONSENT_POLICY in spec["function"]["description"]


def test_discover_strips_parameters_we_override(monkeypatch):
    # Advertising a knob whose value we discard invites wasted (and error-prone)
    # effort from the model.
    monkeypatch.setattr(
        "interview_prep.github_mcp.GITHUB_MCP_ARG_OVERRIDES",
        {"alpha": {"fields": ["name"]}},
    )
    schema = {
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "fields": {"type": "array"},
        },
        "required": ["owner"],
    }
    client = make_client(tools=[make_tool("alpha", schema=schema)])
    (spec,) = client.discover()
    assert list(spec["function"]["parameters"]["properties"]) == ["owner"]
    assert spec["function"]["parameters"]["required"] == ["owner"]
    # The original schema object is left untouched.
    assert "fields" in schema["properties"]


def test_discover_tolerates_missing_description():
    client = make_client(tools=[make_tool("alpha", description=None)])
    (spec,) = client.discover()
    assert spec["function"]["description"] == GITHUB_CONSENT_POLICY


def test_cached_specs_skip_discovery():
    cached = [{"type": "function", "function": {"name": "alpha"}}]
    client = GitHubMCP(pat="pat-123", specs=cached)
    assert client.specs == cached
    assert client.tool_names == {"alpha"}


def test_specs_empty_before_discovery():
    client = GitHubMCP(pat="pat-123")
    assert client.specs == []
    assert client.tool_names == set()


# --- calls -----------------------------------------------------------------


def test_listing_stays_inline_with_no_file_text():
    listing = '[{"name":"core.py","path":"src/core.py","size":12,"type":"file"}]'
    client = make_client(call_result=server_result(text_block(listing)))
    result = client.call("alpha", {"owner": "octocat", "repo": "hello"})
    assert result.text == listing
    assert result.file_text == ""
    assert result.source == ""
    # Paths in a listing are harvested for the fabrication check.
    assert "src/core.py" in result.terms


def test_file_body_is_split_out_of_the_inline_text():
    # The GitHub server returns file bodies as embedded resources; the text
    # block alongside them only says "successfully downloaded".
    client = make_client(
        call_result=server_result(
            text_block("successfully downloaded text file (SHA: abc)"),
            resource_block("def run():\n    return compute_total_volume()\n"),
        )
    )
    result = client.call(
        "alpha", {"owner": "octocat", "repo": "hello", "path": "src/core.py"}
    )
    assert "compute_total_volume" in result.file_text
    assert "compute_total_volume" not in result.text
    assert result.source == "octocat/hello/src/core.py"
    # Identifiers from the body and the path itself become verified terms.
    assert {"compute_total_volume", "src/core.py"} <= result.terms


def test_call_applies_argument_overrides(monkeypatch):
    monkeypatch.setattr(
        "interview_prep.github_mcp.GITHUB_MCP_ARG_OVERRIDES",
        {"alpha": {"minimal_output": True}},
    )
    client = make_client(call_result=server_result(text_block("[]")))
    client.call("alpha", {"query": "user:octocat", "minimal_output": False})
    (_, sent), = client._call_tool.calls
    assert sent == {"query": "user:octocat", "minimal_output": True}


def test_call_tolerates_non_dict_arguments():
    client = make_client(call_result=server_result(text_block("[]")))
    assert client.call("alpha", None).text == "[]"


def test_call_server_error_is_reported_as_errored():
    client = make_client(call_result=server_result(text_block("boom"), is_error=True))
    result = client.call("alpha", {})
    assert result.errored
    assert "boom" in result.text


def test_call_empty_content_is_errored():
    client = make_client(call_result=server_result())
    assert client.call("alpha", {}).errored


# --- fabrication check -----------------------------------------------------


def test_unverified_references_flags_invented_file_and_function():
    seen = {"src/cytocalc/core.py", "read_diameter_csv"}
    reply = (
        "In core.py, `diameter_histogram_to_cytoplasm_fraction` converts bins, "
        "and read_diameter_csv() parses the CSV."
    )
    flagged = unverified_references(reply, seen)
    assert flagged == ["diameter_histogram_to_cytoplasm_fraction"]


def test_unverified_references_matches_a_file_by_basename():
    # "core.py" is verified when the listing showed src/pkg/core.py.
    assert unverified_references("See core.py", {"src/pkg/core.py"}) == []


def test_unverified_references_flags_a_file_never_seen():
    assert unverified_references("See setup.py", {"src/pkg/core.py"}) == ["setup.py"]


def test_unverified_references_ignores_simple_words():
    # A suggestion ("use pytest") is not a claim about the repo, so single
    # short words are never flagged — false alarms would train users to
    # ignore the warning.
    reply = "You could add `pytest` and call main() to check numpy usage."
    assert unverified_references(reply, {"src/core.py"}) == []


def test_unverified_references_silent_when_nothing_was_read():
    # No GitHub tool ran this session: nothing to verify against.
    assert unverified_references("In core.py, foo_bar_baz() runs.", set()) == []


def test_unverified_references_deduplicates():
    reply = "`some_invented_helper` again: `some_invented_helper`"
    assert unverified_references(reply, {"x.py"}) == ["some_invented_helper"]


# --- verbatim quote check --------------------------------------------------

_REAL_FILE = '''
def read_diameter_csv(path: str) -> tuple[list[float], list[float]]:
    """Read a CSV file with columns diameter_um and count."""
    frame = pandas.read_csv(path)
    if "diameter_um" not in frame.columns:
        raise ValueError("input CSV must contain diameter_um")
    return frame["diameter_um"].tolist(), frame["count"].tolist()
'''


def test_unquoted_code_flags_an_invented_block():
    reply = (
        "Your parser does this:\n\n```python\n"
        "def diameter_histogram_to_fraction(bins, counts, nucleus_fraction):\n"
        "    volumes = diameter_bins_to_volumes(bins)\n"
        "    total_cytoplasm = sum(v * c for v, c in zip(volumes, counts))\n"
        "    return total_cytoplasm / sum(volumes)\n"
        "```\n"
    )
    flagged = unquoted_code_blocks(reply, [_REAL_FILE])
    assert len(flagged) == 1
    assert flagged[0].startswith("def diameter_histogram_to_fraction")


def test_unquoted_code_accepts_a_real_quote():
    reply = (
        "Here is what you wrote:\n\n```python\n"
        '    frame = pandas.read_csv(path)\n'
        '    if "diameter_um" not in frame.columns:\n'
        '        raise ValueError("input CSV must contain diameter_um")\n'
        "```\n"
    )
    assert unquoted_code_blocks(reply, [_REAL_FILE]) == []


def test_unquoted_code_tolerates_reindentation_and_elision():
    # Copied but dedented, with one line dropped: still recognisably a quote.
    reply = (
        "```python\n"
        'frame = pandas.read_csv(path)\n'
        'if "diameter_um" not in frame.columns:\n'
        'raise ValueError("input CSV must contain diameter_um")\n'
        "```\n"
    )
    assert unquoted_code_blocks(reply, [_REAL_FILE]) == []


def test_unquoted_code_ignores_short_illustrations():
    # A one-or-two-line snippet is the interviewer suggesting something, not
    # claiming to quote the candidate.
    reply = "You could add:\n\n```python\nassert bins, 'no bins supplied'\n```\n"
    assert unquoted_code_blocks(reply, [_REAL_FILE]) == []


def test_unquoted_code_silent_when_nothing_was_read():
    reply = "```python\ndef a():\n    return invented_helper()\n    # more\n```"
    assert unquoted_code_blocks(reply, []) == []


def test_unverified_references_recognises_dotfiles_from_a_listing():
    # Regression: normalizing with strip("./") ate the leading dot, so every
    # dotfile in a real listing was reported as invented.
    seen = {".gitlab-ci.yml", ".gitignore", "README.md", "pyproject.toml"}
    reply = "Top-level items: .gitignore, .gitlab-ci.yml, pyproject.toml, README.md."
    assert unverified_references(reply, seen) == []


def test_unverified_references_still_flags_an_unseen_dotfile():
    assert unverified_references("See .travis.yml", {".gitlab-ci.yml"}) == [
        ".travis.yml"
    ]


def test_unverified_references_normalizes_a_relative_prefix():
    assert unverified_references("See ./src/core.py", {"src/core.py"}) == []


def test_unverified_references_ignores_technology_names():
    # "Node.js" matches the file-extension pattern but names a technology.
    seen = {"src/core.py"}
    reply = "You could pair this with Node.js, Vue.js or D3.js on the front end."
    assert unverified_references(reply, seen) == []


def test_unverified_references_still_flags_a_real_looking_js_path():
    assert unverified_references("See src/app.js", {"src/core.py"}) == ["src/app.js"]


def test_spec_drops_an_overridden_parameter_from_required(monkeypatch):
    monkeypatch.setattr(
        "interview_prep.github_mcp.GITHUB_MCP_ARG_OVERRIDES",
        {"alpha": {"fields": ["name"]}},
    )
    schema = {
        "type": "object",
        "properties": {"owner": {}, "fields": {}},
        "required": ["owner", "fields"],
    }
    client = make_client(tools=[make_tool("alpha", schema=schema)])
    (spec,) = client.discover()
    # A required property that no longer exists is rejected by strict providers.
    assert spec["function"]["parameters"]["required"] == ["owner"]


def test_spec_supplies_an_object_schema_when_the_tool_declares_none():
    # A tool with no inputs may carry no schema; the chat-completions API still
    # requires an object, or the whole tools payload is rejected.
    tool = make_tool("alpha")
    tool.input_schema = None
    client = make_client(tools=[tool])
    (spec,) = client.discover()
    assert spec["function"]["parameters"] == {"type": "object", "properties": {}}


# --- a zero we supplied must not read as evidence ----------------------------


def test_directory_entries_lose_their_meaningless_size():
    """GitHub reports ``size: 0`` for every directory.

    Observed live: a model read ``{"name":"test","size":0,"type":"dir"}`` and
    reported the test directory as *empty* — a claim the listing never made,
    resting on a field we asked for and GitHub cannot fill.
    """
    listing = (
        '[{"name":".gitignore","path":".gitignore","size":3298,"type":"file"},'
        '{"name":"test","path":"test","size":0,"type":"dir"}]'
    )
    cleaned = json.loads(_drop_directory_sizes(listing))
    assert cleaned[0]["size"] == 3298, "a file's size helps the model choose"
    assert "size" not in cleaned[1]
    assert cleaned[1]["name"] == "test", "everything else survives"


def test_anything_that_is_not_a_listing_is_left_alone():
    for text in ("successfully downloaded text file (SHA: abc)", "", "not json {"):
        assert _drop_directory_sizes(text) == text
