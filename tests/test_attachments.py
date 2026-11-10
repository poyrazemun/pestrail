from __future__ import annotations

import os
from pathlib import Path

import pytest

from pestrail.attachments import AttachmentError, validate_attachment_path


@pytest.fixture
def layout(tmp_path: Path):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "sub").mkdir()
    inside = root / "sub" / "log.txt"
    inside.write_text("ok", encoding="utf-8")
    outside_dir = tmp_path / "secret"
    outside_dir.mkdir()
    outside = outside_dir / "creds.txt"
    outside.write_text("nope", encoding="utf-8")
    sibling = tmp_path / "allowed2"
    sibling.mkdir()
    sibling_file = sibling / "x.txt"
    sibling_file.write_text("x", encoding="utf-8")
    return root.resolve(), inside, outside, sibling_file


def test_inside_root_ok(layout):
    root, inside, _, _ = layout
    assert validate_attachment_path(str(inside), [root]) == inside.resolve()


def test_outside_root_rejected(layout):
    root, _, outside, _ = layout
    with pytest.raises(AttachmentError, match="outside the allowed"):
        validate_attachment_path(str(outside), [root])


def test_dotdot_escape_rejected(layout):
    root, _, _, _ = layout
    sneaky = root / "sub" / ".." / ".." / "secret" / "creds.txt"
    with pytest.raises(AttachmentError, match="outside the allowed"):
        validate_attachment_path(str(sneaky), [root])


def test_sibling_prefix_dir_rejected(layout):
    root, _, _, sibling_file = layout
    with pytest.raises(AttachmentError, match="outside the allowed"):
        validate_attachment_path(str(sibling_file), [root])


def test_symlink_escape_rejected(layout):
    root, _, outside, _ = layout
    link = root / "link.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this system")
    with pytest.raises(AttachmentError, match="outside the allowed"):
        validate_attachment_path(str(link), [root])


def test_no_roots_allows_any_file(layout):
    _, _, outside, _ = layout
    assert validate_attachment_path(str(outside), []) == outside.resolve()


def test_missing_file(tmp_path):
    with pytest.raises(AttachmentError, match="not found"):
        validate_attachment_path(str(tmp_path / "missing.log"), [])


def test_directory_rejected(layout):
    root, _, _, _ = layout
    with pytest.raises(AttachmentError, match="Not a regular file"):
        validate_attachment_path(str(root / "sub"), [root])


def test_size_limit(layout):
    root, inside, _, _ = layout
    with pytest.raises(AttachmentError, match="limit"):
        validate_attachment_path(str(inside), [root], max_bytes=1)


def test_empty_and_null_byte():
    with pytest.raises(AttachmentError):
        validate_attachment_path("", [])
    with pytest.raises(AttachmentError):
        validate_attachment_path("a\x00b", [])


@pytest.mark.skipif(os.name != "nt", reason="Windows paths are case-insensitive")
def test_windows_case_insensitive_root(layout):
    root, inside, _, _ = layout
    upper_root = Path(str(root).upper())
    assert validate_attachment_path(str(inside), [upper_root]) == inside.resolve()
