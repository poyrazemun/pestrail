"""Optional project/run allowlists for write tools (confused-deputy guardrails).

Each write must be traced to the run and/or project it touches, using only API methods
TestRail actually has (verified against a live instance):

* run      -> get_run      -> project_id
* test     -> get_test     -> run_id
* case     -> get_case     -> suite_id -> get_suite -> project_id   (cases have no project_id)
* plan     -> get_plan     -> project_id
* result   -> TestRail has NO single-result endpoint (get_result does not exist), so a
              result is resolved either from results pestrail created itself in this session
              (``known_result_runs``) or from a caller-supplied ``test_id`` that is verified
              to really own the result via get_results(test_id).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .client import TestRailError
from .config import Config

#: Upper bound on results scanned when verifying that a result belongs to a test.
MAX_RESULTS_SCANNED = 1000


class _Client(Protocol):
    async def get(self, method: str, *path_args: Any, params: Any = None) -> Any: ...

    async def get_all(
        self, method: str, *path_args: Any, key: str, params: Any = None, max_items: int = ...
    ) -> tuple[list[Any], bool]: ...


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


async def _get_dict(client: _Client, method: str, entity_id: int) -> dict[str, Any]:
    data = await client.get(method, entity_id)
    if not isinstance(data, dict):
        raise TestRailError(f"Unexpected {method} response for {entity_id}.")
    return data


async def _verify_result_in_test(client: _Client, result_id: int, test_id: int) -> None:
    results, truncated = await client.get_all(
        "get_results", test_id, key="results", max_items=MAX_RESULTS_SCANNED
    )
    if any(isinstance(r, dict) and r.get("id") == result_id for r in results):
        return
    if truncated:
        raise TestRailError(
            f"Could not confirm that result {result_id} belongs to test {test_id} "
            f"(the test has more than {MAX_RESULTS_SCANNED} results)."
        )
    raise TestRailError(
        f"Result {result_id} does not belong to test {test_id}. Use get_results(test_id) "
        "to find the right IDs."
    )


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
    known_result_runs: Mapping[int, int] | None = None,
) -> int | None:
    """Refuse a write when it falls outside configured project/run allowlists.

    Empty allowlists mean no extra restriction (TestRail permissions still apply) and no
    lookups are made. When both lists are set, a run-scoped write must satisfy both.

    Returns the run the write was resolved to (or ``run_id`` unchanged when no allowlist
    is configured), so callers can remember which run a newly created result belongs to.
    """
    allowed_runs = config.allowed_run_ids
    allowed_projects = config.allowed_project_ids
    if not allowed_runs and not allowed_projects:
        return run_id

    resolved_run = run_id
    resolved_project = project_id

    if result_id is not None and resolved_run is None:
        known_run = (known_result_runs or {}).get(result_id)
        if known_run is not None:
            resolved_run = known_run
        elif test_id is not None:
            await _verify_result_in_test(client, result_id, test_id)
        else:
            raise TestRailError(
                f"Cannot check result {result_id} against TESTRAIL_ALLOWED_RUN_IDS / "
                "TESTRAIL_ALLOWED_PROJECT_IDS: TestRail has no API to look up a single "
                "result. Pass the result's test_id (see get_tests / get_results), or attach "
                "to a result created by this server in the current session."
            )

    if test_id is not None and (
        resolved_run is None or (allowed_projects and resolved_project is None)
    ):
        test = await _get_dict(client, "get_test", test_id)
        if resolved_run is None:
            resolved_run = _positive_int(test.get("run_id"), "run_id")

    if resolved_run is not None and allowed_projects and resolved_project is None:
        run = await _get_dict(client, "get_run", resolved_run)
        resolved_project = _positive_int(run.get("project_id"), "project_id")

    if case_id is not None and allowed_projects and resolved_project is None:
        case = await _get_dict(client, "get_case", case_id)
        # get_case has no project_id (only suite_id); fall back to the suite's project.
        resolved_project = _positive_int(case.get("project_id"), "project_id")
        suite_id = _positive_int(case.get("suite_id"), "suite_id")
        if resolved_project is None and suite_id is not None:
            suite = await _get_dict(client, "get_suite", suite_id)
            resolved_project = _positive_int(suite.get("project_id"), "project_id")

    if plan_id is not None and allowed_projects and resolved_project is None:
        plan = await _get_dict(client, "get_plan", plan_id)
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

    return resolved_run
