"""Local file validation for attachment uploads."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .client import TestRailError

#: TestRail's documented maximum attachment size.
MAX_ATTACHMENT_BYTES = 256 * 1024 * 1024


class AttachmentError(TestRailError):
    """Raised when a local file may not be uploaded."""


def validate_attachment_path(
    raw_path: str, roots: Sequence[Path], *, max_bytes: int = MAX_ATTACHMENT_BYTES
) -> Path:
    """Resolve ``raw_path`` and check it is an existing, allowed, not-too-large regular file.

    * ``~`` is expanded.
    * Relative paths are resolved against the attachment roots (the first root where the
      file exists wins), never against the server's working directory, which MCP clients
      set unpredictably. Without roots, relative paths are rejected.
    * Symlinks and ``..`` are resolved *before* the root check, so a link inside a root that
      points outside it is rejected.
    * If ``roots`` is empty, any readable file is allowed.
    """
    if not raw_path or not str(raw_path).strip():
        raise AttachmentError("A file path is required.")
    if "\x00" in raw_path:
        raise AttachmentError("Invalid file path.")

    candidate = Path(str(raw_path).strip().strip('"')).expanduser()
    if not candidate.is_absolute():
        candidate = _resolve_relative(candidate, raw_path, roots)
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise AttachmentError(f"File not found: {raw_path}") from exc
    except (OSError, RuntimeError) as exc:
        raise AttachmentError(f"Cannot access file {raw_path}: {type(exc).__name__}") from exc

    if roots and not any(_is_within(resolved, root) for root in roots):
        allowed = ", ".join(str(r) for r in roots)
        raise AttachmentError(
            f"File {raw_path} is outside the allowed attachment directories "
            f"(TESTRAIL_ATTACHMENT_ROOTS): {allowed}"
        )

    if not resolved.is_file():
        raise AttachmentError(f"Not a regular file: {raw_path}")

    size = resolved.stat().st_size
    if size > max_bytes:
        raise AttachmentError(
            f"File {resolved.name} is {size / 1024 / 1024:.1f} MB; "
            f"TestRail's limit is {max_bytes // 1024 // 1024} MB."
        )
    return resolved


def _resolve_relative(candidate: Path, raw_path: str, roots: Sequence[Path]) -> Path:
    if not roots:
        raise AttachmentError(
            f"Relative path {raw_path!r} is not supported: use an absolute path "
            "(or set TESTRAIL_ATTACHMENT_ROOTS so relative paths are resolved against it)."
        )
    for root in roots:
        joined = root / candidate
        if joined.exists():
            return joined
    searched = ", ".join(str(r) for r in roots)
    raise AttachmentError(f"File not found: {raw_path} (searched in: {searched})")


def _is_within(path: Path, root: Path) -> bool:
    try:
        root_resolved = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    # is_relative_to compares whole path parts, so "/data/logs2" is not inside "/data/logs".
    # WindowsPath comparisons are case-insensitive, matching NTFS semantics.
    return path.is_relative_to(root_resolved)
