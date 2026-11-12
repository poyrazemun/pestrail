"""Date / timestamp parsing edge cases."""

from __future__ import annotations

import pytest

from pestrail.client import TestRailError
from pestrail.read_tools import _MAX_TIMESTAMP, to_timestamp


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        (0, 0),
        (1704067200, 1704067200),
        ("1704067200", 1704067200),
        ("2024-01-01", 1704067200),
    ],
)
def test_to_timestamp_ok(value, expected):
    assert to_timestamp(value) == expected


@pytest.mark.parametrize("value", [-1, _MAX_TIMESTAMP + 1, str(_MAX_TIMESTAMP + 1)])
def test_to_timestamp_out_of_range(value):
    with pytest.raises(TestRailError, match="out of range"):
        to_timestamp(value)


def test_to_timestamp_invalid_text():
    with pytest.raises(TestRailError, match="Invalid date"):
        to_timestamp("not-a-date")
