"""Optional tool groups (PESTRAIL_TOOLSETS): hide tools to make every conversation cheaper."""

from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest

from pestrail.client import TestRailClient
from pestrail.config import ConfigError, load_config, parse_toolsets
from pestrail.server import create_server
from pestrail.toolsets import TOOLSETS, group_of

from .conftest import make_config

ALL_GROUPS = frozenset(TOOLSETS)
GROUPED = frozenset().union(*TOOLSETS.values())
BASE_ENV = {
    "TESTRAIL_URL": "https://testrail.invalid",
    "TESTRAIL_USERNAME": "qa@example.invalid",
    "TESTRAIL_API_KEY": "secret-key",
    "TESTRAIL_READ_ONLY": "true",
}


def build(*, read_only: bool = False, toolsets: frozenset[str] = ALL_GROUPS):
    config = dataclasses.replace(make_config(read_only=read_only), toolsets=toolsets)
    return create_server(config, TestRailClient(config))


def names(server) -> set[str]:
    return {t.name for t in asyncio.run(server.list_tools())}


def size(server) -> int:
    tools = asyncio.run(server.list_tools())
    return sum(
        len(json.dumps(t.model_dump(exclude_none=True), separators=(",", ":"))) for t in tools
    )


# ----------------------------------------------------------------- the groups


def test_every_grouped_tool_exists_and_belongs_to_one_group():
    everything = names(build())
    assert everything >= GROUPED  # no typos: each listed name is a real tool
    for group, tools in TOOLSETS.items():
        assert tools, group
        for other, other_tools in TOOLSETS.items():
            assert other == group or not (tools & other_tools), (group, other)


def test_core_keeps_the_daily_workflow():
    core = names(build(toolsets=frozenset()))
    for tool in (
        "get_projects", "get_cases", "get_case", "get_runs", "get_run", "get_run_summary",
        "get_plans", "get_plan", "get_tests", "get_test", "get_results", "get_statuses",
        "get_current_user", "add_result", "add_result_for_case", "add_results_for_cases",
    ):  # fmt: skip
        assert tool in core and group_of(tool) is None, tool


# ----------------------------------------------------------------- registration


def test_default_registers_every_tool():
    assert len(names(build())) == 43
    assert len(names(build(read_only=True))) == 34


@pytest.mark.parametrize("read_only", [False, True])
def test_core_only_hides_every_group(read_only):
    full = names(build(read_only=read_only))
    core = names(build(read_only=read_only, toolsets=frozenset()))
    assert core == full - GROUPED
    assert size(build(read_only=read_only, toolsets=frozenset())) < 0.75 * size(
        build(read_only=read_only)
    )


@pytest.mark.parametrize("group", sorted(TOOLSETS))
def test_turning_a_group_off_hides_exactly_its_tools(group):
    full = names(build())
    without = names(build(toolsets=ALL_GROUPS - {group}))
    assert without == full - TOOLSETS[group]


def test_hiding_never_adds_write_tools_in_read_only_mode():
    for toolsets in (ALL_GROUPS, frozenset(), frozenset({"attachments"})):
        tools = names(build(read_only=True, toolsets=toolsets))
        assert not any(t.startswith("add_") for t in tools)


def test_hidden_tool_cannot_be_called():
    server = build(toolsets=frozenset())
    with pytest.raises(Exception, match="get_milestones"):
        asyncio.run(server.call_tool("get_milestones", {"project_id": 1}))


def test_instructions_name_hidden_groups_only_when_some_are_hidden():
    assert "turned off" not in (build().instructions or "")
    text = build(toolsets=ALL_GROUPS - {"attachments", "users"}).instructions or ""
    assert "turned off these tool groups to save space: attachments, users." in text
    assert "PESTRAIL_TOOLSETS" in text and '"Tool groups"' in text


# ----------------------------------------------------------------- configuration


@pytest.mark.parametrize("raw", [None, "", "  ", "all", "ALL", "core, all"])
def test_unset_or_all_means_every_group(raw):
    assert parse_toolsets(raw) == ALL_GROUPS


def test_core_means_no_optional_group():
    assert parse_toolsets("core") == frozenset()


def test_groups_are_case_and_space_insensitive():
    assert parse_toolsets(" Attachments ,history,, ") == {"attachments", "history"}
    assert parse_toolsets("core,users") == {"users"}


def test_unknown_group_is_a_clear_error():
    with pytest.raises(ConfigError, match=r"unknown group\(s\) 'atachments'") as exc:
        parse_toolsets("atachments,history")
    assert "Valid groups: core, attachments, history, fields, milestones, users, all." in str(
        exc.value
    )


def test_error_does_not_echo_huge_input():
    with pytest.raises(ConfigError) as exc:
        parse_toolsets("x" * 10_000 + "," + ",".join(f"bad{i}" for i in range(50)))
    assert len(str(exc.value)) < 400


def test_error_names_the_setting():
    with pytest.raises(ConfigError, match=r"^PESTRAIL_TOOLSETS: "):
        parse_toolsets("nope")
    with pytest.raises(ConfigError, match=r"^Tool groups: "):
        parse_toolsets("nope", "Tool groups")


def test_load_config_reads_the_variable():
    assert load_config(BASE_ENV).toolsets == ALL_GROUPS
    config = load_config({**BASE_ENV, "PESTRAIL_TOOLSETS": "history"})
    assert config.toolsets == {"history"}
    with pytest.raises(ConfigError, match="PESTRAIL_TOOLSETS"):
        load_config({**BASE_ENV, "PESTRAIL_TOOLSETS": "everything"})
