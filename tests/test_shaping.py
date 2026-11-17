"""Shaping helpers must not mutate caller-owned mappings."""

from __future__ import annotations

from pestrail import shaping as sh


def test_with_status_does_not_mutate_input():
    original = {"status_id": 5, "title": "t"}
    out = sh.with_status(original, {5: "Failed"})
    assert out["status"] == "Failed"
    assert "status" not in original
    assert out is not original


def test_result_shape_does_not_mutate_input():
    original = {"id": 1, "status_id": 5, "comment": "<p>" + "x" * 50 + "</p>"}
    shape = sh.result_shape({5: "Failed"}, comment_max_chars=10)
    out = shape(original)
    assert out["comment"] == "x" * 10 + "... [truncated, 50 chars total]"
    assert original["comment"] == "<p>" + "x" * 50 + "</p>"
    assert "status" not in original
