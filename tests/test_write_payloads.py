"""Write payload size limits."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from pestrail.client import TestRailClient, TestRailError
from pestrail.server import create_server
from pestrail.write_tools import (
    MAX_COMMENT_CHARS,
    MAX_CUSTOM_FIELDS,
    MAX_CUSTOM_VALUE_CHARS,
    MAX_DEFECTS_CHARS,
    MAX_VERSION_CHARS,
    build_result_body,
)

from .conftest import API, _no_sleep, make_config

STATUSES = [{"id": 1, "name": "passed", "label": "Passed", "is_untested": False}]


def _body(**overrides):
    base = {
        "status": 1,
        "comment": None,
        "elapsed": None,
        "defects": None,
        "version": None,
        "assigned_to_id": None,
        "custom_fields": None,
        "statuses": STATUSES,
    }
    base.update(overrides)
    return build_result_body(**base)


def test_build_result_body_accepts_limits():
    out = _body(
        comment="x" * MAX_COMMENT_CHARS,
        version="v" * MAX_VERSION_CHARS,
        defects="D" * MAX_DEFECTS_CHARS,
        custom_fields={f"custom_f{i}": "ok" for i in range(MAX_CUSTOM_FIELDS)},
    )
    assert len(out["comment"]) == MAX_COMMENT_CHARS
    assert out["custom_f0"] == "ok"


def test_comment_too_long():
    with pytest.raises(TestRailError, match="comment exceeds"):
        _body(comment="x" * (MAX_COMMENT_CHARS + 1))


def test_version_too_long():
    with pytest.raises(TestRailError, match="version exceeds"):
        _body(version="v" * (MAX_VERSION_CHARS + 1))


def test_defects_too_long():
    with pytest.raises(TestRailError, match="defects exceeds"):
        _body(defects="D" * (MAX_DEFECTS_CHARS + 1))


def test_too_many_custom_fields():
    fields = {f"custom_f{i}": "x" for i in range(MAX_CUSTOM_FIELDS + 1)}
    with pytest.raises(TestRailError, match=f"At most {MAX_CUSTOM_FIELDS}"):
        _body(custom_fields=fields)


def test_custom_field_value_too_long():
    with pytest.raises(TestRailError, match="custom_big"):
        _body(custom_fields={"custom_big": "x" * (MAX_CUSTOM_VALUE_CHARS + 1)})


def test_custom_field_structured_value_too_long():
    with pytest.raises(TestRailError, match="custom_list"):
        _body(custom_fields={"custom_list": ["x" * (MAX_CUSTOM_VALUE_CHARS + 1)]})


def test_tool_rejects_oversized_comment(mock_api):
    mock_api.get(API + "get_statuses").mock(return_value=httpx.Response(200, json=STATUSES))
    route = mock_api.post(url__startswith=API + "add_result").mock(
        return_value=httpx.Response(200, json={})
    )
    config = make_config()
    server = create_server(config, TestRailClient(config, sleep=_no_sleep))
    with pytest.raises((ToolError, Exception)):
        asyncio.run(
            server.call_tool(
                "add_result",
                {"test_id": 1, "status": 1, "comment": "x" * (MAX_COMMENT_CHARS + 1)},
            )
        )
    assert route.call_count == 0
