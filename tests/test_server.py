from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from pestrail.client import WRITE_METHODS, TestRailClient
from pestrail.server import create_server

from .conftest import API, FAKE_KEY, _no_sleep, make_config

STATUSES = [
    {"id": 1, "name": "passed", "label": "Passed", "is_untested": False},
    {"id": 2, "name": "blocked", "label": "Blocked", "is_untested": False},
    {"id": 3, "name": "untested", "label": "Untested", "is_untested": True},
    {"id": 4, "name": "retest", "label": "Retest", "is_untested": False},
    {"id": 5, "name": "failed", "label": "Failed", "is_untested": False},
]

WRITE_TOOLS = {
    "add_result",
    "add_result_for_case",
    "add_results_for_cases",
    "add_attachment_to_result",
    "add_attachment_to_run",
    "add_attachment_to_case",
    "add_attachment_to_plan",
    "add_attachment_to_plan_entry",
    "add_result_with_attachments",
}


def build(
    read_only: bool = False,
    roots: tuple[Path, ...] = (),
    *,
    allowed_project_ids: frozenset[int] = frozenset(),
    allowed_run_ids: frozenset[int] = frozenset(),
):
    config = make_config(
        read_only=read_only,
        roots=roots,
        allowed_project_ids=allowed_project_ids,
        allowed_run_ids=allowed_run_ids,
    )
    return create_server(config, TestRailClient(config, sleep=_no_sleep))


def tool_names(server) -> set[str]:
    return {t.name for t in asyncio.run(server.list_tools())}


def call(server, name: str, args: dict[str, Any]) -> Any:
    result = asyncio.run(server.call_tool(name, args))
    assert not result.is_error, result
    return json.loads(result.content[0].text)


def body_of(route) -> Any:
    return json.loads(route.calls.last.request.content)


@pytest.fixture
def statuses(mock_api):
    return mock_api.get(API + "get_statuses").mock(return_value=httpx.Response(200, json=STATUSES))


# ----------------------------------------------------------------- registration


def test_all_tools_registered_in_write_mode(mock_api):
    names = tool_names(build())
    assert names >= WRITE_TOOLS
    assert len(names) == 38


def test_read_only_mode_registers_no_write_tools(mock_api):
    names = tool_names(build(read_only=True))
    assert not (names & WRITE_TOOLS)
    assert all(n.startswith("get_") for n in names)
    assert len(names) == 29


def test_no_destructive_tool_names(mock_api):
    for name in tool_names(build()):
        assert not name.startswith(("delete", "close", "update", "move", "copy")), name


def test_write_tools_only_use_write_methods():
    for name in WRITE_TOOLS - {"add_result_with_attachments"}:
        assert name in WRITE_METHODS


def test_tool_annotations(mock_api):
    for tool in asyncio.run(build().list_tools()):
        assert tool.annotations is not None
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.read_only_hint is (not tool.name.startswith("add_"))
        assert tool.description and len(tool.description) > 20


def test_tool_output_is_compact_json(mock_api):
    mock_api.get(url__startswith=API + "get_projects").mock(
        return_value=httpx.Response(
            200, json=[{"id": 1, "name": "Ödeme Sistemi", "suite_mode": 1, "url": "u"}]
        )
    )
    result = asyncio.run(build().call_tool("get_projects", {}))
    text = result.content[0].text
    assert "\n" not in text
    assert ": " not in text and ", " not in text
    assert "Ödeme Sistemi" in text  # non-ASCII kept readable, not \u-escaped
    assert json.loads(text)["projects"][0]["id"] == 1


def test_lifespan_closes_http_client(mock_api):
    config = make_config()
    client = TestRailClient(config, sleep=_no_sleep)
    server = create_server(config, client)

    async def session() -> None:
        async with server.settings.lifespan(server):
            assert not client._http.is_closed

    asyncio.run(session())
    assert client._http.is_closed


def test_version_has_single_source():
    from importlib.metadata import version

    import pestrail

    assert version("pestrail") == pestrail.__version__


# ----------------------------------------------------------------- read tools


