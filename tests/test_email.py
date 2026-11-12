"""get_user_by_email input validation."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from pestrail.client import TestRailClient
from pestrail.server import create_server

from .conftest import API, _no_sleep, make_config


@pytest.mark.parametrize("email", ["@", "a@", "@b", "no-at", "a@b", "a @b.c", ""])
def test_invalid_email_rejected(mock_api, email):
    route = mock_api.get(url__startswith=API + "get_user_by_email").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    config = make_config()
    server = create_server(config, TestRailClient(config, sleep=_no_sleep))
    with pytest.raises(ToolError, match="not an email"):
        asyncio.run(server.call_tool("get_user_by_email", {"email": email}))
    assert route.call_count == 0


def test_valid_email_accepted(mock_api):
    mock_api.get(url__startswith=API + "get_user_by_email").mock(
        return_value=httpx.Response(
            200, json={"id": 7, "name": "A", "email": "a@example.invalid"}
        )
    )
    config = make_config()
    server = create_server(config, TestRailClient(config, sleep=_no_sleep))
    result = asyncio.run(server.call_tool("get_user_by_email", {"email": " a@example.invalid "}))
    assert not result.is_error
