"""Write MCP tools: add results and attachments. Not registered in read-only mode.

Nothing here deletes, edits or closes anything; every call only *adds* data.
"""

from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from . import shaping as sh
from .attachments import validate_attachment_path
from .client import TestRailError
from .ids import parse_id, resolve_status
from .read_tools import CaseId, PlanId, RunId, TestId

MAX_BULK_RESULTS = 500
MAX_FILES_PER_CALL = 20

# --------------------------------------------------------------------------- parameter types

Status = Annotated[
    int | str | None,
    Field(
        description="Result status: ID or name, e.g. 'Passed', 'Failed', 'Blocked', 'Retest', 5. "
        "Custom statuses work too (see get_statuses). 'Untested' is not allowed. "
        "Omit to add a comment-only result."
    ),
]
Comment = Annotated[
    str | None, Field(description="Result comment. Markdown is supported by TestRail.")
]
Elapsed = Annotated[
    str | None,
    Field(description="Time spent, TestRail timespan format, e.g. '30s', '1m 30s', '2h'."),
]
Defects = Annotated[
    str | list[str] | None,
    Field(description="Defect ID(s) to link, e.g. 'JIRA-123' or ['JIRA-1', 'JIRA-2']."),
]
Version = Annotated[
    str | None, Field(description="Version or build tested, e.g. '2.4.1' or a commit SHA.")
]
AssignedTo = Annotated[
    int | None,
    Field(description="User ID to assign the test to (see get_users / get_user_by_email)."),
]
CustomFields = Annotated[
    dict[str, Any] | None,
    Field(
        description="Custom result fields keyed by system name, e.g. "
        "{'custom_environment': 'staging'}. Keys must start with 'custom_'. "
        "Discover them with get_result_fields."
    ),
]
FilePath = Annotated[
    str,
    Field(
        description="Path to a local file on the machine running this server "
        "(max 256 MB; may be restricted to TESTRAIL_ATTACHMENT_ROOTS)."
    ),
]


class CaseResult(BaseModel):
    """One result in a bulk add_results_for_cases call."""

    model_config = ConfigDict(extra="forbid")

    case_id: CaseId
    status: Status = None
    comment: Comment = None
    elapsed: Elapsed = None
    defects: Defects = None
    version: Version = None
    assigned_to_id: AssignedTo = None
    custom_fields: CustomFields = None