def test_get_cases_filters_and_prefixed_ids(mock_api):
    route = mock_api.get(url__startswith=API + "get_cases/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "offset": 0,
                "limit": 2,
                "size": 2,
                "_links": {"next": "/next"},
                "cases": [
                    {"id": 10, "title": "Login", "custom_steps": "big"},
                    {"id": 11, "title": "Logout"},
                ],
            },
        )
    )
    out = call(
        build(),
        "get_cases",
        {"project_id": "P1", "suite_id": "S3", "priority_id": [1, 2], "filter": "log", "limit": 2},
    )
    url = str(route.calls.last.request.url)
    assert url == API + "get_cases/1&suite_id=3&filter=log&priority_id=1,2&limit=2"
    assert out["cases"] == [{"id": 10, "title": "Login"}, {"id": 11, "title": "Logout"}]
    assert out["paging"]["has_more"] is True
    assert out["paging"]["next_offset"] == 2


def test_legacy_bare_list_is_paged_client_side(mock_api):
    # Pre-6.7 TestRail ignores limit/offset and returns the full bare list.
    mock_api.get(url__startswith=API + "get_projects").mock(
        return_value=httpx.Response(200, json=[{"id": i, "name": f"p{i}"} for i in range(5)])
    )
    out = call(build(), "get_projects", {"limit": 2, "offset": 2})
    assert [p["id"] for p in out["projects"]] == [2, 3]
    assert out["paging"] == {
        "offset": 2,
        "returned": 2,
        "has_more": True,
        "next_offset": 4,
        "hint": "Call again with offset=next_offset, or use fetch_all=true.",
    }


def test_get_tests_status_filter_by_name(mock_api, statuses):
    route = mock_api.get(url__startswith=API + "get_tests/56").mock(
        return_value=httpx.Response(
            200,
            json={
                "offset": 0,
                "limit": 250,
                "size": 1,
                "_links": {"next": None},
                "tests": [{"id": 678, "case_id": 1234, "status_id": 5, "title": "t"}],
            },
        )
    )
    out = call(build(), "get_tests", {"run_id": "R56", "status": ["failed", "Untested"]})
    assert str(route.calls.last.request.url) == API + "get_tests/56&status_id=5,3"
    assert out["tests"][0]["status"] == "Failed"


def test_wrong_prefix_is_a_clear_error(mock_api):
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(ToolError, match="does not belong to a case"):
        asyncio.run(build().call_tool("get_case", {"case_id": "R5"}))
    assert route.call_count == 0


def test_testrail_error_surfaces_as_tool_error(mock_api):
    mock_api.get(API + "get_run/9").mock(
        return_value=httpx.Response(400, json={"error": "Field :run_id is not a valid test run."})
    )
    with pytest.raises(ToolError, match="not a valid test run"):
        asyncio.run(build().call_tool("get_run", {"run_id": 9}))


def test_get_plan_shapes_entries(mock_api):
    mock_api.get(API + "get_plan/9").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 9,
                "name": "Release",
                "entries": [
                    {
                        "id": "3933d74b-4282-4c1f-be62-a641ab427063",
                        "name": "Smoke",
                        "suite_id": 1,
                        "runs": [
                            {
                                "id": 77,
                                "name": "Smoke",
                                "config": "Chrome",
                                "entry_id": "x",
                                "untested_count": 3,
                                "description": "drop me",
                            }
                        ],
                    }
                ],
            },
        )
    )
    out = call(build(), "get_plan", {"plan_id": "P9"})
    run = out["entries"][0]["runs"][0]
    assert run["id"] == 77 and run["config"] == "Chrome"
    assert "description" not in run


def test_dates_accept_iso(mock_api):
    route = mock_api.get(url__startswith=API + "get_runs/1").mock(
        return_value=httpx.Response(200, json=[])
    )
    call(build(), "get_runs", {"project_id": 1, "created_after": "2024-01-01"})
    assert "created_after=1704067200" in str(route.calls.last.request.url)


