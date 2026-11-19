"""get_case / get_test: readable rich-text fields, no empty fields, output size budget.

Fixtures mirror real cases: description/steps/expected pasted from Jira or the web as
rich-text HTML, where styling is ~95% of the size (a 50,752-character case on a real
instance holds ~2,300 characters of text).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any

import httpx
import pytest

from pestrail import shaping as sh
from pestrail.client import TestRailClient
from pestrail.server import create_server

from .conftest import API, _no_sleep, make_config
from .test_text import styled

STATUSES = [{"id": 5, "name": "failed", "label": "Failed", "is_untested": False}]

DESCRIPTION = styled("JIRA Ticket: latency estimation") + (
    "<h3>Measurement modes</h3>"
    "<table><tr><th>Mode</th><th>Value</th></tr>"
    "<tr><td>Disabled</td><td>0</td></tr><tr><td>Aggregated</td><td>1</td></tr></table>"
)
STEPS = styled("Run one of these commands") + "<pre>docker run --rm gst-launch-1.0 -q</pre>"
EXPECTED = styled("Output should contain <strong>summary</strong>")


def real_shape_case(**overrides: Any) -> dict[str, Any]:
    case = {
        "id": 45207,
        "title": "Latency estimation",
        "section_id": 10,
        "template_id": 1,
        "type_id": 7,
        "priority_id": 2,
        "milestone_id": None,
        "refs": None,
        "created_on": 1_763_000_000,
        "updated_on": 1_763_000_100,
        "estimate": None,
        "suite_id": 3,
        "labels": [],
        "custom_description": DESCRIPTION,
        "custom_steps": STEPS,
        "custom_expected": EXPECTED,
        "custom_preconds": "",
        "custom_post_steps": None,
        "custom_automated": False,
        "custom_ai_type": 3,
    }
    return {**case, **overrides}


def build(**overrides: Any):
    config = dataclasses.replace(make_config(read_only=True), **overrides)
    return create_server(config, TestRailClient(config, sleep=_no_sleep))


def call(server, name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    result = asyncio.run(server.call_tool(name, args))
    assert not result.is_error, result
    text = result.content[0].text
    return text, json.loads(text)


@pytest.fixture
def statuses(mock_api):
    return mock_api.get(API + "get_statuses").mock(return_value=httpx.Response(200, json=STATUSES))


def mock_case(mock_api, case: dict[str, Any]):
    return mock_api.get(API + f"get_case/{case['id']}").mock(
        return_value=httpx.Response(200, json=case)
    )


# ----------------------------------------------------------------- get_case


def test_rich_text_fields_are_readable(mock_api):
    mock_case(mock_api, real_shape_case())
    _, out = call(build(), "get_case", {"case_id": "C45207"})
    assert out["custom_description"] == (
        "JIRA Ticket: latency estimation\n\n### Measurement modes\n\n"
        "Mode | Value\nDisabled | 0\nAggregated | 1"
    )
    assert (
        out["custom_steps"]
        == "Run one of these commands\n\n```\ndocker run --rm gst-launch-1.0 -q\n```"
    )
    assert out["custom_expected"] == "Output should contain **summary**"
    assert "style" not in json.dumps(out)


def test_empty_fields_are_left_out_and_values_kept(mock_api):
    mock_case(mock_api, real_shape_case())
    _, out = call(build(), "get_case", {"case_id": 45207})
    for key in (
        "milestone_id",
        "refs",
        "estimate",
        "labels",
        "custom_preconds",
        "custom_post_steps",
    ):
        assert key not in out
    assert out["custom_automated"] is False  # False is a value, not "empty"
    assert out["custom_ai_type"] == 3
    assert out["created_on"].startswith("2025-")  # timestamps still converted


def test_raw_format_returns_fields_as_stored(mock_api):
    case = real_shape_case()
    mock_case(mock_api, case)
    _, out = call(build(), "get_case", {"case_id": 45207, "text_format": "raw"})
    assert out["custom_description"] == DESCRIPTION
    assert out["custom_steps"] == STEPS
    assert "note" not in out


def test_separated_steps_are_readable(mock_api):
    steps = [
        {"content": styled("Open the app"), "expected": styled("Main <em>window</em>"), "refs": ""},
        {"content": "Plain step", "expected": "", "additional_info": None},
    ]
    mock_case(mock_api, real_shape_case(custom_steps_separated=steps))
    _, out = call(build(), "get_case", {"case_id": 45207})
    assert out["custom_steps_separated"] == [
        {"content": "Open the app", "expected": "Main _window_", "refs": ""},
        {"content": "Plain step", "expected": "", "additional_info": None},
    ]


def test_markdown_shrinks_a_case_that_raw_would_cut(mock_api):
    # Like the real 50,752-character case: ~24k of styling in each of two fields.
    big = styled(*(["Average latency per frame. "] * 24))
    mock_case(mock_api, real_shape_case(custom_description=big, custom_expected=big))
    server = build()

    text, out = call(server, "get_case", {"case_id": 45207})
    assert len(text) < 5_000 and "note" not in out

    text, out = call(server, "get_case", {"case_id": 45207, "text_format": "raw"})
    assert len(text) <= 40_000
    assert "output size budget" in out["note"] and "text_format='markdown'" in out["note"]
    for key in ("custom_description", "custom_expected"):
        assert out[key].startswith(big[:200])
        assert out[key].endswith(f"... [truncated, {len(big)} chars total]")
    assert out["title"] == "Latency estimation"  # short fields are never cut


def test_oversized_markdown_case_is_cut_to_budget(mock_api):
    text_body = "x" * 9_000
    mock_case(mock_api, real_shape_case(custom_description=text_body, custom_steps=text_body))
    text, out = call(build(max_output_chars=5_000), "get_case", {"case_id": 45207})
    assert len(text) <= 5_000
    assert "output size budget (5000 characters)" in out["note"]
    assert "text_format=" not in out["note"]  # already markdown
    assert out["custom_description"].endswith("[truncated, 9000 chars total]")


# ----------------------------------------------------------------- get_test


def test_get_test_is_readable_with_status(mock_api, statuses):
    test = {
        "id": 73922,
        "case_id": 46468,
        "run_id": 650,
        "status_id": 5,
        "title": "SDK documentation check",
        "assignedto_id": 13,
        "refs": None,
        "custom_description": DESCRIPTION,
        "custom_preconds": None,
    }
    mock_api.get(API + "get_test/73922").mock(return_value=httpx.Response(200, json=test))
    _, out = call(build(), "get_test", {"test_id": "T73922"})
    assert out["status"] == "Failed"
    assert out["custom_description"].startswith("JIRA Ticket: latency estimation")
    assert "refs" not in out and "custom_preconds" not in out


# ----------------------------------------------------------------- fit_object


def test_fit_object_leaves_small_objects_alone():
    obj = {"id": 1, "title": "t", "custom_description": "short"}
    assert sh.fit_object(obj, 1_000) == (obj, False)


def test_fit_object_cuts_only_the_longest_fields():
    obj = {"id": 1, "title": "kept whole " * 30, "a": "a" * 20_000, "b": "b" * 8_000}
    fitted, cut = sh.fit_object(obj, 10_000)
    assert cut
    assert len(sh.to_json_text(fitted)) <= 10_000
    assert fitted["title"] == obj["title"]
    assert fitted["a"].endswith("[truncated, 20000 chars total]")
    assert fitted["b"].endswith("[truncated, 8000 chars total]")


def test_fit_object_gives_up_on_structure_it_cannot_cut():
    obj = {"entries": [{"id": i} for i in range(5_000)]}  # large without long texts
    fitted, cut = sh.fit_object(obj, 1_000)
    assert fitted == obj and cut is False


# ----------------------------------------------------------------- results use the same cleaning


def test_step_results_in_results_are_readable(mock_api, statuses):
    result = {
        "id": 1,
        "test_id": 73922,
        "status_id": 5,
        "custom_step_results": [{"content": styled("Step one"), "actual": styled("Crashed")}],
    }
    mock_api.get(url__startswith=API + "get_results/73922").mock(
        return_value=httpx.Response(200, json=[result])
    )
    _, out = call(build(), "get_results", {"test_id": 73922})
    assert out["results"][0]["custom_step_results"] == [
        {"content": "Step one", "actual": "Crashed"}
    ]
