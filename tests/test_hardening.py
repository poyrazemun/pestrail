"""Regression tests for the deep-review fixes (items S1-S4, R1-R2, E1, B1, B3, B4).

Timing tests use inputs that took 6-9 s before the fix and well under 0.5 s after; the
2 s threshold leaves ample headroom for slow CI runners.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

import pestrail
from pestrail import client as client_module
from pestrail.client import TestRailClient, TestRailError, read_upload
from pestrail.ids import parse_id, resolve_status
from pestrail.read_tools import to_timestamp
from pestrail.server import create_server
from pestrail.text import MAX_HTML_CHARS, MAX_LIST_INDENT, html_to_markdown

from .conftest import API, _no_sleep, make_config, run

SLOW = 2.0  # seconds; the unfixed code took 6-9 s on these inputs
STATUSES = [{"id": 1, "name": "passed", "label": "Passed", "is_untested": False}]


def timed(fn, *args):
    start = time.perf_counter()
    result = fn(*args)
    return result, time.perf_counter() - start


def build(**overrides: Any):
    config = dataclasses.replace(make_config(read_only=True), **overrides)
    return create_server(config, TestRailClient(config, sleep=_no_sleep))


@pytest.fixture
def statuses(mock_api):
    return mock_api.get(API + "get_statuses").mock(return_value=httpx.Response(200, json=STATUSES))


def paginated(key: str, items: list[dict[str, Any]]):
    """respx side effect serving ``items`` with TestRail 6.7+ limit/offset pagination."""

    def respond(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        query = parse_qs(url.split("&", 1)[1]) if "&" in url else {}
        offset = int(query.get("offset", ["0"])[0])
        limit = int(query.get("limit", ["250"])[0])
        page = items[offset : offset + limit]
        more = offset + limit < len(items)
        return httpx.Response(
            200,
            json={
                "offset": offset,
                "limit": limit,
                "size": len(page),
                "_links": {"next": "/next" if more else None, "prev": None},
                key: page,
            },
        )

    return respond


# ----------------------------------------------------------------- S1: quadratic HTML


def test_s1_nested_unclosed_links_are_linear():
    html = "<p>" + "<a href='u'>x" * 8000 + "</a>" * 8000  # took 6.4 s before
    out, seconds = timed(html_to_markdown, html)
    assert seconds < SLOW
    assert out.startswith("x")


def test_s1_whitespace_paragraphs_in_table_cell_are_linear():
    html = "<table><tr><td>" + " <p>" * 20000 + "x</td></tr></table>"  # took 9.3 s before
    out, seconds = timed(html_to_markdown, html)
    assert seconds < SLOW
    assert out == "x"


def test_s1_links_still_render():
    html = '<p>see <a href="https://j.example/UI-5">UI-5</a> now</p>'
    assert html_to_markdown(html) == "see UI-5 (https://j.example/UI-5) now"


# ----------------------------------------------------------------- S2: output blow-up


def test_s2_deeply_nested_lists_do_not_multiply_output():
    html = "<ul><li>x" * 2000 + "</li></ul>" * 2000  # produced 4 MB from 38 KB before
    out = html_to_markdown(html)
    assert len(out) < 5 * len(html)
    deepest = max(len(line) - len(line.lstrip(" ")) for line in out.splitlines())
    assert deepest <= 2 * MAX_LIST_INDENT


def test_s2_html_beyond_cap_is_not_converted():
    html = "<p>" + "word " * 200_000 + "</p>"  # ~1 MB
    out, seconds = timed(html_to_markdown, html)
    assert seconds < SLOW
    skipped = len(html) - MAX_HTML_CHARS
    assert out.endswith(f"[... {skipped} more characters of HTML were not converted]")


# ----------------------------------------------------------------- S3: email regex


def test_s3_long_email_is_rejected_fast_without_request(mock_api):
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    email = "a@" + "b." * 32_000 + "@"  # took 6.4 s in the regex before
    start = time.perf_counter()
    with pytest.raises(ToolError, match="email"):
        asyncio.run(build().call_tool("get_user_by_email", {"email": email}))
    assert time.perf_counter() - start < SLOW
    assert route.call_count == 0


def test_s3_normal_email_still_works(mock_api):
    mock_api.get(url__startswith=API + "get_user_by_email").mock(
        return_value=httpx.Response(200, json={"id": 7, "name": "A", "email": "a@example.invalid"})
    )
    result = asyncio.run(build().call_tool("get_user_by_email", {"email": "a@example.invalid"}))
    assert json.loads(result.content[0].text)["id"] == 7


# ----------------------------------------------------------------- S4: capped upload read


def test_s4_read_upload_enforces_limit(tmp_path):
    f = tmp_path / "growing.log"
    f.write_bytes(b"x" * 150)
    assert read_upload(f, 150) == b"x" * 150
    with pytest.raises(TestRailError, match="may have grown"):
        read_upload(f, 100)


def test_s4_file_that_grew_past_limit_is_not_uploaded(mock_api, tmp_path, monkeypatch):
    # The file passed validation earlier; it is now larger than the limit at read time.
    monkeypatch.setattr(client_module, "MAX_UPLOAD_BYTES", 100)
    f = tmp_path / "live.log"
    f.write_bytes(b"x" * 500)
    route = mock_api.post(API + "add_attachment_to_run/1").mock(
        return_value=httpx.Response(200, json={"attachment_id": 1})
    )
    c = TestRailClient(make_config(), sleep=_no_sleep)
    with pytest.raises(TestRailError, match="limit"):
        run(c.upload("add_attachment_to_run", 1, file_path=f))
    assert route.call_count == 0


# ----------------------------------------------------------------- R1: digits


@pytest.mark.parametrize("value", ["9" * 5000, "C" + "9" * 5000, "²", "1²", 10**30, "9" * 19])
def test_r1_parse_id_rejects_cleanly(value):
    with pytest.raises(TestRailError) as info:
        parse_id(value, "case")
    assert len(str(info.value)) < 200  # huge inputs are not echoed back


def test_r1_parse_id_still_accepts_normal_ids():
    assert parse_id("C1234", "case") == 1234
    assert parse_id("9" * 18, "case") == int("9" * 18)


@pytest.mark.parametrize("value", ["²", "9" * 5000])
def test_r1_resolve_status_rejects_cleanly(value):
    with pytest.raises(TestRailError):
        resolve_status(value, STATUSES)


@pytest.mark.parametrize("value", ["9" * 5000, "²", "2024-01-01" + "x" * 100])
def test_r1_to_timestamp_rejects_cleanly(value):
    with pytest.raises(TestRailError):
        to_timestamp(value)


def test_r1_absurd_content_length_is_too_large_not_a_crash(mock_api):
    mock_api.get(API + "get_projects").mock(
        return_value=httpx.Response(200, headers={"Content-Length": "9" * 5000}, content=b"[]")
    )
    c = TestRailClient(make_config(), sleep=_no_sleep)
    with pytest.raises(TestRailError, match="too large"):
        run(c.get("get_projects"))


# ----------------------------------------------------------------- B3: trailing Z


def test_b3_trailing_z_is_utc():
    assert to_timestamp("2024-01-01T00:00:00Z") == 1704067200
    assert to_timestamp("2024-01-01T00:00:00+00:00") == 1704067200


# ----------------------------------------------------------------- R2: result_id lookup


def test_r2_result_beyond_scan_cap_says_so(mock_api, statuses, monkeypatch):
    from pestrail import read_tools

    monkeypatch.setattr(read_tools, "RESULT_LOOKUP_MAX", 5)
    results = [{"id": 100 + i, "test_id": 9, "comment": "c"} for i in range(20)]
    mock_api.get(url__startswith=API + "get_results/9").mock(
        side_effect=paginated("results", results)
    )
    text = (
        asyncio.run(build().call_tool("get_results", {"test_id": 9, "result_id": 119, "limit": 5}))
        .content[0]
        .text
    )
    out = json.loads(text)
    assert out["results"] == []
    assert "newest 5 results" in out["note"]


def test_r2_lookup_stops_once_found(mock_api, statuses):
    results = [{"id": 100 + i, "test_id": 9, "comment": "c"} for i in range(200)]
    route = mock_api.get(url__startswith=API + "get_results/9").mock(
        side_effect=paginated("results", results)
    )
    text = (
        asyncio.run(build().call_tool("get_results", {"test_id": 9, "result_id": 102, "limit": 10}))
        .content[0]
        .text
    )
    assert [r["id"] for r in json.loads(text)["results"]] == [102]
    assert route.call_count == 1  # the first page had it; no further pages fetched


# ----------------------------------------------------------------- E1: fetch_all early stop


def test_e1_fetch_all_stops_fetching_when_budget_is_full(mock_api, statuses):
    results = [
        {"id": 1000 + i, "test_id": 7000 + i, "status_id": 1, "comment": "y" * 1900}
        for i in range(1000)
    ]
    route = mock_api.get(url__startswith=API + "get_results_for_run/650").mock(
        side_effect=paginated("results", results)
    )
    text = (
        asyncio.run(
            build().call_tool(
                "get_results_for_run", {"run_id": 650, "fetch_all": True, "limit": 50}
            )
        )
        .content[0]
        .text
    )
    out = json.loads(text)
    # ~18 results fit the 40,000-character budget, so one 50-item page is enough.
    # Before the fix all 20 pages (1,000 items) were fetched and then mostly discarded.
    assert route.call_count == 1
    assert out["truncated"] is True
    assert out["paging"]["next_offset"] == out["count"]


# ----------------------------------------------------------------- B1 / B4: hygiene


def test_b1_source_files_are_ascii():
    src = Path(pestrail.__file__).parent
    offenders = [
        f"{path.name}:{number}"
        for path in sorted(src.glob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if not line.isascii()
    ]
    assert offenders == []  # use \uXXXX escapes for invisible/special characters


def test_b4_ci_actions_are_pinned_to_commit_shas():
    workflow = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
    if not workflow.exists():  # not shipped in the sdist
        pytest.skip("CI workflow not available")
    uses = re.findall(r"uses:\s*(\S+)", workflow.read_text(encoding="utf-8"))
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
