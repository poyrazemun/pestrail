"""'My tests': get_current_user and the assigned_to filter on get_tests.

Mirrors a real run: 131 tests split between users 19 (the logged-in user, 63 tests),
20 (67) and 13 (1). TestRail filters get_tests by ``assignedto_id`` on current versions
but does not document it, so the filter must stay correct when it is ignored.
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

from pestrail.client import READ_METHODS, WRITE_METHODS, TestRailClient
from pestrail.server import create_server

from .conftest import API, _no_sleep, make_config, text_of

ME = {"id": 19, "name": "QA Person", "email": "qa@example.invalid", "is_active": True,
      "role_id": 3, "role": "Tester", "is_admin": False, "mfa_required": False,
      "group_ids": [1], "email_notifications": True}  # fmt: skip
STATUSES = [
    {"id": 1, "name": "passed", "label": "Passed", "is_untested": False},
    {"id": 5, "name": "failed", "label": "Failed", "is_untested": False},
]


def run_tests() -> list[dict[str, Any]]:
    owners = [19] * 63 + [20] * 67 + [13]
    return [
        {"id": 73800 + i, "case_id": 46300 + i, "run_id": 650, "title": f"case {i}",
         "status_id": 5 if i in (3, 70, 130) else 1, "assignedto_id": owner}
        for i, owner in enumerate(owners)
    ]  # fmt: skip


def mock_tests(mock_api, tests: list[dict[str, Any]], *, honors_assignee: bool = True):
    """Paginated get_tests (TestRail 6.7+): status_id always applied, assignedto_id optional."""

    def respond(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        query = {k: v[0] for k, v in parse_qs(url.split("&", 1)[1] if "&" in url else "").items()}
        items = tests
        if "status_id" in query:
            wanted = {int(x) for x in query["status_id"].split(",")}
            items = [t for t in items if t["status_id"] in wanted]
        if honors_assignee and "assignedto_id" in query:
            wanted = {int(x) for x in query["assignedto_id"].split(",")}
            items = [t for t in items if t["assignedto_id"] in wanted]
        offset, limit = int(query.get("offset", 0)), int(query.get("limit", 250))
        page = items[offset : offset + limit]
        more = offset + limit < len(items)
        return httpx.Response(
            200,
            json={"offset": offset, "limit": limit, "size": len(page),
                  "_links": {"next": "/next" if more else None, "prev": None}, "tests": page},
        )  # fmt: skip

    return mock_api.get(url__startswith=API + "get_tests/650").mock(side_effect=respond)


@pytest.fixture
def me_route(mock_api):
    return mock_api.get(API + "get_current_user").mock(return_value=httpx.Response(200, json=ME))


@pytest.fixture
def statuses(mock_api):
    return mock_api.get(API + "get_statuses").mock(return_value=httpx.Response(200, json=STATUSES))


def build(**overrides: Any):
    config = dataclasses.replace(make_config(read_only=True), **overrides)
    return create_server(config, TestRailClient(config, sleep=_no_sleep))


def call(server, name: str, args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(text_of(asyncio.run(server.call_tool(name, args))))
    return out


def owners(out: dict[str, Any]) -> set[Any]:
    return {t.get("assignedto_id") for t in out["tests"]}


# ----------------------------------------------------------------- get_current_user


def test_get_current_user_is_an_allowlisted_read_method():
    assert "get_current_user" in READ_METHODS
    assert "get_current_user" not in WRITE_METHODS


def test_get_current_user_returns_compact_user(mock_api, me_route):
    out = call(build(), "get_current_user", {})
    assert out == {k: ME[k] for k in ("id", "name", "email", "is_active", "role_id", "role",
                                      "is_admin")}  # fmt: skip


# ----------------------------------------------------------------- assigned_to


@pytest.mark.parametrize("honors_assignee", [True, False], ids=["filtered", "ignored"])
def test_me_returns_only_my_tests(mock_api, me_route, statuses, honors_assignee):
    route = mock_tests(mock_api, run_tests(), honors_assignee=honors_assignee)
    out = call(build(), "get_tests", {"run_id": "R650", "assigned_to": "me"})
    assert len(out["tests"]) == 63 and owners(out) == {19}
    assert out["paging"] == {"offset": 0, "returned": 63, "has_more": False}
    assert "assignedto_id=19" in str(route.calls.last.request.url)


def test_me_is_resolved_once_per_session(mock_api, me_route, statuses):
    mock_tests(mock_api, run_tests())
    server = build()
    call(server, "get_tests", {"run_id": 650, "assigned_to": "me"})
    call(server, "get_tests", {"run_id": 650, "assigned_to": ["me", 20]})
    assert me_route.call_count == 1


def test_get_current_user_fills_the_me_cache(mock_api, me_route, statuses):
    mock_tests(mock_api, run_tests())
    server = build()
    call(server, "get_current_user", {})
    call(server, "get_tests", {"run_id": 650, "assigned_to": "me"})
    assert me_route.call_count == 1


@pytest.mark.parametrize("value", [[19, 20], "19,20", ["me", "20"]])
def test_several_assignees(mock_api, me_route, statuses, value):
    mock_tests(mock_api, run_tests(), honors_assignee=False)
    out = call(build(), "get_tests", {"run_id": 650, "assigned_to": value})
    assert len(out["tests"]) == 130 and owners(out) == {19, 20}


def test_combined_with_status(mock_api, me_route, statuses):
    route = mock_tests(mock_api, run_tests())
    out = call(build(), "get_tests", {"run_id": 650, "assigned_to": "me", "status": "Failed"})
    assert [t["id"] for t in out["tests"]] == [73803]
    assert out["tests"][0]["status"] == "Failed"
    assert str(route.calls.last.request.url).endswith("&status_id=5&assignedto_id=19")


def test_unassigned_is_filtered_locally(mock_api, statuses):
    tests = run_tests()
    tests[5]["assignedto_id"] = None
    route = mock_tests(mock_api, tests)
    out = call(build(), "get_tests", {"run_id": 650, "assigned_to": "unassigned"})
    assert [t["id"] for t in out["tests"]] == [73805]
    assert "assignedto_id" not in str(
        route.calls.last.request.url
    )  # TestRail can't ask for "nobody"


@pytest.mark.parametrize("honors_assignee", [True, False], ids=["filtered", "ignored"])
def test_paging_walks_the_filtered_list(mock_api, me_route, statuses, honors_assignee):
    mock_tests(mock_api, run_tests(), honors_assignee=honors_assignee)
    server = build()
    seen: list[int] = []
    offset = 0
    for _ in range(10):
        out = call(server, "get_tests", {"run_id": 650, "assigned_to": "me", "limit": 25,
                                         "offset": offset})  # fmt: skip
        seen += [t["id"] for t in out["tests"]]
        if not out["paging"]["has_more"]:
            break
        assert out["paging"]["hint"] == "Call again with offset=next_offset."
        offset = out["paging"]["next_offset"]
    assert seen == [t["id"] for t in run_tests() if t["assignedto_id"] == 19]


def test_max_items_cap_is_reported(mock_api, me_route, statuses):
    mock_tests(mock_api, run_tests(), honors_assignee=False)
    out = call(build(), "get_tests", {"run_id": 650, "assigned_to": 20, "max_items": 100})
    assert len(out["tests"]) == 37  # tests 63..99 of the first 100 belong to user 20
    assert "Stopped after 100 tests" in out["note"]


def test_without_assigned_to_nothing_changes(mock_api, statuses):
    route = mock_tests(mock_api, run_tests())
    out = call(build(), "get_tests", {"run_id": 650})
    assert len(out["tests"]) == 131
    assert "assignedto_id" not in str(route.calls.last.request.url)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("bob", "Invalid user ID"),
        ("", "assigned_to is empty"),
        (",", "assigned_to is empty"),
        (list(range(1, 102)), "at most 100"),
    ],
)
def test_bad_assignee_is_rejected_before_listing_tests(mock_api, value, message):
    route = mock_tests(mock_api, run_tests())
    with pytest.raises(ToolError, match=message):
        asyncio.run(build().call_tool("get_tests", {"run_id": 650, "assigned_to": value}))
    assert route.call_count == 0