def test_list_filters_accept_prefixed_ids(mock_api):
    runs = mock_api.get(url__startswith=API + "get_runs/1").mock(
        return_value=httpx.Response(200, json=[])
    )
    call(build(), "get_runs", {"project_id": "P1", "suite_id": ["S3", 4], "milestone_id": "M2"})
    assert str(runs.calls.last.request.url) == API + "get_runs/1&milestone_id=2&suite_id=3,4"

    cases = mock_api.get(url__startswith=API + "get_cases/1").mock(
        return_value=httpx.Response(200, json=[])
    )
    call(build(), "get_cases", {"project_id": 1, "section_id": "7", "milestone_id": ["M5"]})
    assert str(cases.calls.last.request.url) == API + "get_cases/1&section_id=7&milestone_id=5"


def test_list_filter_wrong_prefix_rejected(mock_api):
    route = mock_api.route().mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(ToolError, match="does not belong to a suite"):
        asyncio.run(build().call_tool("get_runs", {"project_id": 1, "suite_id": "R3"}))
    assert route.call_count == 0


def test_get_users_follows_pagination(mock_api):
    page1 = {
        "offset": 0,
        "limit": 1,
        "size": 1,
        "_links": {"next": "/api/v2/get_users/3&offset=1"},
        "users": [{"id": 1, "name": "A", "email": "a@example.invalid", "role": "x"}],
    }
    page2 = {
        "offset": 1,
        "limit": 1,
        "size": 1,
        "_links": {"next": None},
        "users": [{"id": 2, "name": "B", "email": "b@example.invalid"}],
    }
    route = mock_api.get(url__startswith=API + "get_users/3").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )
    out = call(build(), "get_users", {"project_id": "P3"})
    assert [u["id"] for u in out["users"]] == [1, 2]
    assert out["count"] == 2
    assert str(route.calls[1].request.url).endswith("get_users/3&offset=1")


def test_get_users_legacy_list_without_project(mock_api):
    mock_api.get(API + "get_users").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "A", "email": "a@x.invalid"}])
    )
    out = call(build(), "get_users", {})
    assert out["users"] == [{"id": 1, "name": "A", "email": "a@x.invalid"}]


def test_results_comments_truncated_and_custom_kept(mock_api, statuses):
    mock_api.get(url__startswith=API + "get_results_for_run/5").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "test_id": 2,
                    "status_id": 1,
                    "comment": "x" * 5000,
                    "custom_env": "staging",
                    "custom_empty": None,
                },
            ],
        )
    )
    out = call(build(), "get_results_for_run", {"run_id": 5})
    result = out["results"][0]
    assert result["status"] == "Passed"
    assert result["custom_env"] == "staging"
    assert "custom_empty" not in result
    assert len(result["comment"]) < 1100


# ----------------------------------------------------------------- write tools


def test_add_result_for_case_by_status_name(mock_api, statuses):
    route = mock_api.post(API + "add_result_for_case/56/1234").mock(
        return_value=httpx.Response(200, json={"id": 999, "test_id": 678, "status_id": 5})
    )
    out = call(
        build(),
        "add_result_for_case",
        {
            "run_id": "R56",
            "case_id": "C1234",
            "status": "Failed",
            "comment": "Boom",
            "elapsed": "1m 30s",
            "defects": ["BUG-1", "BUG-2"],
            "version": "2.0",
            "custom_fields": {"custom_env": "staging"},
        },
    )
    assert body_of(route) == {
        "status_id": 5,
        "comment": "Boom",
        "elapsed": "1m 30s",
        "defects": "BUG-1,BUG-2",
        "version": "2.0",
        "custom_env": "staging",
    }
    assert out["id"] == 999 and out["status"] == "Failed" and out["case_id"] == 1234


def test_untested_is_rejected_without_post(mock_api, statuses):
    route = mock_api.post(url__startswith=API + "add_result").mock(
        return_value=httpx.Response(200, json={})
    )
    with pytest.raises(ToolError, match="Untested"):
        asyncio.run(build().call_tool("add_result", {"test_id": "T1", "status": "untested"}))
    with pytest.raises(ToolError, match="Untested"):
        asyncio.run(build().call_tool("add_result", {"test_id": 1, "status": 3}))
    assert route.call_count == 0


