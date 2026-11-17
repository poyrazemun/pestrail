"""Results tools: readable comments, compact mode, output size budget and paging.

Fixtures mirror the problem seen on a real run: 250 results whose comments are rich-text
HTML pasted from Word/Jira/the web, with ~1,000 characters of inline CSS per paragraph.
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
from pestrail.config import DEFAULT_MAX_OUTPUT_CHARS, ConfigError, load_config
from pestrail.server import create_server

from .conftest import API, _no_sleep, make_config
from .test_text import STYLE, styled

STATUSES = [
    {"id": 1, "name": "passed", "label": "Passed", "is_untested": False},
    {"id": 5, "name": "failed", "label": "Failed", "is_untested": False},
]
REAL_TEXT = "Login works on Chrome 120 with SSO. Verified redirect and session timeout. "
#: Keys a result had before 0.2.0 (with the fields present in these fixtures).
RESULT_KEYS = {"id", "test_id", "status_id", "status", "comment", "created_by", "created_on"}


def big_comment() -> str:
    """~9,300 characters, like the comments seen in production: CSS dwarfs the text."""
    return styled(*([REAL_TEXT] * 8))


def make_results(count: int, *, comment: str | None = None) -> list[dict[str, Any]]:
    return [
        {
            "id": 1000 + i,
            "test_id": 70000 + i,
            "status_id": 1 if i % 2 else 5,
            "created_by": 3,
            "created_on": 1_700_000_000 + i,
            "comment": comment if comment is not None else big_comment(),
        }
        for i in range(count)
    ]


def build(**overrides: Any):
    config = dataclasses.replace(make_config(read_only=True), **overrides)
    return create_server(config, TestRailClient(config, sleep=_no_sleep))


def call_text(server, name: str, args: dict[str, Any]) -> str:
    result = asyncio.run(server.call_tool(name, args))
    assert not result.is_error, result
    return result.content[0].text


@pytest.fixture
def statuses(mock_api):
    return mock_api.get(API + "get_statuses").mock(return_value=httpx.Response(200, json=STATUSES))


def mock_run_results(mock_api, results: list[dict[str, Any]], run_id: int = 650):
    """Paginated get_results_for_run that honors limit/offset like TestRail 6.7+."""

    def respond(request: httpx.Request) -> httpx.Response:
        query = parse_qs(str(request.url).split("&", 1)[1] if "&" in str(request.url) else "")
        offset = int(query.get("offset", ["0"])[0])
        limit = int(query.get("limit", ["250"])[0])
        page = results[offset : offset + limit]
        more = offset + limit < len(results)
        return httpx.Response(
            200,
            json={
                "offset": offset,
                "limit": limit,
                "size": len(page),
                "_links": {"next": "/next" if more else None, "prev": None},
                "results": page,
            },
        )

    return mock_api.get(url__startswith=API + f"get_results_for_run/{run_id}").mock(
        side_effect=respond
    )


def mock_test_results(mock_api, results: list[dict[str, Any]], test_id: int = 73925):
    return mock_api.get(url__startswith=API + f"get_results/{test_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "offset": 0,
                "limit": 250,
                "size": len(results),
                "_links": {"next": None, "prev": None},
                "results": results,
            },
        )
    )


# ----------------------------------------------------------------- acceptance tests


def test_styled_paragraph_is_returned_as_its_text(mock_api, statuses):
    mock_test_results(mock_api, [{"id": 1, "test_id": 73925, "comment": styled("Real text")}])
    out = json.loads(call_text(build(), "get_results", {"test_id": "T73925"}))
    assert out["results"][0]["comment"] == "Real text"


def test_long_comment_is_cut_after_cleaning(mock_api, statuses):
    comment = big_comment()
    mock_test_results(mock_api, [{"id": 1, "test_id": 73925, "comment": comment}])
    out = json.loads(
        call_text(build(), "get_results", {"test_id": 73925, "comment_max_chars": 200})
    )
    text = out["results"][0]["comment"]
    cleaned_length = len("\n\n".join([REAL_TEXT.strip()] * 8))
    assert "style" not in text and "margin" not in text
    assert text.startswith(REAL_TEXT.strip()[:50])
    assert text.endswith(f"... [truncated, {cleaned_length} chars total]")
    assert len(text.split("... [truncated")[0]) == 200


def test_full_comment_returns_whole_cleaned_text(mock_api, statuses):
    mock_test_results(mock_api, [{"id": 1, "test_id": 73925, "comment": big_comment()}])
    out = json.loads(
        call_text(
            build(),
            "get_results",
            {"test_id": 73925, "full_comment": True, "comment_max_chars": 100},
        )
    )
    assert out["results"][0]["comment"] == "\n\n".join([REAL_TEXT.strip()] * 8)


def test_run_without_comments_stays_small(mock_api, statuses):
    mock_run_results(mock_api, make_results(250))
    text = call_text(
        build(), "get_results_for_run", {"run_id": "R650", "limit": 250, "include_comments": False}
    )
    out = json.loads(text)
    assert len(text) < 40_000  # the original call produced 135,318 characters
    assert out["paging"]["returned"] == 250
    assert all("comment" not in r for r in out["results"])


def test_old_call_keeps_the_same_fields(mock_api, statuses):
    mock_run_results(mock_api, make_results(3, comment=styled("Real text")))
    out = json.loads(call_text(build(), "get_results_for_run", {"run_id": 650}))
    assert set(out) == {"results", "paging"}
    assert {"offset", "returned", "has_more"} <= set(out["paging"])
    for result in out["results"]:
        assert set(result) == RESULT_KEYS
        assert result["comment"] == "Real text"  # value is cleaned; the field is unchanged


# ----------------------------------------------------------------- size budget and paging


def test_large_page_is_cut_to_budget_and_paging_continues(mock_api, statuses):
    results = make_results(250)
    mock_run_results(mock_api, results)
    server = build()

    seen: list[int] = []
    offset = 0
    for _ in range(50):
        text = call_text(server, "get_results_for_run", {"run_id": 650, "offset": offset})
        assert len(text) <= DEFAULT_MAX_OUTPUT_CHARS
        out = json.loads(text)
        seen.extend(r["id"] for r in out["results"])
        if not out["paging"]["has_more"]:
            break
        assert "output size budget" in out["paging"]["note"]
        assert out["paging"]["next_offset"] == offset + out["paging"]["returned"]
        offset = out["paging"]["next_offset"]
    assert seen == [r["id"] for r in results]  # every result exactly once, in order


def test_single_oversized_item_is_still_returned(mock_api, statuses):
    huge = "y" * 20_000
    mock_run_results(mock_api, make_results(2, comment=huge))
    server = build(max_output_chars=5_000)
    out = json.loads(
        call_text(server, "get_results_for_run", {"run_id": 650, "full_comment": True})
    )
    assert out["paging"]["returned"] == 1  # never zero, so paging can't get stuck
    assert out["paging"]["next_offset"] == 1


def test_fetch_all_respects_budget_and_reports_next_offset(mock_api, statuses):
    mock_run_results(mock_api, make_results(250))
    out = json.loads(call_text(build(), "get_results_for_run", {"run_id": 650, "fetch_all": True}))
    assert out["truncated"] is True
    assert out["count"] == out["paging"]["returned"] < 250
    assert out["paging"]["next_offset"] == out["count"]
    assert "output size budget" in out["note"]


def test_output_has_one_result_per_line(mock_api, statuses):
    mock_run_results(mock_api, make_results(5, comment=styled("a", "b")))
    text = call_text(build(), "get_results_for_run", {"run_id": 650})
    lines = text.splitlines()
    assert sum(1 for line in lines if line.startswith('{"id":')) == 5
    json.loads(text)  # still one valid JSON document


# ----------------------------------------------------------------- comment options


def test_plain_and_markdown_comments_are_untouched(mock_api, statuses):
    plain = "1. a < b\n2. **bold** and `code`"
    mock_test_results(mock_api, [{"id": 1, "test_id": 73925, "comment": plain}])
    out = json.loads(call_text(build(), "get_results", {"test_id": 73925}))
    assert out["results"][0]["comment"] == plain


def test_raw_format_reproduces_old_behavior(mock_api, statuses):
    comment = big_comment()
    mock_test_results(mock_api, [{"id": 1, "test_id": 73925, "comment": comment}])
    out = json.loads(
        call_text(
            build(),
            "get_results",
            {"test_id": 73925, "comment_format": "raw", "comment_max_chars": 1000},
        )
    )
    assert out["results"][0]["comment"] == (
        comment[:1000] + f"... [truncated, {len(comment)} chars total]"
    )


def test_custom_fields_with_html_are_cleaned(mock_api, statuses):
    mock_test_results(
        mock_api,
        [{"id": 1, "test_id": 73925, "comment": "c", "custom_notes": styled("Env: staging")}],
    )
    out = json.loads(call_text(build(), "get_results", {"test_id": 73925}))
    assert out["results"][0]["custom_notes"] == "Env: staging"


@pytest.mark.parametrize("value", [99, 50_001])
def test_comment_max_chars_is_bounded(mock_api, value):
    route = mock_api.route().mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(ToolError, match="comment_max_chars"):
        asyncio.run(build().call_tool("get_results", {"test_id": 1, "comment_max_chars": value}))
    assert route.call_count == 0


# ----------------------------------------------------------------- result_id lookup


def test_result_id_returns_only_that_result(mock_api, statuses):
    mock_test_results(
        mock_api,
        [
            {"id": 501, "test_id": 73925, "comment": styled("newer")},
            {"id": 500, "test_id": 73925, "comment": styled("x" * 5000)},
        ],
    )
    out = json.loads(
        call_text(
            build(), "get_results", {"test_id": 73925, "result_id": 500, "full_comment": True}
        )
    )
    assert [r["id"] for r in out["results"]] == [500]
    assert out["results"][0]["comment"] == "x" * 5000


def test_unknown_result_id_returns_empty_with_note(mock_api, statuses):
    mock_test_results(mock_api, [{"id": 501, "test_id": 73925, "comment": "c"}])
    out = json.loads(call_text(build(), "get_results", {"test_id": 73925, "result_id": 999}))
    assert out["results"] == []
    assert "999" in out["note"]


# ----------------------------------------------------------------- configuration

BASE_ENV = {
    "TESTRAIL_URL": "https://testrail.invalid",
    "TESTRAIL_USERNAME": "qa@example.invalid",
    "TESTRAIL_API_KEY": "k",
    "TESTRAIL_READ_ONLY": "true",
}


def test_max_output_chars_setting():
    assert load_config(BASE_ENV).max_output_chars == DEFAULT_MAX_OUTPUT_CHARS
    cfg = load_config({**BASE_ENV, "PESTRAIL_MAX_OUTPUT_CHARS": "80_000"})
    assert cfg.max_output_chars == 80_000


@pytest.mark.parametrize("raw", ["abc", "100", "2000000", "-5"])
def test_max_output_chars_invalid(raw):
    with pytest.raises(ConfigError, match="PESTRAIL_MAX_OUTPUT_CHARS"):
        load_config({**BASE_ENV, "PESTRAIL_MAX_OUTPUT_CHARS": raw})


def test_style_fixture_is_realistic():
    # Guard the fixture itself: each paragraph carries ~1,000 characters of CSS.
    assert 900 < len(STYLE) < 1200
    assert 8_000 < len(big_comment()) < 11_000
