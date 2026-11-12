"""Trim TestRail objects to compact, LLM-friendly shapes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

PROJECT_FIELDS = ("id", "name", "suite_mode", "is_completed", "url")
SUITE_FIELDS = ("id", "name", "description", "project_id", "is_master", "is_completed", "url")
SECTION_FIELDS = ("id", "name", "parent_id", "depth", "suite_id", "display_order")
CASE_FIELDS = (
    "id",
    "title",
    "section_id",
    "suite_id",
    "type_id",
    "priority_id",
    "milestone_id",
    "refs",
    "estimate",
    "template_id",
    "updated_on",
)
RUN_FIELDS = (
    "id",
    "name",
    "suite_id",
    "plan_id",
    "milestone_id",
    "config",
    "is_completed",
    "passed_count",
    "failed_count",
    "blocked_count",
    "retest_count",
    "untested_count",
    "assignedto_id",
    "created_on",
    "completed_on",
    "url",
)
PLAN_FIELDS = (
    "id",
    "name",
    "milestone_id",
    "is_completed",
    "passed_count",
    "failed_count",
    "blocked_count",
    "retest_count",
    "untested_count",
    "assignedto_id",
    "created_on",
    "completed_on",
    "url",
)
TEST_FIELDS = (
    "id",
    "case_id",
    "run_id",
    "title",
    "status_id",
    "assignedto_id",
    "priority_id",
    "type_id",
    "refs",
    "estimate",
)
RESULT_FIELDS = (
    "id",
    "test_id",
    "status_id",
    "comment",
    "elapsed",
    "defects",
    "version",
    "assignedto_id",
    "created_by",
    "created_on",
    "attachment_ids",
)
MILESTONE_FIELDS = (
    "id",
    "name",
    "description",
    "project_id",
    "parent_id",
    "is_started",
    "is_completed",
    "start_on",
    "due_on",
    "completed_on",
    "url",
)
USER_FIELDS = ("id", "name", "email", "is_active", "role_id", "role")
ATTACHMENT_FIELDS = (
    "id",
    "name",
    "filename",
    "size",
    "filetype",
    "is_image",
    "created_on",
    "user_id",
    "entity_type",
    "entity_id",
    "result_id",
    "case_id",
    "test_change_id",
)

TIMESTAMP_FIELDS = {"created_on", "updated_on", "completed_on", "due_on", "start_on"}
LIST_COMMENT_LIMIT = 1000


def iso(ts: Any) -> Any:
    """Convert a TestRail unix timestamp to an ISO-8601 UTC string; leave others alone."""
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return ts
    try:
        return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return ts


def pick(
    obj: Mapping[str, Any],
    fields: Iterable[str],
    *,
    include_custom: bool = False,
    drop_empty_custom: bool = True,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in fields:
        if key in obj:
            value = obj[key]
            out[key] = iso(value) if key in TIMESTAMP_FIELDS else value
    if include_custom:
        for key, value in obj.items():
            if key.startswith("custom_") and not (drop_empty_custom and value in (None, "", [])):
                out[key] = value
    return out


def with_status(obj: Mapping[str, Any], labels: Mapping[int, str]) -> dict[str, Any]:
    out = dict(obj)
    status_id = out.get("status_id")
    if status_id is not None and status_id in labels:
        out["status"] = labels[status_id]
    return out


def truncate_comment(obj: Mapping[str, Any], limit: int = LIST_COMMENT_LIMIT) -> dict[str, Any]:
    out = dict(obj)
    comment = out.get("comment")
    if isinstance(comment, str) and len(comment) > limit:
        out["comment"] = comment[:limit] + f"... [truncated, {len(comment)} chars total]"
    return out


def full(obj: Mapping[str, Any]) -> dict[str, Any]:
    """Return the object with timestamps converted, keeping all fields."""
    return {k: (iso(v) if k in TIMESTAMP_FIELDS else v) for k, v in obj.items()}


def attachment(obj: Mapping[str, Any]) -> dict[str, Any]:
    picked = pick(obj, ATTACHMENT_FIELDS)
    return picked or full(obj)