def test_empty_result_rejected(mock_api):
    with pytest.raises(ToolError, match="Nothing to add"):
        asyncio.run(build().call_tool("add_result", {"test_id": 1}))


def test_bad_custom_field_key_rejected(mock_api, statuses):
    with pytest.raises(ToolError, match="custom_"):
        asyncio.run(
            build().call_tool(
                "add_result", {"test_id": 1, "status": 1, "custom_fields": {"status_id": 3}}
            )
        )


def test_bulk_results(mock_api, statuses):
    route = mock_api.post(API + "add_results_for_cases/56").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "test_id": 11, "status_id": 1},
                {"id": 2, "test_id": 12, "status_id": 5},
            ],
        )
    )
    out = call(
        build(),
        "add_results_for_cases",
        {
            "run_id": 56,
            "results": [
                {"case_id": "C1", "status": "passed"},
                {"case_id": 2, "status": 5, "comment": "fail", "defects": "BUG-9"},
            ],
        },
    )
    assert body_of(route) == {
        "results": [
            {"case_id": 1, "status_id": 1},
            {"case_id": 2, "status_id": 5, "comment": "fail", "defects": "BUG-9"},
        ]
    }
    assert out["added"] == 2
    assert [r["case_id"] for r in out["results"]] == [1, 2]


def test_bulk_results_validated_before_sending(mock_api, statuses):
    route = mock_api.post(API + "add_results_for_cases/56").mock(
        return_value=httpx.Response(200, json=[])
    )
    with pytest.raises(ToolError) as info:
        asyncio.run(
            build().call_tool(
                "add_results_for_cases",
                {
                    "run_id": 56,
                    "results": [
                        {"case_id": 1, "status": "passed"},
                        {"case_id": "R2", "status": "passed"},
                        {"case_id": 3, "status": "nope"},
                    ],
                },
            )
        )
    message = str(info.value)
    assert "Nothing was sent" in message
    assert "results[1]" in message and "results[2]" in message
    assert route.call_count == 0


def test_bulk_results_rejects_length_mismatch(mock_api, statuses):
    mock_api.post(API + "add_results_for_cases/56").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "test_id": 11, "status_id": 1}])
    )
    with pytest.raises(ToolError, match="returned 1 results but 2 were sent"):
        asyncio.run(
            build().call_tool(
                "add_results_for_cases",
                {
                    "run_id": 56,
                    "results": [
                        {"case_id": 1, "status": "passed"},
                        {"case_id": 2, "status": "failed"},
                    ],
                },
            )
        )


# ----------------------------------------------------------------- attachments


