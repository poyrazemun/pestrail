"""Optional tool groups (PESTRAIL_TOOLSETS).

Every tool definition is sent to the model in every conversation. Users who never need some
tools can leave their group out to make each conversation cheaper. Tools not listed here are
the core: projects, suites, sections, cases, runs, plans, tests, results and statuses. They
are always available.
"""

from __future__ import annotations

#: Optional groups and their tools. Turning a group off only hides tools: it never changes
#: what the remaining tools may do.
TOOLSETS: dict[str, frozenset[str]] = {
    "attachments": frozenset(
        {
            "get_attachments_for_case",
            "get_attachments_for_test",
            "get_attachments_for_run",
            "get_attachments_for_plan",
            "add_attachment_to_result",
            "add_attachment_to_run",
            "add_attachment_to_case",
            "add_attachment_to_plan",
            "add_attachment_to_plan_entry",
            "add_result_with_attachments",
        }
    ),
    "history": frozenset({"get_history_for_case"}),
    "fields": frozenset(
        {
            "get_case_fields",
            "get_result_fields",
            "get_priorities",
            "get_case_types",
            "get_templates",
            "get_configs",
        }
    ),
    "milestones": frozenset({"get_milestones", "get_milestone"}),
    "users": frozenset({"get_users", "get_user_by_email"}),
}

#: Accepted as a toolset name: the core tools only (no optional group).
CORE = "core"
ALL = "all"


def group_of(tool: str) -> str | None:
    """The optional group a tool belongs to, or None for a core tool."""
    return next((name for name, tools in TOOLSETS.items() if tool in tools), None)
