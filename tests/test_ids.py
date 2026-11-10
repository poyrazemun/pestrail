from __future__ import annotations

import pytest

from pestrail.client import TestRailError
from pestrail.ids import parse_id, parse_id_list, resolve_status

STATUSES = [
    {"id": 1, "name": "passed", "label": "Passed", "is_untested": False},
    {"id": 2, "name": "blocked", "label": "Blocked", "is_untested": False},
    {"id": 3, "name": "untested", "label": "Untested", "is_untested": True},
    {"id": 4, "name": "retest", "label": "Retest", "is_untested": False},
    {"id": 5, "name": "failed", "label": "Failed", "is_untested": False},
    {"id": 6, "name": "custom_status1", "label": "Known Issue", "is_untested": False},
]


@pytest.mark.parametrize(
    ("value", "kind", "expected"),
    [
        (123, "case", 123),
        ("123", "case", 123),
        ("C123", "case", 123),
        ("c123", "case", 123),
        (" C 123 ", "case", 123),
        ("R45", "run", 45),
        ("T678", "test", 678),
        ("P9", "plan", 9),
        ("S3", "suite", 3),
        ("M2", "milestone", 2),
        ("77", "result", 77),
    ],
)
def test_parse_id(value, kind, expected):
    assert parse_id(value, kind) == expected


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("R45", "case"),
        ("C1", "run"),
        ("abc", "case"),
        ("", "case"),
        (0, "case"),
        (-1, "run"),
        (True, "case"),
        (None, "case"),
        ("C-1", "case"),
        ("CC1", "case"),
        ("X5", "result"),
    ],
)
def test_parse_id_invalid(value, kind):
    with pytest.raises(TestRailError):
        parse_id(value, kind)


def test_parse_id_list():
    assert parse_id_list(["C1", 2, "3"], "case") == [1, 2, 3]
    assert parse_id_list("C1, C2", "case") == [1, 2]
    assert parse_id_list(None, "case") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, 1), ("5", 5), ("passed", 1), ("FAILED", 5), ("Known Issue", 6), ("custom_status1", 6)],
)
def test_resolve_status(value, expected):
    assert resolve_status(value, STATUSES) == expected


@pytest.mark.parametrize("value", [3, "3", "untested", "Untested"])
def test_untested_rejected(value):
    with pytest.raises(TestRailError, match="Untested"):
        resolve_status(value, STATUSES)


def test_untested_rejected_without_status_list():
    with pytest.raises(TestRailError, match="Untested"):
        resolve_status(3, [])


@pytest.mark.parametrize("value", [99, "nonsense"])
def test_unknown_status(value):
    with pytest.raises(TestRailError, match="Unknown status"):
        resolve_status(value, STATUSES)
