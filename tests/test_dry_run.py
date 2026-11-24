"""dry_run on write tools: every check runs, the exact request is returned, nothing is sent.

"Nothing is sent" is checked at the HTTP layer: every request that reaches the mocked
TestRail is recorded, and a dry run may only make GET requests (the read lookups the
write allowlists need), never a POST.
"""

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
from pestrail.write_tools import DRY_RUN_NOTE

from .conftest import API, _no_sleep, make_config, text_of

STATUSES = [
    {"id": 1, "name": "passed", "label": "Passed", "is_untested": False},
    {"id": 3, "name": "untested", "label": "Untested", "is_untested": True},
    {"id": 5, "name": "failed", "label": "Failed", "is_untested": False},
]
ENTRY = "3933d74b-4282-4c1f-be62-a641ab427063"
LOG = b"line 1\nline 2\n"


@pytest.fixture
def roots(tmp_path: Path) -> Path:
    (tmp_path / "log.txt").write_bytes(LOG)
    (tmp_path / "shot.png").write_bytes(b"\x89PNG" + b"0" * 96)
    return tmp_path


@pytest.fixture
def posts(mock_api):
    """Catch-all for POSTs: a dry run must never reach it."""
    mock_api.get(API + "get_statuses").mock(return_value=httpx.Response(200, json=STATUSES))
    return mock_api.post(url__startswith=API).mock(return_value=httpx.Response(500))


def build(roots: Path, **config: Any):
    cfg = make_config(roots=(roots,), **config)
    return create_server(cfg, TestRailClient(cfg, sleep=_no_sleep))


