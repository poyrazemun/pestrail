"""Read-only MCP tools. These are always registered."""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from . import shaping as sh
from .client import TestRailError
from .ids import parse_id, parse_id_list, parse_optional_id, resolve_status_filter

if TYPE_CHECKING:
    from .server import Registry

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# --------------------------------------------------------------------------- parameter types

ProjectId = Annotated[
    int | str, Field(description="Project ID, e.g. 1 or 'P1'. Use get_projects to find it.")
]
SuiteId = Annotated[
    int | str, Field(description="Suite ID, e.g. 3 or 'S3'. Use get_suites to find it.")
]
OptSuiteId = Annotated[
    int | str | None,
    Field(
        description="Suite ID (e.g. 3 or 'S3'). Required for projects with multiple suites; "
        "omit for single-suite projects. Use get_suites."
    ),
]
CaseId = Annotated[
    int | str, Field(description="Test case ID, e.g. 1234 or 'C1234'. Use get_cases to find it.")
]
RunId = Annotated[
    int | str,
    Field(
        description="Test run ID, e.g. 56 or 'R56'. Use get_runs, or get_plan for runs in plans."
    ),
]
TestId = Annotated[
    int | str,
    Field(
        description="Test ID (an instance of a case inside a run), e.g. 678 or 'T678'. "
        "Use get_tests(run_id) to find it. Not the same as a case ID."
    ),
]
PlanId = Annotated[
    int | str, Field(description="Test plan ID, e.g. 9 or 'P9'. Use get_plans to find it.")
]
MilestoneId = Annotated[
    int | str, Field(description="Milestone ID, e.g. 2 or 'M2'. Use get_milestones to find it.")
]
Limit = Annotated[
    int | None,
    Field(ge=1, le=250, description="Max items to return (1-250). Default: TestRail's page size."),
]
Offset = Annotated[int, Field(ge=0, description="Number of items to skip, for paging.")]
FetchAll = Annotated[
    bool,
    Field(
        description="If true, follow pagination and return all items up to max_items. "
        "Use sparingly on large projects; prefer filters."
    ),
]
MaxItems = Annotated[
    int, Field(ge=1, le=5000, description="Safety cap on items when fetch_all is true.")
]
IdList = Annotated[
    list[int | str] | int | str | None,
    Field(description="One ID or a list of IDs to filter by."),
]
StatusFilter = Annotated[
    list[int | str] | int | str | None,
    Field(
        description="Filter by status: IDs or names, e.g. ['Failed', 'Retest'] or [5, 4]. "
        "Use get_statuses to see options."
    ),
]
DateFilter = Annotated[
    int | str | None,
    Field(description="Date as 'YYYY-MM-DD', ISO-8601 datetime, or unix timestamp (UTC)."),
]
OptBool = Annotated[bool | None, Field(description="Omit for no filter.")]

FIELD_TYPES = {
    1: "String",
    2: "Integer",
    3: "Text",
    4: "URL",
    5: "Checkbox",
    6: "Dropdown",
    7: "User",
    8: "Date",
    9: "Milestone",
    10: "Steps",
    11: "Step Results",
    12: "Multi-select",
    13: "Scenario",
}


#: Reject timestamps beyond ~2100-01-01 UTC (and negatives).
_MAX_TIMESTAMP = 4_102_444_800


def to_timestamp(value: int | str | None) -> int | None:
    """Accept unix seconds, 'YYYY-MM-DD' or an ISO-8601 datetime; return unix seconds (UTC)."""
    if value is None or value == "":
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        ts = value
    else:
        text = str(value).strip()
        if text.isdigit():
            ts = int(text)
        else:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise TestRailError(
                    f"Invalid date {value!r}: use 'YYYY-MM-DD', ISO-8601 or a unix timestamp."
                ) from exc
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            ts = int(parsed.timestamp())
    if ts < 0 or ts > _MAX_TIMESTAMP:
        raise TestRailError(f"Date out of range: {value!r}.")
    return ts


