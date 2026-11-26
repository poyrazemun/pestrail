"""get_run_summary: counts per status and per assignee, plus the tests needing attention.

Neutral data shaped like a real run (paginated get_tests, TestRail 6.7+).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from pestrail.client import TestRailClient
from pestrail.read_tools import run_test_count
from pestrail.server import create_server

from .conftest import API, _no_sleep, make_config, text_of

STATUSES: list[dict[str, Any]] = [
    {"id": 1, "name": "passed", "label": "Passed", "is_untested": False},
    {"id": 2, "name": "blocked", "label": "Blocked", "is_untested": False},
    {"id": 3, "name": "untested", "label": "Untested", "is_untested": True},
    {"id": 4, "name": "retest", "label": "Retest", "is_untested": False},
    {"id": 5, "name": "failed", "label": "Failed", "is_untested": False},
]
RUN = {
    "id": 650, "name": "Regression", "project_id": 31, "plan_id": None, "milestone_id": None,
    "config": None, "is_completed": False, "url": "https://testrail.invalid/runs/view/650",
    "description": "long text that a summary does not need", "passed_count": 300,
    "failed_count": 4, "blocked_count": 2, "untested_count": 3, "retest_count": 1,
    "custom_status1_count": 0, "created_by": 19,
}  # fmt: skip

# 310 tests: users 19 and 20 plus 3 unassigned; status per index below.
FAILED = {3, 70, 130, 200}
BLOCKED = {12, 250}
RETEST = {99}
UNTESTED = {5, 6, 7}


def run_tests(total: int = 310) -> list[dict[str, Any]]:
    def status(i: int) -> int:
        return (
            5 if i in FAILED else 2 if i in BLOCKED else 4 if i in RETEST
            else 3 if i in UNTESTED else 1
        )  # fmt: skip

    def owner(i: int) -> int | None:
        return None if i >= total - 3 else 19 if i % 2 == 0 else 20

    return [
        {"id": 73800 + i, "case_id": 46300 + i, "run_id": 650, "title": f"case {i}",
         "status_id": status(i), "assignedto_id": owner(i), "priority_id": 2, "type_id": 7,
         "refs": None, "estimate": None, "custom_steps": "x" * 50}
        for i in range(total)
    ]  # fmt: skip


def mock_tests(mock_api, tests: list[dict[str, Any]]):
    def respond(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        query = {k: v[0] for k, v in parse_qs(url.split("&", 1)[1] if "&" in url else "").items()}
        offset, limit = int(query.get("offset", 0)), int(query.get("limit", 250))
        page = tests[offset : offset + limit]
        more = offset + limit < len(tests)
        return httpx.Response(
            200,
            json={"offset": offset, "limit": limit, "size": len(page),
                  "_links": {"next": "/next" if more else None, "prev": None}, "tests": page},
        )  # fmt: skip

    return mock_api.get(url__startswith=API + "get_tests/650").mock(side_effect=respond)


@pytest.fixture
def api(mock_api):
    mock_api.get(API + "get_statuses").mock(return_value=httpx.Response(200, json=STATUSES))
    mock_api.get(API + "get_run/650").mock(return_value=httpx.Response(200, json=RUN))
    mock_tests(mock_api, run_tests())
    return mock_api


def build(**overrides: Any):
    config = dataclasses.replace(make_config(read_only=True), **overrides)
    return create_server(config, TestRailClient(config, sleep=_no_sleep))


def summary(server, **args: Any) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(
        text_of(asyncio.run(server.call_tool("get_run_summary", {"run_id": "R650", **args})))
    )
    return out


# ----------------------------------------------------------------- content


def test_counts_per_status_and_assignee(api):
    out = summary(build())
    run_fields = ("id", "name", "project_id", "plan_id", "milestone_id", "config",
                  "is_completed", "url")  # fmt: skip
    assert out["run"] == {k: RUN[k] for k in run_fields}  # no description or counters
    assert out["total_tests"] == 310
    assert out["by_status"] == [
        {"status_id": 1, "status": "Passed", "count": 300},
        {"status_id": 5, "status": "Failed", "count": 4},
        {"status_id": 3, "status": "Untested", "count": 3},
        {"status_id": 2, "status": "Blocked", "count": 2},
        {"status_id": 4, "status": "Retest", "count": 1},
    ]
    people = {p["assignedto_id"]: p for p in out["by_assignee"]}
    assert [p["assignedto_id"] for p in out["by_assignee"]] == [19, 20, None]  # biggest first
    assert people[19]["count"] == 154 and people[20]["count"] == 153
    assert people[None] == {"assignedto_id": None, "count": 3, "statuses": {"Passed": 3}}
    # User 19 has the even-numbered tests, user 20 the odd ones (see run_tests).
    assert people[19]["statuses"] == {"Passed": 148, "Failed": 3, "Blocked": 2, "Untested": 1}
    assert people[20]["statuses"] == {"Passed": 149, "Failed": 1, "Untested": 2, "Retest": 1}
    assert sum(p["count"] for p in out["by_assignee"]) == out["total_tests"]


def test_lists_failed_and_blocked_by_default(api):
    out = summary(build())
    assert out["listed_statuses"] == ["Blocked", "Failed"]
    assert [t["id"] - 73800 for t in out["tests"]] == sorted(FAILED | BLOCKED)
    assert out["tests"][0] == {"id": 73803, "case_id": 46303, "title": "case 3", "status_id": 5,
                               "assignedto_id": 20, "status": "Failed"}  # fmt: skip
    assert out["paging"]["has_more"] is False
    assert "note" not in out


def test_list_status_by_name_or_id(api):
    out = summary(build(), list_status=["Retest", 3])
    assert out["listed_statuses"] == ["Untested", "Retest"]
    assert {t["id"] - 73800 for t in out["tests"]} == RETEST | UNTESTED


def test_unknown_list_status_fails_before_reading_the_run(api):
    with pytest.raises(ToolError, match="Unknown status 'Flaky'"):
        asyncio.run(build().call_tool("get_run_summary", {"run_id": 650, "list_status": "Flaky"}))
    assert not api.routes[1].called and not api.routes[2].called  # get_run, get_tests


def test_default_uses_system_names_not_labels(mock_api):
    # An instance that renamed Failed -> "Broken" and Blocked -> "Stuck", and has a custom
    # status labelled "Failed": the built-in statuses are still the ones listed.
    renamed = [
        {**s, "label": {"failed": "Broken", "blocked": "Stuck"}.get(s["name"], s["label"])}
        for s in STATUSES
    ] + [{"id": 6, "name": "custom_status1", "label": "Failed", "is_untested": False}]
    mock_api.get(API + "get_statuses").mock(return_value=httpx.Response(200, json=renamed))
    mock_api.get(API + "get_run/650").mock(return_value=httpx.Response(200, json=RUN))
    mock_tests(mock_api, run_tests())
    out = summary(build())
    assert out["listed_statuses"] == ["Stuck", "Broken"]
    assert len(out["tests"]) == len(FAILED | BLOCKED)


def test_statuses_unavailable_falls_back_to_builtin_ids(mock_api):
    mock_api.get(API + "get_statuses").mock(return_value=httpx.Response(403, json={"error": "x"}))
    mock_api.get(API + "get_run/650").mock(return_value=httpx.Response(200, json=RUN))
    mock_tests(mock_api, run_tests())
    out = summary(build())
    assert out["listed_statuses"] == ["status 2", "status 5"]
    assert len(out["tests"]) == len(FAILED | BLOCKED)
    assert {"status_id": 1, "status": "status 1", "count": 300} in out["by_status"]


def test_empty_run(mock_api):
    mock_api.get(API + "get_statuses").mock(return_value=httpx.Response(200, json=STATUSES))
    mock_api.get(API + "get_run/650").mock(return_value=httpx.Response(200, json=RUN))
    mock_tests(mock_api, [])
    out = summary(build())
    assert out["total_tests"] == 0 and out["by_status"] == [] and out["by_assignee"] == []
    assert out["tests"] == []


# ----------------------------------------------------------------- limits


def test_max_items_note_names_the_run_size(api):
    out = summary(build(), max_items=100)
    assert out["total_tests"] == 100
    assert "Only the first 100 tests were read of 310 in this run" in out["note"]
    assert "max_items (up to 20000)" in out["note"]


def test_run_test_count_sums_status_counters():
    assert run_test_count(RUN) == 310
    assert run_test_count({"passed_count": None, "custom_status7_count": 2}) == 2
    assert run_test_count({}) == 0


def test_listing_is_cut_to_the_output_budget_and_pages_on(mock_api):
    many_failed = [{**t, "status_id": 5, "title": "t" * 200} for t in run_tests(300)]
    mock_api.get(API + "get_statuses").mock(return_value=httpx.Response(200, json=STATUSES))
    mock_api.get(API + "get_run/650").mock(return_value=httpx.Response(200, json=RUN))
    mock_tests(mock_api, many_failed)
    server = build(max_output_chars=5000)
    seen: list[int] = []
    offset = 0
    while True:
        result = asyncio.run(server.call_tool("get_run_summary", {"run_id": 650, "offset": offset}))
        text = text_of(result)
        assert len(text) <= 5000  # summary + listed tests stay within the budget
        out = json.loads(text)
        assert out["total_tests"] == 300  # every page repeats the whole summary
        seen += [t["id"] for t in out["tests"]]
        if not out["paging"]["has_more"]:
            break
        assert "offset=" in out["paging"]["note"]
        offset = out["paging"]["next_offset"]
    assert seen == [t["id"] for t in many_failed]  # nothing skipped, nothing repeated


def test_sends_only_get_requests(api):
    summary(build(), list_status="Failed")
    assert api.calls and all(call.request.method == "GET" for call in api.calls)


def test_is_a_read_only_tool(api):
    tools = {t.name: t for t in asyncio.run(build().list_tools())}
    annotations = tools["get_run_summary"].annotations
    assert annotations is not None and annotations.read_only_hint is True
