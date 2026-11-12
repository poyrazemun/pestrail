"""Optional project/run allowlists for write tools (confused-deputy guardrails)."""

from __future__ import annotations

from typing import Any, Protocol

from .client import TestRailError
from .config import Config


class _Client(Protocol):
    async def get(self, method: str, *path_args: Any, params: Any = None) -> Any: ...


def _positive_int(value: Any, label: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise TestRailError(f"Could not read {label} from TestRail: {value!r}.") from exc
    if number <= 0:
        raise TestRailError(f"Could not read {label} from TestRail: {value!r}.")
    return number


async def enforce_write_scope(
    client: _Client,
    config: Config,
    *,
    run_id: int | None = None,
    project_id: int | None = None,
    test_id: int | None = None,
    case_id: int | None = None,
    plan_id: int | None = None,
    result_id: int | None = None,
) -> None:
    """Refuse a write when it falls outside configured project/run allowlists.

    Empty allowlists mean no extra restriction (TestRail permissions still apply).
    When both lists are set, a run-scoped write must satisfy both.
    """
    allowed_runs = config.allowed_run_ids
    allowed_projects = config.allowed_project_ids
    if not allowed_runs and not allowed_projects:
        return

    resolved_run = run_id
    resolved_project = project_id

    if result_id is not None and (
        resolved_run is None or (allowed_projects and resolved_project is None)
    ):
        result = await client.get("get_result", result_id)
        if not isinstance(result, dict):
            raise TestRailError(f"Unexpected get_result response for result {result_id}.")
        if test_id is None:
            test_id = _positive_int(result.get("test_id"), "test_id")

    if test_id is not None and (
        resolved_run is None or (allowed_projects and resolved_project is None)
    ):
        test = await client.get("get_test", test_id)
        if not isinstance(test, dict):
            raise TestRailError(f"Unexpected get_test response for test {test_id}.")
        if resolved_run is None:
            resolved_run = _positive_int(test.get("run_id"), "run_id")

    if resolved_run is not None and allowed_projects and resolved_project is None:
        run = await client.get("get_run", resolved_run)
        if not isinstance(run, dict):
            raise TestRailError(f"Unexpected get_run response for run {resolved_run}.")
        resolved_project = _positive_int(run.get("project_id"), "project_id")

    if case_id is not None and allowed_projects and resolved_project is None:
        case = await client.get("get_case", case_id)
        if not isinstance(case, dict):
            raise TestRailError(f"Unexpected get_case response for case {case_id}.")
        resolved_project = _positive_int(case.get("project_id"), "project_id")

    if plan_id is not None and allowed_projects and resolved_project is None:
        plan = await client.get("get_plan", plan_id)
        if not isinstance(plan, dict):
            raise TestRailError(f"Unexpected get_plan response for plan {plan_id}.")
        resolved_project = _positive_int(plan.get("project_id"), "project_id")

    if allowed_runs:
        if resolved_run is None:
            raise TestRailError(
                "TESTRAIL_ALLOWED_RUN_IDS is set, but this write is not tied to a test run. "
                "Use a run/result/test-scoped tool, or also set TESTRAIL_ALLOWED_PROJECT_IDS "
                "for case/plan uploads."
            )
        if resolved_run not in allowed_runs:
            allowed = ", ".join(str(i) for i in sorted(allowed_runs))
            raise TestRailError(
                f"Run {resolved_run} is outside TESTRAIL_ALLOWED_RUN_IDS ({allowed})."
            )

    if allowed_projects:
        if resolved_project is None:
            raise TestRailError(
                "TESTRAIL_ALLOWED_PROJECT_IDS is set, but the project for this write "
                "could not be determined."
            )
        if resolved_project not in allowed_projects:
            allowed = ", ".join(str(i) for i in sorted(allowed_projects))
            raise TestRailError(
                f"Project {resolved_project} is outside TESTRAIL_ALLOWED_PROJECT_IDS ({allowed})."
            )
