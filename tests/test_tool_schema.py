"""Advertised tool schemas: smaller than pydantic's output, but meaning the same thing.

Every tool definition is sent to the model in every conversation, so its size is a
per-conversation token cost. These tests keep it small and prove the slimming is lossless.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator
from mcp.server.mcpserver.exceptions import ToolError

from pestrail.client import TestRailClient
from pestrail.schema import slim_schema
from pestrail.server import create_server

from .conftest import API, make_config

#: Ceilings for the full tools/list payload in compact JSON. Measured 2026-11-19: 34,958 /
#: 24,455 (incl. text_format on get_case/get_test); before slimming 48,238 / 32,520.
#: Raise deliberately, not by habit: every character here is paid in every conversation.
MAX_WRITE_MODE_CHARS = 35_500
MAX_READ_ONLY_CHARS = 24_800

#: Values tried against every parameter; both schemas must accept/reject each the same way.
SAMPLES: list[Any] = [
    None, True, False, 0, 1, -1, 250, 251, 5000, 5001, 1.5, "", "x", "R56", "a" * 300,
    "b" * 25_000, [], [1], ["Failed", 5], [None], [{"case_id": 1}], {}, {"custom_env": "x"},
]  # fmt: skip


def _server(read_only: bool):
    config = make_config(read_only=read_only)
    return create_server(config, TestRailClient(config))


def _payload_chars(read_only: bool) -> int:
    tools = asyncio.run(_server(read_only).list_tools())
    return sum(
        len(json.dumps(t.model_dump(exclude_none=True), separators=(",", ":"))) for t in tools
    )


def _registered_tools():
    return _server(read_only=False)._tool_manager.list_tools()


# ----------------------------------------------------------------- size guard


def test_write_mode_tool_definitions_stay_small():
    assert _payload_chars(read_only=False) <= MAX_WRITE_MODE_CHARS


def test_read_only_tool_definitions_stay_small():
    assert _payload_chars(read_only=True) <= MAX_READ_ONLY_CHARS


def test_advertised_schemas_have_no_clutter():
    for tool in asyncio.run(_server(read_only=False).list_tools()):
        text = json.dumps(tool.input_schema)
        assert '"title"' not in text, tool.name
        for name, prop in tool.input_schema.get("properties", {}).items():
            assert not ("default" in prop and prop["default"] is None), (tool.name, name)


# ----------------------------------------------------------------- lossless


@pytest.mark.parametrize("tool", _registered_tools(), ids=lambda t: t.name)
def test_slim_schema_accepts_exactly_the_same_values(tool):
    original = tool.fn_metadata.arg_model.model_json_schema(by_alias=True)
    slim = tool.parameters
    assert slim == slim_schema(original)  # what is advertised is the slimmed original
    assert set(slim["properties"]) == set(original["properties"])
    assert slim.get("required", []) == original.get("required", [])
    for name, prop in original["properties"].items():
        if "default" in prop and prop["default"] is not None:
            assert slim["properties"][name]["default"] == prop["default"], (tool.name, name)

    # Each parameter is checked against its own schema: checking whole argument objects
    # would hide differences behind a missing required parameter.
    for name, prop in original["properties"].items():
        full = Draft202012Validator({**prop, "$defs": original.get("$defs", {})})
        small = Draft202012Validator({**slim["properties"][name], "$defs": slim.get("$defs", {})})
        for value in SAMPLES:
            assert full.is_valid(value) == small.is_valid(value), (tool.name, name, value)


# ----------------------------------------------------------------- slim_schema rules


def test_titles_dropped_but_a_property_named_title_is_kept():
    schema = {
        "title": "Args",
        "type": "object",
        "properties": {"title": {"title": "Title", "type": "string"}},
    }
    assert slim_schema(schema) == {"type": "object", "properties": {"title": {"type": "string"}}}


def test_only_null_defaults_are_dropped():
    schema = {"properties": {"a": {"default": None}, "b": {"default": 0}, "c": {"default": False}}}
    assert slim_schema(schema)["properties"] == {
        "a": {},
        "b": {"default": 0},
        "c": {"default": False},
    }


def test_simple_any_of_is_merged_with_type_specific_keywords():
    schema = {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 250}, {"type": "null"}]}
    assert slim_schema(schema) == {"minimum": 1, "maximum": 250, "type": ["integer", "null"]}


@pytest.mark.parametrize(
    "branches",
    [
        # enum would reject null after merging: Literal[...] | None must stay as anyOf.
        [{"type": "string", "enum": ["a", "b"]}, {"type": "null"}],
        # numeric keywords would apply to both.
        [{"type": "integer", "minimum": 1}, {"type": "number"}],
        [{"$ref": "#/$defs/X"}, {"type": "null"}],
        [{"type": "string"}, {"type": "string", "maxLength": 3}],
        # minLength applies to strings only; on an integer branch it would be a no-op.
        [{"type": "integer", "minLength": 1}, {"type": "null"}],
    ],
)
def test_any_of_that_would_change_meaning_is_kept(branches):
    assert slim_schema({"anyOf": branches}) == {"anyOf": branches}


# ----------------------------------------------------------------- unknown arguments


def test_schemas_declare_no_extra_arguments():
    for tool in asyncio.run(_server(read_only=False).list_tools()):
        assert tool.input_schema.get("additionalProperties") is False, tool.name


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        # Seen in Claude Desktop: TestRail's API name instead of the tool's parameter name.
        # It used to be dropped silently, returning all 131 tests instead of the failed ones.
        ("get_tests", {"run_id": 650, "status_id": [5, 4]}),
        # Worse for writes: the result would have been added without a status.
        ("add_result", {"test_id": 678, "status_id": 5, "comment": "ok"}),
    ],
)
def test_unknown_argument_is_rejected_before_any_request(mock_api, tool, args):
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(ToolError, match="status_id") as exc:
        asyncio.run(_server(read_only=False).call_tool(tool, args))
    message = str(exc.value)
    # The error says what to use instead, so the model retries with the filter, not without.
    assert "Unknown parameter 'status_id' (did you mean 'status'?)" in message
    assert f"for {tool};" in message and "Valid parameters:" in message
    assert route.call_count == 0


def test_unknown_argument_without_a_close_match_lists_valid_ones(mock_api):
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(ToolError) as exc:
        asyncio.run(_server(read_only=True).call_tool("get_run", {"run_id": 1, "foo": 2}))
    message = str(exc.value)
    assert "Unknown parameter 'foo' for get_run" in message and "did you mean" not in message
    assert "Valid parameters: run_id." in message
    assert route.call_count == 0


@pytest.mark.parametrize(
    "status",
    [
        ["Failed", "Retest"],
        # Seen in Claude Desktop: the list arrives as JSON text. The SDK parses it back.
        '["Failed", "Retest"]',
        "Failed, Retest",
    ],
)
def test_status_list_in_any_form_filters_by_both(mock_api, status):
    statuses = [
        {"id": 4, "name": "retest", "label": "Retest", "is_untested": False},
        {"id": 5, "name": "failed", "label": "Failed", "is_untested": False},
    ]
    mock_api.get(API + "get_statuses").mock(return_value=httpx.Response(200, json=statuses))
    tests = mock_api.get(url__startswith=API + "get_tests/650").mock(
        return_value=httpx.Response(200, json=[])
    )
    asyncio.run(_server(read_only=True).call_tool("get_tests", {"run_id": "650", "status": status}))
    assert str(tests.calls.last.request.url).endswith("get_tests/650&status_id=5,4")
