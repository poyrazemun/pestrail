"""Trim TestRail objects to compact, LLM-friendly shapes."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from .serialize import to_json_text
from .text import html_to_markdown, looks_like_html

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
#: Case types and templates: what a filter needs (drops e.g. i18n_custom_id).
NAMED_FIELDS = ("id", "name", "is_default")
HISTORY_FIELDS = ("id", "type_id", "created_on", "user_id")
#: A history change without its "options" block (field settings, not the change).
CHANGE_FIELDS = ("field", "label", "old_value", "new_value", "old_text", "new_text")
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
#: Default length of a *cleaned* comment in results tools (comment_max_chars).
DEFAULT_COMMENT_CHARS = 2000

CommentFormat = Literal["markdown", "raw"]


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


def clean_text(value: Any) -> Any:
    """Convert rich-text HTML to Markdown; leave plain text, Markdown and non-strings alone."""
    if isinstance(value, str) and looks_like_html(value):
        return html_to_markdown(value)
    return value


def clean_custom(value: Any) -> Any:
    """clean_text for a custom field, including lists of step dicts.

    Separated steps (``custom_steps_separated``, ``custom_step_results``) are lists of
    ``{content, expected, additional_info, ...}`` whose texts are rich text as well.
    """
    if isinstance(value, list):
        return [
            {k: clean_text(v) for k, v in item.items()} if isinstance(item, dict) else item
            for item in value
        ]
    return clean_text(value)


def history_entry(fmt: CommentFormat = "markdown") -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Shape one get_history_for_case entry: who changed what, when; values made readable."""

    def shape(entry: Mapping[str, Any]) -> dict[str, Any]:
        out = pick(entry, HISTORY_FIELDS)
        changes = []
        for change in entry.get("changes") or []:
            if not isinstance(change, Mapping):
                continue
            picked = pick(change, CHANGE_FIELDS)
            if fmt == "markdown":
                picked = {k: clean_text(v) for k, v in picked.items()}
            changes.append({k: v for k, v in picked.items() if not _is_empty(v)})
        out["changes"] = changes
        comments = entry.get("comments")
        out["comments"] = clean_custom(comments) if fmt == "markdown" else comments
        return {k: v for k, v in out.items() if not _is_empty(v)}

    return shape


def detail(obj: Mapping[str, Any], *, fmt: CommentFormat = "markdown") -> dict[str, Any]:
    """A single case/test: all fields, rich-text custom fields readable, empty fields left out."""
    out = full(obj)
    if fmt == "markdown":
        out = {k: clean_custom(v) if k.startswith("custom_") else v for k, v in out.items()}
    return {k: v for k, v in out.items() if not _is_empty(v)}


#: Text fields are never cut below this many characters by fit_object.
MIN_FIELD_CHARS = 200


def _cap_text(value: Any, cap: int) -> Any:
    if isinstance(value, str) and len(value) > cap:
        return value[:cap] + f"... [truncated, {len(value)} chars total]"
    return value


def _cap_strings(obj: Mapping[str, Any], cap: int) -> dict[str, Any]:
    """Cut every string longer than ``cap``: top-level fields and fields of list items."""
    out: dict[str, Any] = {}
    for key, value in obj.items():
        if isinstance(value, list):
            out[key] = [
                {k: _cap_text(v, cap) for k, v in item.items()} if isinstance(item, dict) else item
                for item in value
            ]
        else:
            out[key] = _cap_text(value, cap)
    return out


def _longest_string(obj: Mapping[str, Any]) -> int:
    lengths = [len(v) for v in obj.values() if isinstance(v, str)]
    for value in obj.values():
        if isinstance(value, list):
            lengths += [
                len(v)
                for item in value
                if isinstance(item, dict)
                for v in item.values()
                if isinstance(v, str)
            ]
    return max(lengths, default=0)


def fit_object(obj: dict[str, Any], budget: int) -> tuple[dict[str, Any], bool]:
    """Keep one object within ``budget`` characters of output by cutting its longest texts.

    Finds the largest per-field length that fits (every longer text is cut to it, with a
    '[truncated, N chars total]' marker), so short fields stay whole. Returns the object
    and whether anything was cut. Never cuts below MIN_FIELD_CHARS; if the object still
    doesn't fit (e.g. it is large because of structure, not text), it is returned as is.
    """
    if len(to_json_text(obj)) <= budget:
        return obj, False
    low, high = MIN_FIELD_CHARS, _longest_string(obj)
    best: dict[str, Any] | None = None
    while low <= high:  # binary search: at most ~20 serializations
        cap = (low + high) // 2
        candidate = _cap_strings(obj, cap)
        if len(to_json_text(candidate)) <= budget:
            best, low = candidate, cap + 1
        else:
            high = cap - 1
    result = best if best is not None else _cap_strings(obj, MIN_FIELD_CHARS)
    return result, result != obj


def shape_comment(
    value: Any,
    *,
    max_chars: int = DEFAULT_COMMENT_CHARS,
    full: bool = False,
    fmt: CommentFormat = "markdown",
) -> Any:
    """Prepare a result comment for the assistant: clean first, then truncate.

    Cleaning before cutting matters: pasted rich text can spend 1,000+ characters on
    inline CSS before the first real word. The truncation marker reports the length of
    the text the assistant would actually get in full (cleaned length in markdown mode).
    """
    if not isinstance(value, str):
        return value
    text = clean_text(value) if fmt == "markdown" else value
    if full or len(text) <= max_chars:
        return text
    return text[:max_chars] + f"... [truncated, {len(text)} chars total]"


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, (str, list, dict)) and not value)


def result_shape(
    labels: Mapping[int, str],
    *,
    include_comments: bool = True,
    comment_max_chars: int = DEFAULT_COMMENT_CHARS,
    full_comment: bool = False,
    comment_format: CommentFormat = "markdown",
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Build the per-result shaping function used by the results tools."""

    def shape(result: Mapping[str, Any]) -> dict[str, Any]:
        out = with_status(pick(result, RESULT_FIELDS, include_custom=True), labels)
        if not include_comments:
            out.pop("comment", None)
        elif "comment" in out:
            out["comment"] = shape_comment(
                out["comment"], max_chars=comment_max_chars, full=full_comment, fmt=comment_format
            )
        if comment_format == "markdown":
            for key, value in out.items():
                if key.startswith("custom_"):
                    out[key] = clean_custom(value)
        # Empty fields (null elapsed/defects/version, no attachments, ...) are left out: they
        # cost ~1/3 of a result's size and say nothing. A missing field means "empty".
        return {k: v for k, v in out.items() if not _is_empty(v)}

    return shape


def full(obj: Mapping[str, Any]) -> dict[str, Any]:
    """Return the object with timestamps converted, keeping all fields."""
    return {k: (iso(v) if k in TIMESTAMP_FIELDS else v) for k, v in obj.items()}


def attachment(obj: Mapping[str, Any]) -> dict[str, Any]:
    picked = pick(obj, ATTACHMENT_FIELDS)
    return picked or full(obj)
