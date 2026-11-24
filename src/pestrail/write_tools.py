"""Write MCP tools: add results and attachments. Not registered in read-only mode.

Nothing here deletes, edits or closes anything; every call only *adds* data.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from . import shaping as sh
from .attachments import MAX_ATTACHMENT_BYTES, validate_attachment_path
from .client import TestRailError
from .ids import parse_id, parse_optional_id, resolve_status
from .read_tools import CaseId, PlanId, RunId, TestId
from .scope import enforce_write_scope

if TYPE_CHECKING:
    from .server import Registry

MAX_BULK_RESULTS = 500
MAX_FILES_PER_CALL = 20
#: How many result_id -> run_id pairs to remember for scope checks on later attachments.
MAX_REMEMBERED_RESULTS = 10_000
MAX_COMMENT_CHARS = 20_000
MAX_VERSION_CHARS = 250
MAX_ELAPSED_CHARS = 64
MAX_DEFECTS_CHARS = 2_000
MAX_CUSTOM_FIELDS = 50
MAX_CUSTOM_KEY_CHARS = 64
MAX_CUSTOM_VALUE_CHARS = 10_000

# --------------------------------------------------------------------------- parameter types

Status = Annotated[
    int | str | None,
    Field(
        description="Status ID or name, e.g. 'Passed', 'Failed', 'Blocked', 'Retest', 5. "
        "Not 'Untested'. Omit for a comment-only result."
    ),
]
Comment = Annotated[
    str | None,
    Field(max_length=MAX_COMMENT_CHARS, description="Result comment (Markdown works)."),
]
Elapsed = Annotated[
    str | None,
    Field(max_length=MAX_ELAPSED_CHARS, description="Time spent, e.g. '30s', '1m 30s', '2h'."),
]
Defects = Annotated[
    str | list[str] | None,
    Field(
        description="Defect ID(s), e.g. 'JIRA-123' or ['JIRA-1', 'JIRA-2'] "
        f"(max {MAX_DEFECTS_CHARS} chars joined)."
    ),
]
Version = Annotated[
    str | None,
    Field(max_length=MAX_VERSION_CHARS, description="Version or build tested, e.g. '2.4.1'."),
]
AssignedTo = Annotated[
    int | None, Field(description="User ID to assign the test to (see get_user_by_email).")
]
CustomFields = Annotated[
    dict[str, Any] | None,
    Field(
        description="Custom fields by system name, e.g. {'custom_env': 'staging'}. Keys start "
        f"with 'custom_'; max {MAX_CUSTOM_FIELDS} fields of {MAX_CUSTOM_VALUE_CHARS} chars. "
        "See get_result_fields."
    ),
]
FilePath = Annotated[
    str,
    Field(
        description="Local file (max 256 MB) inside TESTRAIL_ATTACHMENT_ROOTS; relative paths "
        "are resolved against those roots."
    ),
]
DryRun = Annotated[
    bool,
    Field(description="Run every check and return the exact request, but send nothing."),
]

#: Added to every dry-run response.
DRY_RUN_NOTE = "Dry run: nothing was sent to TestRail. Call again without dry_run to do it."


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


def _reject_if_too_long(value: str, name: str, limit: int) -> str:
    if len(value) > limit:
        raise TestRailError(f"{name} exceeds {limit} characters ({len(value)} given).")
    return value


def _custom_value_chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError) as exc:
        kind = type(value).__name__
        raise TestRailError(f"Custom field value could not be measured: {kind}.") from exc


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
    """Build a TestRail result payload, validating status, sizes and custom field keys."""
    body: dict[str, Any] = {}
    if status is not None and status != "":
        body["status_id"] = resolve_status(status, statuses)
    if comment:
        body["comment"] = _reject_if_too_long(comment, "comment", MAX_COMMENT_CHARS)
    if elapsed and elapsed.strip():
        body["elapsed"] = _reject_if_too_long(elapsed.strip(), "elapsed", MAX_ELAPSED_CHARS)
    if defects:
        items = [defects] if isinstance(defects, str) else list(defects)
        joined = ",".join(d.strip() for d in items if d and d.strip())
        if joined:
            body["defects"] = _reject_if_too_long(joined, "defects", MAX_DEFECTS_CHARS)
    if version:
        body["version"] = _reject_if_too_long(version, "version", MAX_VERSION_CHARS)
    if assigned_to_id is not None:
        if assigned_to_id <= 0:
            raise TestRailError("assigned_to_id must be a positive user ID.")
        body["assignedto_id"] = assigned_to_id
    fields = custom_fields or {}
    if len(fields) > MAX_CUSTOM_FIELDS:
        raise TestRailError(f"At most {MAX_CUSTOM_FIELDS} custom fields are allowed.")
    for key, value in fields.items():
        if not key.startswith("custom_") or key == "custom_":
            raise TestRailError(
                f"Custom field key {key!r} must be a system name starting with 'custom_' "
                "(see get_result_fields)."
            )
        if len(key) > MAX_CUSTOM_KEY_CHARS:
            raise TestRailError(
                f"Custom field key {key!r} exceeds {MAX_CUSTOM_KEY_CHARS} characters."
            )
        size = _custom_value_chars(value)
        if size > MAX_CUSTOM_VALUE_CHARS:
            raise TestRailError(
                f"Custom field {key!r} exceeds {MAX_CUSTOM_VALUE_CHARS} characters ({size} given)."
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


def register(reg: Registry) -> None:
    client = reg.client
    roots = reg.config.attachment_roots
    allow_any_path = reg.config.allow_any_attachment_path

    async def _statuses_if(status: Any) -> list[dict[str, Any]]:
        if status is None or status == "":
            return []
        # Numeric IDs still get validated against the instance's list when available.
        return await client.get_statuses()

    async def _check_file(path: str) -> Path:
        return await asyncio.to_thread(
            validate_attachment_path,
            path,
            roots,
            max_bytes=MAX_ATTACHMENT_BYTES,
            allow_any_path=allow_any_path,
        )

    # result_id -> run_id for results this server created. TestRail has no single-result
    # lookup, so this is how add_attachment_to_result can be scope-checked without extra
    # input in the common "add a result, then attach a log" flow. Bounded, oldest dropped.
    known_result_runs: dict[int, int] = {}

    def _remember(result: Any, run: int | None) -> None:
        if run is None or not isinstance(result, dict):
            return
        result_id = result.get("id")
        if isinstance(result_id, int) and result_id > 0:
            known_result_runs[result_id] = run
            while len(known_result_runs) > MAX_REMEMBERED_RESULTS:
                known_result_runs.pop(next(iter(known_result_runs)))

    async def _scope(**kwargs: Any) -> int | None:
        return await enforce_write_scope(
            client, reg.config, known_result_runs=known_result_runs, **kwargs
        )

    async def _upload(method: str, *ids: int | str, file_path: str) -> dict[str, Any]:
        resolved = await _check_file(file_path)
        data = await client.upload(method, *ids, file_path=resolved)
        size_bytes = await asyncio.to_thread(lambda: resolved.stat().st_size)
        return {
            "attachment_id": (data or {}).get("attachment_id"),
            "file": resolved.name,
            "size_bytes": size_bytes,
        }

    # ------------------------------------------------------------------ dry run
    # A dry run makes every check a real call makes (IDs, write allowlists and their read
    # lookups, statuses, payload limits, attachment rules, and the client's own method/ID
    # validation via build_url), then describes the request instead of calling post/upload.

    def _request(method: str, *ids: int | str, body: Any = None) -> dict[str, Any]:
        client.build_url(method, ids)  # same allowlist and ID checks as a real request
        out: dict[str, Any] = {
            "http": "POST",
            "endpoint": "/".join([method, *(str(i) for i in ids)]),
        }
        if body is not None:
            out["body"] = body
        return out

    async def _file_info(path: str) -> dict[str, Any]:
        resolved = await _check_file(path)
        try:
            size_bytes = await asyncio.to_thread(lambda: resolved.stat().st_size)
        except OSError as exc:
            raise TestRailError(f"Cannot read file {path}: {type(exc).__name__}") from exc
        return {"file": resolved.name, "path": str(resolved), "size_bytes": size_bytes}

    def _dry_run(*requests: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {"dry_run": True, "requests": list(requests), **extra, "note": DRY_RUN_NOTE}

    async def _upload_or_preview(
        method: str, *ids: int | str, file_path: str, dry_run: bool
    ) -> dict[str, Any]:
        if dry_run:
            info = await _file_info(file_path)
            return _dry_run({**_request(method, *ids), "file": info})
        return await _upload(method, *ids, file_path=file_path)

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
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        """Add a new result to a test (a case inside a run), identified by test ID.

        If you only know the run and case IDs, use add_result_for_case. Fails if the run is
        closed.
        """
        tid = parse_id(test_id, "test")
        run = await _scope(test_id=tid)
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
        if dry_run:
            return _dry_run(_request("add_result", tid, body=body))
        result = await client.post("add_result", tid, body=body)
        _remember(result, run)
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
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        """Add a new result for a case in a run, e.g. "mark C1234 in R56 as Failed".

        The case must be in the run (see get_tests); fails if the run is closed. To attach
        files in the same step, use add_result_with_attachments.
        """
        rid, cid = parse_id(run_id, "run"), parse_id(case_id, "case")
        await _scope(run_id=rid)
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
        if dry_run:
            return _dry_run(_request("add_result_for_case", rid, cid, body=body))
        result = await client.post("add_result_for_case", rid, cid, body=body)
        _remember(result, rid)
        out = _result_summary(result or {}, await reg.status_labels())
        out["case_id"] = cid
        return out

    @reg.write
    async def add_results_for_cases(
        run_id: RunId,
        results: Annotated[
            list[CaseResult],
            Field(
                min_length=1,
                max_length=MAX_BULK_RESULTS,
                description="One entry per case.",
            ),
        ],
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        """Add results for many cases in one run in one request: the main tool for
        reporting a test session.

        All entries are validated before anything is sent; TestRail then adds all or none.
        Every case must already be in the run (see get_tests).
        """
        rid = parse_id(run_id, "run")
        await _scope(run_id=rid)
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
        if dry_run:
            return _dry_run(
                _request("add_results_for_cases", rid, body={"results": payload}),
                count=len(payload),
            )

        created = await client.post("add_results_for_cases", rid, body={"results": payload})
        # From here on TestRail has accepted the request: the results exist. Any error must
        # say so, or the assistant may "retry" and add every result a second time.
        do_not_retry = (
            f"TestRail accepted the request, so the {len(payload)} results WERE added to run "
            f"{rid}. Do NOT retry this call (it would add duplicates); check them with "
            "get_results_for_run instead."
        )
        if not isinstance(created, list):
            raise TestRailError(
                "Unexpected response from add_results_for_cases (expected a list of results). "
                + do_not_retry
            )
        if len(created) != len(payload):
            raise TestRailError(
                f"TestRail returned {len(created)} results for {len(payload)} entries sent. "
                + do_not_retry
            )
        labels = await reg.status_labels()
        summaries = []
        for sent, result in zip(payload, created, strict=True):
            _remember(result, rid)
            summary = _result_summary(result, labels)
            summary["case_id"] = sent["case_id"]
            summaries.append(summary)
        return {"run_id": rid, "added": len(summaries), "results": summaries}

    # ------------------------------------------------------------------ attachments

    @reg.write
    async def add_attachment_to_result(
        result_id: Annotated[
            int,
            Field(gt=0, description="Result ID (from add_result* or get_results)."),
        ],
        file_path: FilePath,
        test_id: Annotated[
            int | str | None,
            Field(
                description="Test the result belongs to, e.g. 'T678'. Needed only with write "
                "allowlists, for a result not added in this session (it is verified)."
            ),
        ] = None,
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        """Upload a local file (log, screenshot, report) and attach it to an existing result.

        Result IDs come from the add_result* tools or get_results / get_results_for_run.
        """
        await _scope(result_id=result_id, test_id=parse_optional_id(test_id, "test"))
        return await _upload_or_preview(
            "add_attachment_to_result", result_id, file_path=file_path, dry_run=dry_run
        )

    @reg.write
    async def add_attachment_to_run(
        run_id: RunId, file_path: FilePath, dry_run: DryRun = False
    ) -> dict[str, Any]:
        """Upload a local file and attach it to a test run (e.g. a full test report)."""
        rid = parse_id(run_id, "run")
        await _scope(run_id=rid)
        return await _upload_or_preview(
            "add_attachment_to_run", rid, file_path=file_path, dry_run=dry_run
        )

    @reg.write
    async def add_attachment_to_case(
        case_id: CaseId, file_path: FilePath, dry_run: DryRun = False
    ) -> dict[str, Any]:
        """Upload a local file and attach it to a test case. This does not change the case's
        fields; it only adds an attachment."""
        cid = parse_id(case_id, "case")
        await _scope(case_id=cid)
        return await _upload_or_preview(
            "add_attachment_to_case", cid, file_path=file_path, dry_run=dry_run
        )

    @reg.write
    async def add_attachment_to_plan(
        plan_id: PlanId, file_path: FilePath, dry_run: DryRun = False
    ) -> dict[str, Any]:
        """Upload a local file and attach it to a test plan."""
        pid = parse_id(plan_id, "plan")
        await _scope(plan_id=pid)
        return await _upload_or_preview(
            "add_attachment_to_plan", pid, file_path=file_path, dry_run=dry_run
        )

    @reg.write
    async def add_attachment_to_plan_entry(
        plan_id: PlanId,
        entry_id: Annotated[
            str,
            Field(description="Plan entry ID (a GUID like '3933d74b-...'); see get_plan entries."),
        ],
        file_path: FilePath,
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        """Upload a local file and attach it to an entry (group of runs) in a test plan."""
        pid = parse_id(plan_id, "plan")
        await _scope(plan_id=pid)
        return await _upload_or_preview(
            "add_attachment_to_plan_entry",
            pid,
            entry_id.strip(),
            file_path=file_path,
            dry_run=dry_run,
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
                description="Local files inside TESTRAIL_ATTACHMENT_ROOTS (max 256 MB each).",
            ),
        ],
        status: Status = None,
        comment: Comment = None,
        elapsed: Elapsed = None,
        defects: Defects = None,
        version: Version = None,
        assigned_to_id: AssignedTo = None,
        custom_fields: CustomFields = None,
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        """Add a result for a case in a run, then upload files to it.

        All files are checked before the result is created, so a bad path creates nothing.
        If an upload fails afterwards, the result is kept and the response lists which
        uploads failed; retry them with add_attachment_to_result.
        """
        rid, cid = parse_id(run_id, "run"), parse_id(case_id, "case")
        await _scope(run_id=rid)
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
        files = []
        for path in file_paths:
            try:
                if dry_run:
                    files.append(await _file_info(path))
                else:
                    await _check_file(path)  # unchanged real-call check
            except TestRailError as exc:
                problems.append(str(exc))
        if problems:
            raise TestRailError("No result was added. File problems:\n" + "\n".join(problems))
        if dry_run:
            # The uploads go to the new result, whose ID only exists after the first request.
            client.build_url("add_attachment_to_result", (0,))  # method allowlist check
            uploads = [
                {"http": "POST", "endpoint": "add_attachment_to_result/<new result id>", "file": f}
                for f in files
            ]
            return _dry_run(_request("add_result_for_case", rid, cid, body=body), *uploads)

        result = await client.post("add_result_for_case", rid, cid, body=body) or {}
        result_id = result.get("id")
        _remember(result, rid)
        if not isinstance(result_id, int) or result_id <= 0:
            raise TestRailError(
                "Result was created but TestRail returned no usable result id; "
                "attachments were not uploaded. Inspect the run manually."
            )
        out = _result_summary(result, await reg.status_labels())
        out = {"result_id": result_id, "case_id": cid, "run_id": rid, **out}
        out.pop("id", None)

        uploaded, failed = [], []
        for path in file_paths:
            try:
                uploaded.append(
                    await _upload("add_attachment_to_result", result_id, file_path=path)
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
