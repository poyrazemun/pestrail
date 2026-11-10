"""Parsing of TestRail IDs and status references."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .client import TestRailError

#: TestRail-style display prefixes accepted per entity.
PREFIXES: dict[str, str] = {
    "case": "C",
    "run": "R",
    "test": "T",
    "plan": "P",
    "suite": "S",
    "project": "P",
    "milestone": "M",
}

_ID_RE = re.compile(r"^\s*([A-Za-z]?)\s*(\d+)\s*$")
UNTESTED_STATUS_ID = 3


def parse_id(value: int | str | None, kind: str) -> int:
    """Turn ``123``, ``"123"`` or ``"C123"`` into ``123``.

    Only the prefix belonging to ``kind`` is accepted, so passing ``"R45"`` as a
    case ID fails loudly instead of silently using the wrong entity.
    """
    label = kind.replace("_", " ")
    if value is None:
        raise TestRailError(f"A {label} ID is required.")
    if isinstance(value, bool):
        raise TestRailError(f"Invalid {label} ID {value!r}.")
    if isinstance(value, int):
        if value <= 0:
            raise TestRailError(f"Invalid {label} ID {value!r}: must be a positive integer.")
        return value
    match = _ID_RE.match(str(value))
    if not match:
        raise TestRailError(
            f"Invalid {label} ID {value!r}: expected a number"
            + (f" or {PREFIXES[kind]}<number>" if kind in PREFIXES else "")
            + "."
        )
    prefix, digits = match.group(1).upper(), match.group(2)
    expected = PREFIXES.get(kind)
    if prefix and prefix != expected:
        hint = f" (expected {expected}<number>)" if expected else ""
        raise TestRailError(
            f"ID {value!r} has prefix {prefix!r}, which does not belong to a {label}{hint}."
        )
    number = int(digits)
    if number <= 0:
        raise TestRailError(f"Invalid {label} ID {value!r}: must be a positive integer.")
    return number


def parse_optional_id(value: int | str | None, kind: str) -> int | None:
    return None if value is None or value == "" else parse_id(value, kind)


def parse_id_list(values: Iterable[int | str] | int | str | None, kind: str) -> list[int] | None:
    if values is None:
        return None
    if isinstance(values, (int, str)):
        values = [v for v in str(values).split(",") if v.strip()]
    return [parse_id(v, kind) for v in values]


def resolve_status(
    value: int | str, statuses: list[dict[str, Any]], *, allow_untested: bool = False
) -> int:
    """Resolve a status ID or (case-insensitive) name/label to a status ID.

    Unless ``allow_untested`` is set (for filters), raises if the status is Untested
    (ID 3), which add_result rejects.
    """
    status_id: int | None = None
    if isinstance(value, bool):
        raise TestRailError(f"Invalid status {value!r}.")
    if isinstance(value, int) or (isinstance(value, str) and value.strip().isdigit()):
        status_id = int(value)
        if statuses and not any(s.get("id") == status_id for s in statuses):
            raise TestRailError(
                f"Unknown status ID {status_id}. Valid statuses: {_describe(statuses)}"
            )
    else:
        wanted = str(value).strip().lower()
        for status in statuses:
            names = {str(status.get("name", "")).lower(), str(status.get("label", "")).lower()}
            if wanted in names:
                status_id = int(status["id"])
                break
        if status_id is None:
            raise TestRailError(f"Unknown status {value!r}. Valid statuses: {_describe(statuses)}")

    if allow_untested:
        return status_id
    untested = next((s for s in statuses if s.get("is_untested")), None)
    untested_id = int(untested["id"]) if untested else UNTESTED_STATUS_ID
    if status_id == untested_id:
        raise TestRailError(
            "Status 'Untested' cannot be set with add_result. "
            f"Choose another status: {_describe(statuses)}"
        )
    return status_id


def resolve_status_filter(
    values: list[int | str] | int | str | None, statuses: list[dict[str, Any]]
) -> list[int] | None:
    """Resolve a status filter (IDs and/or names, Untested allowed) to a list of IDs."""
    if values is None or values == "" or values == []:
        return None
    if isinstance(values, (int, str)):
        values = [v.strip() for v in str(values).split(",") if v.strip()]
    return [resolve_status(v, statuses, allow_untested=True) for v in values]


def _describe(statuses: list[dict[str, Any]]) -> str:
    if not statuses:
        return "(could not load statuses; call get_statuses)"
    return ", ".join(
        f"{s.get('id')}={s.get('label') or s.get('name')}"
        for s in statuses
        if not s.get("is_untested")
    )