async def list_page(
    reg: "Registry",
    method: str,
    *path_args: int,
    key: str,
    shape: Callable[[dict[str, Any]], dict[str, Any]],
    params: dict[str, Any] | None = None,
    limit: int | None = None,
    offset: int = 0,
    fetch_all: bool = False,
    max_items: int = 1000,
) -> dict[str, Any]:
    """Fetch a list endpoint (one page or all pages) and return a compact, shaped payload."""
    params = {k: v for k, v in (params or {}).items() if v is not None}
    client = reg.client

    if fetch_all:
        if limit:
            params["limit"] = limit
        if offset:
            params["offset"] = offset
        items, truncated = await client.get_all(
            method, *path_args, key=key, params=params, max_items=max_items
        )
        out: dict[str, Any] = {key: [shape(i) for i in items], "count": len(items)}
        if truncated:
            out["truncated"] = True
            out["note"] = (
                f"Stopped at max_items={max_items}. Narrow the filters, raise max_items, "
                f"or page with offset={offset + len(items)}."
            )
        return out

    if limit:
        params["limit"] = limit
    if offset:
        params["offset"] = offset
    items, meta = await client.get_page(method, *path_args, key=key, params=params)

    if not meta.get("paginated"):
        # TestRail < 6.7 returns bare lists and ignores limit/offset, so the whole list
        # came back; page it client-side.
        total = len(items)
        end = offset + limit if limit else total
        items = items[offset:end]
        has_more = end < total
    else:
        has_more = bool(meta.get("has_more"))

    paging: dict[str, Any] = {"offset": offset, "returned": len(items), "has_more": has_more}
    if has_more:
        paging["next_offset"] = offset + len(items)
        paging["hint"] = "Call again with offset=next_offset, or use fetch_all=true."
    return {key: [shape(i) for i in items], "paging": paging}


