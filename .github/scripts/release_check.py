"""Release checks and notes for .github/workflows/release.yml (stdlib only).

    python .github/scripts/release_check.py check v0.3.0   -> prints 0.3.0, or exits 1
    python .github/scripts/release_check.py notes 0.3.0    -> prints the release notes

check: the tag must be vX.Y.Z, equal to __version__ in src/pestrail/__init__.py, CHANGELOG.md
must have a "## X.Y.Z (YYYY-MM-DD)" section, and the Claude Desktop extension (mcpb/) must
have the same version and pin pestrail==X.Y.Z, so nothing is published with a wrong version
or without notes.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAG_RE = re.compile(r"v(\d+\.\d+\.\d+)")
VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)
DISCLAIMER = (
    "Community project, not affiliated with or endorsed by TestRail, Gurock Software or "
    'Idera, Inc. "TestRail" is a trademark of its respective owner.'
)


class ReleaseError(Exception):
    pass


def package_version(root: Path | None = None) -> str:
    root = root or ROOT
    match = VERSION_RE.search((root / "src" / "pestrail" / "__init__.py").read_text("utf-8"))
    if not match:
        raise ReleaseError("__version__ not found in src/pestrail/__init__.py")
    return match.group(1)


def changelog_section(version: str, root: Path | None = None) -> str:
    """The body of the CHANGELOG section for ``version`` (without its heading)."""
    text = ((root or ROOT) / "CHANGELOG.md").read_text("utf-8")
    heading = re.compile(rf"^## {re.escape(version)} \(\d{{4}}-\d{{2}}-\d{{2}}\)[ \t]*$", re.M)
    match = heading.search(text)
    if not match:
        raise ReleaseError(f'CHANGELOG.md has no "## {version} (YYYY-MM-DD)" section')
    rest = text[match.end() :]
    following = re.search(r"^## ", rest, re.MULTILINE)
    body = (rest[: following.start()] if following else rest).strip()
    if not body:
        raise ReleaseError(f"The CHANGELOG.md section for {version} is empty")
    return body


def check_bundle(version: str, root: Path | None = None) -> None:
    """mcpb/manifest.json and mcpb/pyproject.toml must match the package version."""
    bundle = (root or ROOT) / "mcpb"
    manifest = json.loads((bundle / "manifest.json").read_text("utf-8"))
    project = tomllib.loads((bundle / "pyproject.toml").read_text("utf-8"))["project"]
    problems = []
    if manifest.get("version") != version:
        problems.append(f"mcpb/manifest.json version is {manifest.get('version')!r}")
    if project.get("version") != version:
        problems.append(f"mcpb/pyproject.toml version is {project.get('version')!r}")
    if project.get("dependencies") != [f"pestrail=={version}"]:
        problems.append(f"mcpb/pyproject.toml dependencies are {project.get('dependencies')!r}")
    if problems:
        raise ReleaseError(f"Extension bundle does not match {version}: " + "; ".join(problems))


def check(tag: str, root: Path | None = None) -> str:
    match = TAG_RE.fullmatch(tag)
    if not match:
        raise ReleaseError(f"Tag {tag!r} is not vX.Y.Z")
    version = match.group(1)
    actual = package_version(root)
    if actual != version:
        raise ReleaseError(f"Tag {tag} does not match __version__ = {actual!r}")
    changelog_section(version, root)
    check_bundle(version, root)
    return version


def notes(version: str, root: Path | None = None) -> str:
    return f"{changelog_section(version, root)}\n\n---\n\n{DISCLAIMER}\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("check", "notes"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        output = check(argv[2]) if argv[1] == "check" else notes(argv[2])
    except (ReleaseError, OSError) as exc:
        print(f"release check failed: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(output if output.endswith("\n") else output + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