def call(server, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(text_of(asyncio.run(server.call_tool(tool, args))))
    return out


def only_gets(mock_api) -> bool:
    return all(c.request.method == "GET" for c in mock_api.calls)


# ----------------------------------------------------------------- every write tool

CASES: list[tuple[str, dict[str, Any], str, dict[str, Any] | None]] = [
    ("add_result", {"test_id": "T678", "status": "Failed", "comment": "Crash on save"},
     "add_result/678", {"status_id": 5, "comment": "Crash on save"}),
    ("add_result_for_case", {"run_id": "R56", "case_id": "C1234", "status": "passed"},
     "add_result_for_case/56/1234", {"status_id": 1}),
    ("add_results_for_cases",
     {"run_id": 56, "results": [{"case_id": 1, "status": 1}, {"case_id": "C2", "comment": "c"}]},
     "add_results_for_cases/56",
     {"results": [{"case_id": 1, "status_id": 1}, {"case_id": 2, "comment": "c"}]}),
    ("add_attachment_to_result", {"result_id": 999, "file_path": "log.txt"},
     "add_attachment_to_result/999", None),
    ("add_attachment_to_run", {"run_id": "R56", "file_path": "log.txt"},
     "add_attachment_to_run/56", None),
    ("add_attachment_to_case", {"case_id": "C1234", "file_path": "log.txt"},
     "add_attachment_to_case/1234", None),
    ("add_attachment_to_plan", {"plan_id": "P9", "file_path": "log.txt"},
     "add_attachment_to_plan/9", None),
    ("add_attachment_to_plan_entry", {"plan_id": 9, "entry_id": ENTRY, "file_path": "log.txt"},
     f"add_attachment_to_plan_entry/9/{ENTRY}", None),
]  # fmt: skip


@pytest.mark.parametrize(("tool", "args", "endpoint", "body"), CASES, ids=[c[0] for c in CASES])
def test_dry_run_describes_the_request_and_sends_nothing(
    mock_api, posts, roots, tool, args, endpoint, body
):
    out = call(build(roots), tool, {**args, "dry_run": True})
    assert out["dry_run"] is True and out["note"] == DRY_RUN_NOTE
    [request] = out["requests"]
    assert request["http"] == "POST" and request["endpoint"] == endpoint
    if body is None:  # attachment: which file, resolved inside the roots, and its size
        assert "body" not in request
        assert request["file"] == {
            "file": "log.txt",
            "path": str((roots / "log.txt").resolve()),
            "size_bytes": len(LOG),
        }
    else:
        assert request["body"] == body
    if tool == "add_results_for_cases":
        assert out["count"] == 2
    assert posts.call_count == 0 and only_gets(mock_api)


def test_result_with_attachments_dry_run(mock_api, posts, roots):
    out = call(
        build(roots),
        "add_result_with_attachments",
        {"run_id": 56, "case_id": 1234, "status": "Failed", "file_paths": ["log.txt", "shot.png"],
         "dry_run": True},
    )  # fmt: skip
    first, *uploads = out["requests"]
    assert first == {"http": "POST", "endpoint": "add_result_for_case/56/1234",
                     "body": {"status_id": 5}}  # fmt: skip
    assert [u["endpoint"] for u in uploads] == ["add_attachment_to_result/<new result id>"] * 2
    assert [u["file"]["file"] for u in uploads] == ["log.txt", "shot.png"]
    assert posts.call_count == 0 and only_gets(mock_api)


def test_every_write_tool_has_dry_run_and_no_read_tool_does(roots):
    tools = asyncio.run(build(roots).list_tools())
    write_tools = [t for t in tools if not (t.annotations and t.annotations.read_only_hint)]
    assert len(write_tools) == 9
    for tool in tools:
        has = "dry_run" in tool.input_schema.get("properties", {})
        assert has == (tool in write_tools), tool.name
    # Each write method the client allows is reachable from some write tool's preview.
    endpoints = {c[2].split("/")[0] for c in CASES}
    assert endpoints == WRITE_METHODS


# ----------------------------------------------------------------- same body as a real call


@pytest.mark.parametrize("tool", ["add_result", "add_result_for_case", "add_results_for_cases"])
def test_dry_run_body_is_exactly_what_a_real_call_sends(mock_api, roots, tool):
    _, args, endpoint, _ = next(c for c in CASES if c[0] == tool)
    mock_api.get(API + "get_statuses").mock(return_value=httpx.Response(200, json=STATUSES))
    created: Any = {"id": 999, "test_id": 678, "status_id": 5}
    if tool == "add_results_for_cases":
        created = [{"id": 1000 + i, "test_id": 678, "status_id": 1} for i in range(2)]
    sent = mock_api.post(API + endpoint).mock(return_value=httpx.Response(200, json=created))
    server = build(roots)

    preview = call(server, tool, {**args, "dry_run": True})["requests"][0]
    assert sent.call_count == 0
    call(server, tool, args)  # the real call
    assert sent.call_count == 1
    assert json.loads(sent.calls.last.request.content) == preview["body"]
    assert str(sent.calls.last.request.url).endswith(preview["endpoint"])


def test_real_call_still_sends_by_default(mock_api, roots):
    mock_api.get(API + "get_statuses").mock(return_value=httpx.Response(200, json=STATUSES))
    sent = mock_api.post(API + "add_result/678").mock(
        return_value=httpx.Response(200, json={"id": 1, "test_id": 678, "status_id": 1})
    )
    out = call(build(roots), "add_result", {"test_id": 678, "status": "Passed"})
    assert sent.call_count == 1 and "dry_run" not in out


# ----------------------------------------------------------------- checks still apply


@pytest.mark.parametrize(
    ("tool", "args", "message"),
    [
        ("add_result", {"test_id": 678, "status": "Untested"}, "Untested"),
        ("add_result", {"test_id": 678}, "Nothing to add"),
        ("add_result_for_case", {"run_id": 56, "case_id": 1, "custom_fields": {"env": "x"}},
         "must be a system name"),
        ("add_results_for_cases",
         {"run_id": 56, "results": [{"case_id": 1, "status": "Passed"}, {"case_id": 2}]},
         "Nothing was sent"),
        ("add_attachment_to_run", {"run_id": 56, "file_path": "missing.txt"}, "not found"),
        ("add_attachment_to_run", {"run_id": 56, "file_path": "../outside.txt"},
         "outside the allowed attachment directories"),
        ("add_attachment_to_plan_entry",
         {"plan_id": 9, "entry_id": "x/../y", "file_path": "log.txt"},
         "expected an integer or GUID"),
        ("add_result_with_attachments",
         {"run_id": 56, "case_id": 1, "status": "Passed", "file_paths": ["log.txt", "nope.txt"]},
         "No result was added"),
    ],
)  # fmt: skip
def test_dry_run_reports_the_same_errors(mock_api, posts, roots, tool, args, message):
    (roots.parent / "outside.txt").write_text("secret")
    with pytest.raises(ToolError, match=message):
        asyncio.run(build(roots).call_tool(tool, {**args, "dry_run": True}))
    assert posts.call_count == 0 and only_gets(mock_api)


def test_dry_run_applies_the_write_allowlists(mock_api, posts, roots):
    server = build(roots, allowed_run_ids=frozenset({56}))
    with pytest.raises(ToolError, match="outside TESTRAIL_ALLOWED_RUN_IDS"):
        args: dict[str, Any] = {"run_id": 57, "case_id": 1, "status": 1, "dry_run": True}
        asyncio.run(server.call_tool("add_result_for_case", args))
    with pytest.raises(ToolError, match="Cannot check result"):
        args = {"result_id": 5, "file_path": "log.txt", "dry_run": True}
        asyncio.run(server.call_tool("add_attachment_to_result", args))
    assert posts.call_count == 0


def test_dry_run_makes_the_allowlist_lookups_but_no_write(mock_api, posts, roots):
    # Case -> suite -> project, as a real upload would check (get_case has no project_id).
    case = mock_api.get(API + "get_case/1234").mock(
        return_value=httpx.Response(200, json={"id": 1234, "suite_id": 3})
    )
    suite = mock_api.get(API + "get_suite/3").mock(
        return_value=httpx.Response(200, json={"id": 3, "project_id": 7})
    )
    out = call(build(roots, allowed_project_ids=frozenset({7})), "add_attachment_to_case",
               {"case_id": 1234, "file_path": "log.txt", "dry_run": True})  # fmt: skip
    assert out["requests"][0]["endpoint"] == "add_attachment_to_case/1234"
    assert case.call_count == 1 and suite.call_count == 1
    assert posts.call_count == 0 and only_gets(mock_api)
