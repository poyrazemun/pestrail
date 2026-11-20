"""The release workflow's checks (.github/scripts/release_check.py) and workflow hygiene.

Skipped when run from the sdist, which does not include .github/.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "release_check.py"
WORKFLOWS = ROOT / ".github" / "workflows"

if not SCRIPT.exists():  # pragma: no cover - sdist
    pytest.skip("release files are not in the sdist", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("release_check", SCRIPT)
assert _spec and _spec.loader
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)

CHANGELOG = """# Changelog

## 0.3.0 (2026-12-01)

### Added

- Something new.

## 0.2.0 (2026-11-17)

- Older.
"""


def make_repo(tmp_path: Path, version: str = "0.3.0", changelog: str = CHANGELOG) -> Path:
    (tmp_path / "src" / "pestrail").mkdir(parents=True)
    (tmp_path / "src" / "pestrail" / "__init__.py").write_text(
        f'"""pestrail."""\n\n__version__ = "{version}"\n', encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    return tmp_path


def test_matching_tag_passes(tmp_path):
    assert rc.check("v0.3.0", make_repo(tmp_path)) == "0.3.0"


@pytest.mark.parametrize(
    ("tag", "version", "changelog", "message"),
    [
        ("v0.4.0", "0.3.0", CHANGELOG, "does not match __version__"),
        ("0.3.0", "0.3.0", CHANGELOG, "is not vX.Y.Z"),
        ("v0.3", "0.3", CHANGELOG, "is not vX.Y.Z"),
        ("v0.3.0-rc1", "0.3.0", CHANGELOG, "is not vX.Y.Z"),
        ("v0.3.0", "0.3.0", "# Changelog\n\n## 0.2.0 (2026-11-17)\n\n- x\n", "has no"),
        ("v0.3.0", "0.3.0", "# Changelog\n\n## 0.3.0\n\n- undated\n", "has no"),
        ("v0.3.0", "0.3.0", "## 0.3.0 (2026-12-01)\n\n## 0.2.0 (2026-11-17)\n", "is empty"),
    ],
)
def test_bad_release_is_refused(tmp_path, tag, version, changelog, message):
    with pytest.raises(rc.ReleaseError, match=message):
        rc.check(tag, make_repo(tmp_path, version, changelog))


def test_notes_are_the_section_plus_disclaimer(tmp_path):
    text = rc.notes("0.3.0", make_repo(tmp_path))
    assert text.startswith("### Added\n\n- Something new.")
    assert "Older" not in text and "## 0.3.0" not in text
    assert text.rstrip().endswith(rc.DISCLAIMER)
    assert "not affiliated with or endorsed by TestRail" in rc.DISCLAIMER


def test_cli_exit_codes(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(rc, "ROOT", make_repo(tmp_path))
    assert rc.main(["x", "check", "v0.3.0"]) == 0
    assert capsys.readouterr().out == "0.3.0\n"
    assert rc.main(["x", "check", "v9.9.9"]) == 1
    assert "release check failed" in capsys.readouterr().err
    assert rc.main(["x", "bogus"]) == 2


def test_this_repo_is_consistent():
    # __version__ always has a dated CHANGELOG section: bump both in the same commit.
    version = rc.package_version()
    assert rc.check(f"v{version}") == version


# ----------------------------------------------------------------- workflow hygiene


@pytest.mark.parametrize("workflow", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_actions_are_pinned_to_commit_shas(workflow):
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", workflow.read_text("utf-8"), re.MULTILINE)
    assert uses, workflow
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), f"{workflow.name}: {ref}"


def test_release_permissions_are_minimal():
    text = (WORKFLOWS / "release.yml").read_text("utf-8")
    assert re.search(r"^permissions: \{\}$", text, re.MULTILINE)  # nothing by default
    assert text.count("id-token: write") == 1  # only the PyPI publish job
    assert text.count("contents: write") == 1  # only the draft-release job
    assert "persist-credentials: false" in text
    assert "environment:\n      name: pypi" in text