def build_result_body(
    *,
    status: int | str | None,
    comment: str | None,
    elapsed: str | None,
    defects: str | list[str] | None,
    version: str | None,
    assigned_to_id: int | None,
    custom_fields: dict[str, Any] | None,
    statuses: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a TestRail result payload, validating status and custom field keys."""
    body: dict[str, Any] = {}
    if status is not None and status != "":
        body["status_id"] = resolve_status(status, statuses)
    if comment:
        body["comment"] = comment
    if elapsed and elapsed.strip():
        body["elapsed"] = elapsed.strip()
    if defects:
        items = [defects] if isinstance(defects, str) else list(defects)
        joined = ",".join(d.strip() for d in items if d and d.strip())
        if joined:
            body["defects"] = joined
    if version:
        body["version"] = version
    if assigned_to_id is not None:
        if assigned_to_id <= 0:
            raise TestRailError("assigned_to_id must be a positive user ID.")
        body["assignedto_id"] = assigned_to_id
    for key, value in (custom_fields or {}).items():
        if not key.startswith("custom_") or key == "custom_":
            raise TestRailError(
                f"Custom field key {key!r} must be a system name starting with 'custom_' "
                "(see get_result_fields)."
            )
        body[key] = value
    if not body:
        raise TestRailError(
            "Nothing to add: provide at least a status or a comment (or elapsed/defects/"
            "version/custom_fields)."
        )
    return body


def _result_summary(result: dict[str, Any], labels: dict[int, str]) -> dict[str, Any]:
    return sh.with_status(
        sh.pick(result, ("id", "test_id", "status_id", "created_on", "version", "elapsed")),
        labels,
    )


def register(reg: Any) -> None:
    client = reg.client
    roots = reg.config.attachment_roots

    async def _statuses_if(status: Any) -> list[dict[str, Any]]:
        if status is None or status == "":
            return []
        # Numeric IDs still get validated against the instance's list when available.
        return await client.get_statuses()

    def _check_file(path: str) -> Path:
        return validate_attachment_path(path, roots)

    async def _upload(method: str, *ids: int | str, file_path: str) -> dict[str, Any]:
        resolved = _check_file(file_path)
        data = await client.upload(method, *ids, file_path=resolved)
        return {
            "attachment_id": (data or {}).get("attachment_id"),
            "file": resolved.name,
            "size_bytes": resolved.stat().st_size,
        }

    # ------------------------------------------------------------------ results

    @reg.write
    async def add_result(
        test_id: TestId,
        status: Status = None,
        comment: Comment = None,
        elapsed: Elapsed = None,
        defects: Defects = None,
        version: Version = None,
        assigned_to_id: AssignedTo = None,
        custom_fields: CustomFields = None,
    ) -> dict:
        """Add a new result to a test (a case inside a run), identified by test ID.

        Use get_tests(run_id) to find test IDs. If you only know the run and case IDs,
        use add_result_for_case instead. Results are appended to the history and cannot be
        removed through this server, so double-check the IDs. Fails if the run is closed.
        """
        body = build_result_body(
            status=status,
            comment=comment,
            elapsed=elapsed,
            defects=defects,
            version=version,
            assigned_to_id=assigned_to_id,
            custom_fields=custom_fields,
            statuses=await _statuses_if(status),
        )
        result = await client.post("add_result", parse_id(test_id, "test"), body=body)
        return _result_summary(result or {}, await reg.status_labels())

    @reg.write
    async def add_result_for_case(
        run_id: RunId,
        case_id: CaseId,
        status: Status = None,
        comment: Comment = None,
        elapsed: Elapsed = None,
        defects: Defects = None,
        version: Version = None,
        assigned_to_id: AssignedTo = None,
        custom_fields: CustomFields = None,
    ) -> dict:
        """Add a new result for a case in a run, e.g. "mark C1234 in R56 as Failed".

        The case must already be part of the run (check with get_tests). Results are
        appended and cannot be removed through this server. Fails if the run is closed.
        To attach logs/screenshots in the same step, use add_result_with_attachments.
        """
        body = build_result_body(
            status=status,
            comment=comment,
            elapsed=elapsed,
            defects=defects,
            version=version,
            assigned_to_id=assigned_to_id,
            custom_fields=custom_fields,
            statuses=await _statuses_if(status),
        )
        result = await client.post(
            "add_result_for_case", parse_id(run_id, "run"), parse_id(case_id, "case"), body=body
        )
        out = _result_summary(result or {}, await reg.status_labels())
        out["case_id"] = parse_id(case_id, "case")
        return out

    @reg.write
    async def add_results_for_cases(
        run_id: RunId,
        results: Annotated[
            list[CaseResult],
            Field(
                min_length=1,
                max_length=MAX_BULK_RESULTS,
                description="One entry per result: {case_id, status, comment, elapsed, "
                "defects, version, assigned_to_id, custom_fields}.",
            ),
        ],
    ) -> dict:
        """Add results for many cases in one run in a single request: the main tool for
        reporting a test session.

        All entries are validated (IDs, statuses, custom field keys) before anything is sent;
        TestRail then adds them all or rejects the whole request. Every case must already be
        in the run (see get_tests). Results cannot be removed through this server.
        """
        rid = parse_id(run_id, "run")
        need_statuses = any(r.status not in (None, "") for r in results)
        statuses = await client.get_statuses() if need_statuses else []
        payload = []
        errors = []
        for index, entry in enumerate(results):
            try:
                body = build_result_body(
                    status=entry.status,
                    comment=entry.comment,
                    elapsed=entry.elapsed,
                    defects=entry.defects,
                    version=entry.version,
                    assigned_to_id=entry.assigned_to_id,
                    custom_fields=entry.custom_fields,
                    statuses=statuses,
                )
                payload.append({"case_id": parse_id(entry.case_id, "case"), **body})
            except TestRailError as exc:
                errors.append(f"results[{index}] (case {entry.case_id}): {exc}")
        if errors:
            raise TestRailError("Nothing was sent. Fix these entries:\n" + "\n".join(errors))

        created = await client.post("add_results_for_cases", rid, body={"results": payload})
        labels = await reg.status_labels()
        summaries = []
        for sent, result in zip(payload, created or [], strict=False):
            summary = _result_summary(result, labels)
            summary["case_id"] = sent["case_id"]
            summaries.append(summary)
        return {"run_id": rid, "added": len(summaries), "results": summaries}

    # ------------------------------------------------------------------ attachments

    @reg.write
    async def add_attachment_to_result(
        result_id: Annotated[
            int,
            Field(
                gt=0, description="Result ID, as returned by the add_result* tools or get_results."
            ),
        ],
        file_path: FilePath,
    ) -> dict:
        """Upload a local file (log, screenshot, report) and attach it to an existing result."""
        return await _upload("add_attachment_to_result", result_id, file_path=file_path)

    @reg.write
    async def add_attachment_to_run(run_id: RunId, file_path: FilePath) -> dict:
        """Upload a local file and attach it to a test run (e.g. a full test report)."""
        return await _upload("add_attachment_to_run", parse_id(run_id, "run"), file_path=file_path)

    @reg.write
    async def add_attachment_to_case(case_id: CaseId, file_path: FilePath) -> dict:
        """Upload a local file and attach it to a test case. This does not change the case's
        fields; it only adds an attachment."""
        return await _upload(
            "add_attachment_to_case", parse_id(case_id, "case"), file_path=file_path
        )

    @reg.write
    async def add_attachment_to_plan(plan_id: PlanId, file_path: FilePath) -> dict:
        """Upload a local file and attach it to a test plan."""
        return await _upload(
            "add_attachment_to_plan", parse_id(plan_id, "plan"), file_path=file_path
        )

    @reg.write
    async def add_attachment_to_plan_entry(
        plan_id: PlanId,
        entry_id: Annotated[
            str,
            Field(description="Plan entry ID (a GUID like '3933d74b-...'); see get_plan entries."),
        ],
        file_path: FilePath,
    ) -> dict:
        """Upload a local file and attach it to an entry (group of runs) in a test plan."""
        return await _upload(
            "add_attachment_to_plan_entry",
            parse_id(plan_id, "plan"),
            entry_id.strip(),
            file_path=file_path,
        )

    # ------------------------------------------------------------------ convenience

    @reg.write
    async def add_result_with_attachments(
        run_id: RunId,
        case_id: CaseId,
        file_paths: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=MAX_FILES_PER_CALL,
                description="Local files to attach to the new result (logs, screenshots...).",
            ),
        ],
        status: Status = None,
        comment: Comment = None,
        elapsed: Elapsed = None,
        defects: Defects = None,
        version: Version = None,
        assigned_to_id: AssignedTo = None,
        custom_fields: CustomFields = None,
    ) -> dict:
        """Add a result for a case in a run, then upload files to that new result.

        All files are checked (exist, allowed directory, size) before the result is created,
        so a bad path creates nothing. If an upload fails after the result was created, the
        result is kept (nothing can be deleted) and the response lists exactly which
        uploads succeeded and which failed; retry failures with add_attachment_to_result.
        """
        rid, cid = parse_id(run_id, "run"), parse_id(case_id, "case")
        body = build_result_body(
            status=status,
            comment=comment,
            elapsed=elapsed,
            defects=defects,
            version=version,
            assigned_to_id=assigned_to_id,
            custom_fields=custom_fields,
            statuses=await _statuses_if(status),
        )

        problems = []
        for path in file_paths:
            try:
                _check_file(path)
            except TestRailError as exc:
                problems.append(str(exc))
        if problems:
            raise TestRailError("No result was added. File problems:\n" + "\n".join(problems))

        result = await client.post("add_result_for_case", rid, cid, body=body) or {}
        result_id = result.get("id")
        out = _result_summary(result, await reg.status_labels())
        out = {"result_id": result_id, "case_id": cid, "run_id": rid, **out}
        out.pop("id", None)

        uploaded, failed = [], []
        for path in file_paths:
            try:
                uploaded.append(
                    await _upload("add_attachment_to_result", int(result_id), file_path=path)
                )
            except (TestRailError, OSError, TypeError, ValueError) as exc:
                failed.append({"file": path, "error": str(exc) or type(exc).__name__})

        out["attachments"] = uploaded
        out["failed_attachments"] = failed
        if failed:
            out["outcome"] = "partial"
            out["message"] = (
                f"Result {result_id} was created, {len(uploaded)} of {len(file_paths)} files "
                "were attached. Retry the failed files with add_attachment_to_result."
            )
        else:
            out["outcome"] = "complete"
        return out