def test_attachment_upload_and_root_restriction(mock_api, tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    good = root / "shot.png"
    good.write_bytes(b"\x89PNG")
    bad = tmp_path / "secret.txt"
    bad.write_text("no", encoding="utf-8")
    route = mock_api.post(API + "add_attachment_to_run/5").mock(
        return_value=httpx.Response(200, json={"attachment_id": 42})
    )
    server = build(roots=(root.resolve(),))
    out = call(server, "add_attachment_to_run", {"run_id": "R5", "file_path": str(good)})
    assert out == {"attachment_id": 42, "file": "shot.png", "size_bytes": 4}
    with pytest.raises(ToolError, match="outside the allowed"):
        asyncio.run(server.call_tool("add_attachment_to_run", {"run_id": 5, "file_path": str(bad)}))
    assert route.call_count == 1


def test_attachment_to_plan_entry_guid(mock_api, tmp_path):
    f = tmp_path / "r.html"
    f.write_text("<html/>", encoding="utf-8")
    guid = "3933d74b-4282-4c1f-be62-a641ab427063"
    route = mock_api.post(API + f"add_attachment_to_plan_entry/9/{guid}").mock(
        return_value=httpx.Response(200, json={"attachment_id": 7})
    )
    call(
        build(),
        "add_attachment_to_plan_entry",
        {"plan_id": "P9", "entry_id": guid, "file_path": str(f)},
    )
    assert route.call_count == 1
    with pytest.raises(ToolError, match="GUID"):
        asyncio.run(
            build().call_tool(
                "add_attachment_to_plan_entry",
                {"plan_id": 9, "entry_id": "../x", "file_path": str(f)},
            )
        )


def test_result_with_attachments_complete(mock_api, statuses, tmp_path):
    a, b = tmp_path / "a.log", tmp_path / "b.png"
    a.write_text("log", encoding="utf-8")
    b.write_bytes(b"png")
    mock_api.post(API + "add_result_for_case/56/1234").mock(
        return_value=httpx.Response(200, json={"id": 500, "test_id": 9, "status_id": 5})
    )
    upload = mock_api.post(API + "add_attachment_to_result/500").mock(
        side_effect=[
            httpx.Response(200, json={"attachment_id": 1}),
            httpx.Response(200, json={"attachment_id": 2}),
        ]
    )
    out = call(
        build(),
        "add_result_with_attachments",
        {
            "run_id": "R56",
            "case_id": "C1234",
            "status": "Failed",
            "file_paths": [str(a), str(b)],
        },
    )
    assert out["result_id"] == 500
    assert out["outcome"] == "complete"
    assert [x["attachment_id"] for x in out["attachments"]] == [1, 2]
    assert out["failed_attachments"] == []
    assert upload.call_count == 2


def test_result_with_attachments_partial_failure(mock_api, statuses, tmp_path):
    a, b = tmp_path / "a.log", tmp_path / "b.log"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    mock_api.post(API + "add_result_for_case/56/1234").mock(
        return_value=httpx.Response(200, json={"id": 500, "test_id": 9, "status_id": 1})
    )
    mock_api.post(API + "add_attachment_to_result/500").mock(
        side_effect=[
            httpx.Response(200, json={"attachment_id": 1}),
            httpx.Response(403, json={"error": "No permission"}),
        ]
    )
    out = call(
        build(),
        "add_result_with_attachments",
        {
            "run_id": 56,
            "case_id": 1234,
            "status": 1,
            "file_paths": [str(a), str(b)],
        },
    )
    assert out["outcome"] == "partial"
    assert out["result_id"] == 500
    assert len(out["attachments"]) == 1
    assert out["failed_attachments"][0]["file"] == str(b)
    assert "No permission" in out["failed_attachments"][0]["error"]


def test_result_with_attachments_bad_file_creates_nothing(mock_api, statuses, tmp_path):
    route = mock_api.post(url__startswith=API + "add_").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    with pytest.raises(ToolError, match="No result was added"):
        asyncio.run(
            build().call_tool(
                "add_result_with_attachments",
                {
                    "run_id": 56,
                    "case_id": 1,
                    "status": 1,
                    "file_paths": [str(tmp_path / "missing")],
                },
            )
        )
    assert route.call_count == 0


def test_result_with_attachments_missing_result_id(mock_api, statuses, tmp_path):
    f = tmp_path / "a.log"
    f.write_text("a", encoding="utf-8")
    mock_api.post(API + "add_result_for_case/56/1").mock(
        return_value=httpx.Response(200, json={"test_id": 9, "status_id": 1})
    )
    upload = mock_api.post(url__startswith=API + "add_attachment").mock(
        return_value=httpx.Response(200, json={"attachment_id": 1})
    )
    with pytest.raises(ToolError, match="no usable result id"):
        asyncio.run(
            build().call_tool(
                "add_result_with_attachments",
                {
                    "run_id": 56,
                    "case_id": 1,
                    "status": 1,
                    "file_paths": [str(f)],
                },
            )
        )
    assert upload.call_count == 0


# ----------------------------------------------------------------- secrecy


def test_api_key_never_in_tool_output_or_errors(mock_api, statuses):
    mock_api.get(API + "get_projects").mock(return_value=httpx.Response(200, json=[]))
    mock_api.get(API + "get_case/1").mock(return_value=httpx.Response(401, json={"error": "x"}))
    server = build()
    text = asyncio.run(server.call_tool("get_projects", {})).content[0].text
    assert FAKE_KEY not in text
    with pytest.raises(ToolError) as info:
        asyncio.run(server.call_tool("get_case", {"case_id": 1}))
    assert FAKE_KEY not in str(info.value)
