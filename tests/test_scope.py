"""Write-scope allowlist enforcement."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from pestrail.client import TestRailClient, TestRailError
from pestrail.scope import enforce_write_scope
from pestrail.server import create_server

from .conftest import API, _no_sleep, make_config


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def statuses(mock_api):
    return mock_api.get(API + "get_statuses").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "name": "passed", "label": "Passed", "is_untested": False},
                {"id": 5, "name": "failed", "label": "Failed", "is_untested": False},
            ],
        )
    )


def test_enforce_noop_without_allowlists():
    cfg = make_config()
    client = TestRailClient(cfg, sleep=_no_sleep)
    run(enforce_write_scope(client, cfg, run_id=99))


def test_enforce_run_allowlist_rejects_other_run():
    cfg = make_config(allowed_run_ids=frozenset({56}))
    client = TestRailClient(cfg, sleep=_no_sleep)
    with pytest.raises(TestRailError, match="outside TESTRAIL_ALLOWED_RUN_IDS"):
        run(enforce_write_scope(client, cfg, run_id=99))


def test_enforce_run_only_blocks_case_without_run():
    cfg = make_config(allowed_run_ids=frozenset({56}))
    client = TestRailClient(cfg, sleep=_no_sleep)
    with pytest.raises(TestRailError, match="not tied to a test run"):
        run(enforce_write_scope(client, cfg, case_id=1))


def test_enforce_project_allowlist_via_run(mock_api):
    mock_api.get(API + "get_run/56").mock(
        return_value=httpx.Response(200, json={"id": 56, "project_id": 3})
    )
    cfg = make_config(allowed_project_ids=frozenset({1}))
    client = TestRailClient(cfg, sleep=_no_sleep)
    with pytest.raises(TestRailError, match="outside TESTRAIL_ALLOWED_PROJECT_IDS"):
        run(enforce_write_scope(client, cfg, run_id=56))


def test_enforce_project_allowlist_accepts(mock_api):
    mock_api.get(API + "get_run/56").mock(
        return_value=httpx.Response(200, json={"id": 56, "project_id": 1})
    )
    cfg = make_config(allowed_project_ids=frozenset({1}))
    client = TestRailClient(cfg, sleep=_no_sleep)
    run(enforce_write_scope(client, cfg, run_id=56))


def test_enforce_result_resolves_test_and_run(mock_api):
    mock_api.get(API + "get_result/500").mock(
        return_value=httpx.Response(200, json={"id": 500, "test_id": 9})
    )
    mock_api.get(API + "get_test/9").mock(
        return_value=httpx.Response(200, json={"id": 9, "run_id": 56})
    )
    cfg = make_config(allowed_run_ids=frozenset({56}))
    client = TestRailClient(cfg, sleep=_no_sleep)
    run(enforce_write_scope(client, cfg, result_id=500))


def test_tool_blocks_write_outside_allowed_run(mock_api, statuses):
    route = mock_api.post(API + "add_result_for_case/99/1").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    config = make_config(allowed_run_ids=frozenset({56}))
    server = create_server(config, TestRailClient(config, sleep=_no_sleep))
    with pytest.raises(ToolError, match="outside TESTRAIL_ALLOWED_RUN_IDS"):
        asyncio.run(
            server.call_tool(
                "add_result_for_case",
                {"run_id": 99, "case_id": 1, "status": 1},
            )
        )
    assert route.call_count == 0


def test_tool_allows_write_inside_allowed_run(mock_api, statuses):
    mock_api.post(API + "add_result_for_case/56/1234").mock(
        return_value=httpx.Response(200, json={"id": 999, "test_id": 678, "status_id": 5})
    )
    config = make_config(allowed_run_ids=frozenset({56}))
    server = create_server(config, TestRailClient(config, sleep=_no_sleep))
    result = asyncio.run(
        server.call_tool(
            "add_result_for_case",
            {"run_id": "R56", "case_id": "C1234", "status": "Failed"},
        )
    )
    assert not result.is_error
    payload: dict[str, Any] = __import__("json").loads(result.content[0].text)
    assert payload["id"] == 999