def register(reg: "Registry") -> None:
    client = reg.client

    # ------------------------------------------------------------------ projects / suites

    @reg.read
    async def get_projects(
        is_completed: OptBool = None, limit: Limit = None, offset: Offset = 0
    ) -> dict:
        """List TestRail projects. Start here to find a project_id.

        is_completed: true = only completed (archived) projects, false = only active ones.
        suite_mode in the output: 1 = single suite, 2 = single suite + baselines,
        3 = multiple suites (then most case/section tools need a suite_id).
        """
        return await list_page(
            reg,
            "get_projects",
            key="projects",
            shape=lambda p: sh.pick(p, sh.PROJECT_FIELDS),
            params={"is_completed": is_completed},
            limit=limit,
            offset=offset,
        )

    @reg.read
    async def get_project(project_id: ProjectId) -> dict:
        """Get one project's details by ID."""
        return sh.full(await client.get("get_project", parse_id(project_id, "project")))

    @reg.read
    async def get_suites(project_id: ProjectId) -> dict:
        """List the test suites in a project. Use the suite IDs with get_sections/get_cases."""
        data = await client.get("get_suites", parse_id(project_id, "project"))
        items = data.get("suites", []) if isinstance(data, dict) else data
        return {"suites": [sh.pick(s, sh.SUITE_FIELDS) for s in items or []]}

    @reg.read
    async def get_suite(suite_id: SuiteId) -> dict:
        """Get one test suite by ID."""
        return sh.full(await client.get("get_suite", parse_id(suite_id, "suite")))

    @reg.read
    async def get_sections(
        project_id: ProjectId,
        suite_id: OptSuiteId = None,
        limit: Limit = None,
        offset: Offset = 0,
        fetch_all: FetchAll = False,
        max_items: MaxItems = 1000,
    ) -> dict:
        """List sections (folders) of a suite. parent_id/depth describe the tree.

        Use section IDs to filter get_cases. suite_id is required for multi-suite projects.
        """
        return await list_page(
            reg,
            "get_sections",
            parse_id(project_id, "project"),
            key="sections",
            shape=lambda s: sh.pick(s, sh.SECTION_FIELDS),
            params={"suite_id": parse_optional_id(suite_id, "suite")},
            limit=limit,
            offset=offset,
            fetch_all=fetch_all,
            max_items=max_items,
        )

    # ------------------------------------------------------------------ cases

    @reg.read
    async def get_cases(
        project_id: ProjectId,
        suite_id: OptSuiteId = None,
        section_id: Annotated[
            int | str | None,
            Field(description="Only cases directly in this section (get_sections)."),
        ] = None,
        filter: Annotated[
            str | None, Field(description="Only cases whose title contains this text.")
        ] = None,
        priority_id: Annotated[
            IdList, Field(description="Priority ID(s); see get_priorities.")
        ] = None,
        type_id: Annotated[IdList, Field(description="Case type ID(s).")] = None,
        milestone_id: Annotated[
            IdList, Field(description="Milestone ID(s), e.g. 2 or ['M2', 'M3'].")
        ] = None,
        template_id: Annotated[IdList, Field(description="Template ID(s).")] = None,
        refs: Annotated[
            str | None, Field(description="Only cases with this reference, e.g. a Jira key.")
        ] = None,
        updated_after: DateFilter = None,
        updated_before: DateFilter = None,
        limit: Limit = None,
        offset: Offset = 0,
        fetch_all: FetchAll = False,
        max_items: MaxItems = 1000,
    ) -> dict:
        """List test cases in a project/suite with optional filters (compact fields only).

        Use get_case for a case's full details (steps, expected results, custom fields).
        For projects in multi-suite mode, suite_id is required (see get_suites).
        """
        params = {
            "suite_id": parse_optional_id(suite_id, "suite"),
            "section_id": parse_optional_id(section_id, "section"),
            "filter": filter,
            "priority_id": parse_id_list(priority_id, "priority"),
            "type_id": parse_id_list(type_id, "case_type"),
            "milestone_id": parse_id_list(milestone_id, "milestone"),
            "template_id": parse_id_list(template_id, "template"),
            "refs": refs,
            "updated_after": to_timestamp(updated_after),
            "updated_before": to_timestamp(updated_before),
        }
        return await list_page(
            reg,
            "get_cases",
            parse_id(project_id, "project"),
            key="cases",
            shape=lambda c: sh.pick(c, sh.CASE_FIELDS),
            params=params,
            limit=limit,
            offset=offset,
            fetch_all=fetch_all,
            max_items=max_items,
        )

    @reg.read
    async def get_case(case_id: CaseId) -> dict:
        """Get the full details of a test case, including steps and custom fields."""
        return sh.full(await client.get("get_case", parse_id(case_id, "case")))

    # ------------------------------------------------------------------ metadata

    def _field(f: dict[str, Any]) -> dict[str, Any]:
        out = sh.pick(
            f,
            (
                "id",
                "system_name",
                "name",
                "label",
                "description",
                "type_id",
                "is_active",
                "include_all",
                "template_ids",
                "configs",
            ),
        )
        if f.get("type_id") in FIELD_TYPES:
            out["type"] = FIELD_TYPES[f["type_id"]]
        return out

    @reg.read
    async def get_case_fields() -> dict:
        """List the case fields (system and custom) configured on this TestRail instance."""
        return {"case_fields": [_field(f) for f in await client.get("get_case_fields") or []]}

    @reg.read
    async def get_result_fields() -> dict:
        """List the custom result fields available when adding results.

        Pass them to the add_result* tools in custom_fields, keyed by system_name
        (e.g. {"custom_environment": "staging"}). For Dropdown fields pass the numeric
        option ID from configs[].options.items ("1, Chrome" -> 1). configs[].context
        shows which projects a field applies to; options.is_required shows if it is mandatory.
        """
        return {"result_fields": [_field(f) for f in await client.get("get_result_fields") or []]}

    @reg.read
    async def get_statuses() -> dict:
        """List result statuses (IDs, names, labels), including custom statuses.

        Result tools accept either the ID or the name/label. 'Untested' (is_untested=true)
        cannot be set as a result.
        """
        statuses = await client.get_statuses(refresh=True)
        return {
            "statuses": [
                sh.pick(s, ("id", "name", "label", "is_system", "is_untested", "is_final"))
                for s in statuses
            ]
        }

    @reg.read
    async def get_priorities() -> dict:
        """List case priorities (IDs for the priority_id filter of get_cases)."""
        data = await client.get("get_priorities") or []
        return {
            "priorities": [
                sh.pick(p, ("id", "name", "short_name", "priority", "is_default")) for p in data
            ]
        }

    # ------------------------------------------------------------------ milestones

    @reg.read
    async def get_milestones(
        project_id: ProjectId,
        is_completed: OptBool = None,
        is_started: OptBool = None,
        limit: Limit = None,
        offset: Offset = 0,
    ) -> dict:
        """List milestones in a project."""
        return await list_page(
            reg,
            "get_milestones",
            parse_id(project_id, "project"),
            key="milestones",
            shape=lambda m: sh.pick(m, sh.MILESTONE_FIELDS),
            params={"is_completed": is_completed, "is_started": is_started},
            limit=limit,
            offset=offset,
        )

    @reg.read
    async def get_milestone(milestone_id: MilestoneId) -> dict:
        """Get one milestone by ID."""
        return sh.full(await client.get("get_milestone", parse_id(milestone_id, "milestone")))

    # ------------------------------------------------------------------ plans / runs

    @reg.read
    async def get_plans(
        project_id: ProjectId,
        is_completed: OptBool = None,
        milestone_id: Annotated[
            IdList, Field(description="Milestone ID(s), e.g. 2 or ['M2', 'M3'].")
        ] = None,
        created_after: DateFilter = None,
        created_before: DateFilter = None,
        created_by: Annotated[IdList, Field(description="Creator user ID(s).")] = None,
        limit: Limit = None,
        offset: Offset = 0,
        fetch_all: FetchAll = False,
        max_items: MaxItems = 1000,
    ) -> dict:
        """List test plans in a project. Use get_plan to see the runs inside a plan."""
        params = {
            "is_completed": is_completed,
            "milestone_id": parse_id_list(milestone_id, "milestone"),
            "created_after": to_timestamp(created_after),
            "created_before": to_timestamp(created_before),
            "created_by": parse_id_list(created_by, "user"),
        }
        return await list_page(
            reg,
            "get_plans",
            parse_id(project_id, "project"),
            key="plans",
            shape=lambda p: sh.pick(p, sh.PLAN_FIELDS),
            params=params,
            limit=limit,
            offset=offset,
            fetch_all=fetch_all,
            max_items=max_items,
        )

    @reg.read
    async def get_plan(plan_id: PlanId) -> dict:
        """Get a test plan with its entries and the runs inside each entry.

        Each entry has an id (a GUID, used by add_attachment_to_plan_entry) and runs;
        each run has its own run ID and config (e.g. browser/OS) for result tools.
        """
        plan = await client.get("get_plan", parse_id(plan_id, "plan"))
        out = sh.pick(plan, (*sh.PLAN_FIELDS, "description", "project_id"))
        run_fields = (*sh.RUN_FIELDS, "entry_id", "config_ids")
        out["entries"] = [
            {
                "id": e.get("id"),
                "name": e.get("name"),
                "suite_id": e.get("suite_id"),
                "runs": [sh.pick(r, run_fields) for r in e.get("runs") or []],
            }
            for e in plan.get("entries") or []
        ]
        return out

    @reg.read
    async def get_runs(
        project_id: ProjectId,
        is_completed: OptBool = None,
        milestone_id: Annotated[
            IdList, Field(description="Milestone ID(s), e.g. 2 or ['M2', 'M3'].")
        ] = None,
        suite_id: Annotated[
            IdList, Field(description="Suite ID(s), e.g. 3 or ['S3', 'S4'].")
        ] = None,
        created_after: DateFilter = None,
        created_before: DateFilter = None,
        created_by: Annotated[IdList, Field(description="Creator user ID(s).")] = None,
        limit: Limit = None,
        offset: Offset = 0,
        fetch_all: FetchAll = False,
        max_items: MaxItems = 1000,
    ) -> dict:
        """List test runs in a project (with pass/fail counts).

        Note: runs that are part of a test plan are NOT returned here; use get_plans + get_plan.
        Then use get_tests(run_id) to see the tests in a run.
        """
        params = {
            "is_completed": is_completed,
            "milestone_id": parse_id_list(milestone_id, "milestone"),
            "suite_id": parse_id_list(suite_id, "suite"),
            "created_after": to_timestamp(created_after),
            "created_before": to_timestamp(created_before),
            "created_by": parse_id_list(created_by, "user"),
        }
        return await list_page(
            reg,
            "get_runs",
            parse_id(project_id, "project"),
            key="runs",
            shape=lambda r: sh.pick(r, sh.RUN_FIELDS),
            params=params,
            limit=limit,
            offset=offset,
            fetch_all=fetch_all,
            max_items=max_items,
        )

    @reg.read
    async def get_run(run_id: RunId) -> dict:
        """Get one test run, including its status counts and whether it is completed (closed)."""
        return sh.full(await client.get("get_run", parse_id(run_id, "run")))

    # ------------------------------------------------------------------ tests

    @reg.read
    async def get_tests(
        run_id: RunId,
        status: StatusFilter = None,
        limit: Limit = None,
        offset: Offset = 0,
        fetch_all: FetchAll = False,
        max_items: MaxItems = 1000,
    ) -> dict:
        """List the tests in a run (each test = one case in that run) with current status.

        Use the returned test IDs with add_result/get_results, or the case_id with
        add_result_for_case. Filter by status, e.g. status=['Untested', 'Retest'].
        """
        statuses = await client.get_statuses() if status else []
        labels = await reg.status_labels()
        return await list_page(
            reg,
            "get_tests",
            parse_id(run_id, "run"),
            key="tests",
            shape=lambda t: sh.with_status(sh.pick(t, sh.TEST_FIELDS), labels),
            params={"status_id": resolve_status_filter(status, statuses)},
            limit=limit,
            offset=offset,
            fetch_all=fetch_all,
            max_items=max_items,
        )

    @reg.read
    async def get_test(test_id: TestId) -> dict:
        """Get one test (a case inside a run) with its current status and case details."""
        labels = await reg.status_labels()
        return sh.with_status(
            sh.full(await client.get("get_test", parse_id(test_id, "test"))), labels
        )

    # ------------------------------------------------------------------ results

    def _result_shape(labels: dict[int, str]) -> Callable[[dict[str, Any]], dict[str, Any]]:
        def shape(r: dict[str, Any]) -> dict[str, Any]:
            out = sh.pick(r, sh.RESULT_FIELDS, include_custom=True)
            return sh.truncate_comment(sh.with_status(out, labels))

        return shape

    DefectsFilter = Annotated[
        str | None, Field(description="Only results linked to this defect ID, e.g. 'JIRA-123'.")
    ]

    @reg.read
    async def get_results(
        test_id: TestId,
        status: StatusFilter = None,
        defects_filter: DefectsFilter = None,
        limit: Limit = None,
        offset: Offset = 0,
    ) -> dict:
        """List results (newest first) for a test in a run. Use get_tests to find test IDs."""
        statuses = await client.get_statuses() if status else []
        labels = await reg.status_labels()
        return await list_page(
            reg,
            "get_results",
            parse_id(test_id, "test"),
            key="results",
            shape=_result_shape(labels),
            params={
                "status_id": resolve_status_filter(status, statuses),
                "defects_filter": defects_filter,
            },
            limit=limit,
            offset=offset,
        )

    @reg.read
    async def get_results_for_case(
        run_id: RunId,
        case_id: CaseId,
        status: StatusFilter = None,
        defects_filter: DefectsFilter = None,
        limit: Limit = None,
        offset: Offset = 0,
    ) -> dict:
        """List results (newest first) for a case within a specific run."""
        statuses = await client.get_statuses() if status else []
        labels = await reg.status_labels()
        return await list_page(
            reg,
            "get_results_for_case",
            parse_id(run_id, "run"),
            parse_id(case_id, "case"),
            key="results",
            shape=_result_shape(labels),
            params={
                "status_id": resolve_status_filter(status, statuses),
                "defects_filter": defects_filter,
            },
            limit=limit,
            offset=offset,
        )

    @reg.read
    async def get_results_for_run(
        run_id: RunId,
        status: StatusFilter = None,
        created_after: DateFilter = None,
        created_before: DateFilter = None,
        created_by: Annotated[IdList, Field(description="Creator user ID(s).")] = None,
        defects_filter: DefectsFilter = None,
        limit: Limit = None,
        offset: Offset = 0,
        fetch_all: FetchAll = False,
        max_items: MaxItems = 1000,
    ) -> dict:
        """List all results added in a run (newest first). Comments are truncated in lists;
        use get_results(test_id) for a single test's full history."""
        statuses = await client.get_statuses() if status else []
        labels = await reg.status_labels()
        params = {
            "status_id": resolve_status_filter(status, statuses),
            "created_after": to_timestamp(created_after),
            "created_before": to_timestamp(created_before),
            "created_by": parse_id_list(created_by, "user"),
            "defects_filter": defects_filter,
        }
        return await list_page(
            reg,
            "get_results_for_run",
            parse_id(run_id, "run"),
            key="results",
            shape=_result_shape(labels),
            params=params,
            limit=limit,
            offset=offset,
            fetch_all=fetch_all,
            max_items=max_items,
        )

    # ------------------------------------------------------------------ attachments

    @reg.read
    async def get_attachments_for_case(
        case_id: CaseId, limit: Limit = None, offset: Offset = 0
    ) -> dict:
        """List attachments on a test case (metadata only, not file contents)."""
        return await list_page(
            reg,
            "get_attachments_for_case",
            parse_id(case_id, "case"),
            key="attachments",
            shape=sh.attachment,
            limit=limit,
            offset=offset,
        )

    @reg.read
    async def get_attachments_for_test(test_id: TestId) -> dict:
        """List attachments on a test's results (metadata only). Use get_tests to find test IDs."""
        return await list_page(
            reg,
            "get_attachments_for_test",
            parse_id(test_id, "test"),
            key="attachments",
            shape=sh.attachment,
        )

    @reg.read
    async def get_attachments_for_run(
        run_id: RunId, limit: Limit = None, offset: Offset = 0
    ) -> dict:
        """List attachments on a test run (metadata only)."""
        return await list_page(
            reg,
            "get_attachments_for_run",
            parse_id(run_id, "run"),
            key="attachments",
            shape=sh.attachment,
            limit=limit,
            offset=offset,
        )

    @reg.read
    async def get_attachments_for_plan(
        plan_id: PlanId, limit: Limit = None, offset: Offset = 0
    ) -> dict:
        """List attachments on a test plan (metadata only)."""
        return await list_page(
            reg,
            "get_attachments_for_plan",
            parse_id(plan_id, "plan"),
            key="attachments",
            shape=sh.attachment,
            limit=limit,
            offset=offset,
        )

    # ------------------------------------------------------------------ users / configs

    @reg.read
    async def get_users(
        project_id: Annotated[
            int | str | None,
            Field(description="Optional project ID; required on TestRail Cloud for non-admins."),
        ] = None,
        limit: Limit = None,
        offset: Offset = 0,
        fetch_all: Annotated[
            bool,
            Field(
                description="If true, follow pagination and return all users up to max_items. "
                "Default false (one page); prefer get_user_by_email for a single person."
            ),
        ] = False,
        max_items: MaxItems = 1000,
    ) -> dict:
        """List users (id, name, email). Use a user's id as assigned_to_id in result tools.

        To find one person, get_user_by_email is cheaper.
        """
        pid = parse_optional_id(project_id, "project")
        return await list_page(
            reg,
            "get_users",
            *([pid] if pid else []),
            key="users",
            shape=lambda u: sh.pick(u, sh.USER_FIELDS),
            limit=limit,
            offset=offset,
            fetch_all=fetch_all,
            max_items=max_items,
        )

    @reg.read
    async def get_user_by_email(
        email: Annotated[str, Field(description="The user's email address.")],
    ) -> dict:
        """Look up a single user by email address (to get the ID for assigned_to_id)."""
        cleaned = email.strip()
        if not _EMAIL_RE.fullmatch(cleaned) or len(cleaned) > 254:
            raise TestRailError(f"{email!r} is not an email address.")
        return sh.pick(
            await client.get("get_user_by_email", params={"email": cleaned}), sh.USER_FIELDS
        )

    @reg.read
    async def get_configs(project_id: ProjectId) -> dict:
        """List configuration groups and options (e.g. Browsers: Chrome, Firefox) of a project.

        Runs inside test plans can be per-configuration; get_plan shows each run's config.
        """
        data = await client.get("get_configs", parse_id(project_id, "project")) or []
        return {
            "config_groups": [
                {
                    "id": g.get("id"),
                    "name": g.get("name"),
                    "configs": [
                        {"id": c.get("id"), "name": c.get("name")} for c in g.get("configs") or []
                    ],
                }
                for g in data
            ]
        }
