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


# Response shapes below mirror a live TestRail instance: get_case has suite_id but no
# project_id, and there is no get_result endpoint at all.


def test_enforce_result_known_from_this_session_needs_no_lookup(mock_api):
    route = mock_api.route().mock(return_value=httpx.Response(500))
    cfg = make_config(allowed_run_ids=frozenset({56}))
    client = TestRailClient(cfg, sleep=_no_sleep)
    resolved = run(enforce_write_scope(client, cfg, result_id=500, known_result_runs={500: 56}))
    assert resolved == 56
    assert route.call_count == 0


def test_enforce_result_unknown_without_test_id_is_refused(mock_api):
    route = mock_api.route().mock(return_value=httpx.Response(500))
    cfg = make_config(allowed_run_ids=frozenset({56}))
    client = TestRailClient(cfg, sleep=_no_sleep)
    with pytest.raises(TestRailError, match="no API to look up a single result"):
        run(enforce_write_scope(client, cfg, result_id=500))
    assert route.call_count == 0


def test_enforce_result_with_test_id_is_verified(mock_api):
    mock_api.get(url__startswith=API + "get_results/9").mock(
        return_value=httpx.Response(
            200,
            json={
                "offset": 0,
                "limit": 250,
                "size": 2,
                "_links": {"next": None},
                "results": [{"id": 499, "test_id": 9}, {"id": 500, "test_id": 9}],
            },
        )
    )
    mock_api.get(API + "get_test/9").mock(
        return_value=httpx.Response(200, json={"id": 9, "run_id": 56})
    )
    cfg = make_config(allowed_run_ids=frozenset({56}))
    client = TestRailClient(cfg, sleep=_no_sleep)
    assert run(enforce_write_scope(client, cfg, result_id=500, test_id=9)) == 56


def test_enforce_result_not_in_given_test_is_refused(mock_api):
    mock_api.get(url__startswith=API + "get_results/9").mock(
        return_value=httpx.Response(200, json=[{"id": 499, "test_id": 9}])
    )
    get_test = mock_api.get(API + "get_test/9").mock(
        return_value=httpx.Response(200, json={"id": 9, "run_id": 56})
    )
    cfg = make_config(allowed_run_ids=frozenset({56}))
    client = TestRailClient(cfg, sleep=_no_sleep)
    with pytest.raises(TestRailError, match="does not belong to test 9"):
        run(enforce_write_scope(client, cfg, result_id=500, test_id=9))
    assert get_test.call_count == 0


@pytest.mark.parametrize(("suite_project", "allowed"), [(1, True), (3, False)])
def test_enforce_case_project_via_suite(mock_api, suite_project, allowed):
    mock_api.get(API + "get_case/7196").mock(
        return_value=httpx.Response(
            200, json={"id": 7196, "suite_id": 72, "section_id": 5, "title": "t"}
        )
    )
    mock_api.get(API + "get_suite/72").mock(
        return_value=httpx.Response(200, json={"id": 72, "project_id": suite_project})
    )
    cfg = make_config(allowed_project_ids=frozenset({1}))
    client = TestRailClient(cfg, sleep=_no_sleep)
    if allowed:
        run(enforce_write_scope(client, cfg, case_id=7196))
    else:
        with pytest.raises(TestRailError, match="Project 3 is outside"):
            run(enforce_write_scope(client, cfg, case_id=7196))


def test_tool_attach_to_result_created_in_session(mock_api, statuses, tmp_path):
    f = tmp_path / "log.txt"
    f.write_text("log", encoding="utf-8")
    mock_api.post(API + "add_result_for_case/56/1234").mock(
        return_value=httpx.Response(200, json={"id": 999, "test_id": 678, "status_id": 5})
    )
    upload = mock_api.post(API + "add_attachment_to_result/999").mock(
        return_value=httpx.Response(200, json={"attachment_id": 1})
    )
    config = make_config(allowed_run_ids=frozenset({56}))
    server = create_server(config, TestRailClient(config, sleep=_no_sleep))
    asyncio.run(
        server.call_tool("add_result_for_case", {"run_id": 56, "case_id": 1234, "status": 5})
    )
    result = asyncio.run(
        server.call_tool("add_attachment_to_result", {"result_id": 999, "file_path": str(f)})
    )
    assert not result.is_error
    assert upload.call_count == 1


def test_tool_attach_to_unknown_result_needs_test_id(mock_api, tmp_path):
    f = tmp_path / "log.txt"
    f.write_text("log", encoding="utf-8")
    upload = mock_api.post(url__startswith=API + "add_attachment_to_result").mock(
        return_value=httpx.Response(200, json={"attachment_id": 1})
    )
    config = make_config(allowed_run_ids=frozenset({56}))
    server = create_server(config, TestRailClient(config, sleep=_no_sleep))
    with pytest.raises(ToolError, match="Pass the result's test_id"):
        asyncio.run(
            server.call_tool("add_attachment_to_result", {"result_id": 5, "file_path": str(f)})
        )
    assert upload.call_count == 0


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
