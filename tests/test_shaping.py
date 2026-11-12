"""Shaping helpers must not mutate caller-owned mappings."""

from __future__ import annotations

from pestrail import shaping as sh


def test_with_status_does_not_mutate_input():
    original = {"status_id": 5, "title": "t"}
    out = sh.with_status(original, {5: "Failed"})
    assert out["status"] == "Failed"
    assert "status" not in original
    assert out is not original


def test_truncate_comment_does_not_mutate_input():
    original = {"comment": "x" * 50}
    out = sh.truncate_comment(original, limit=10)
    assert out["comment"].startswith("xxxxxxxxxx...")
    assert original["comment"] == "x" * 50
