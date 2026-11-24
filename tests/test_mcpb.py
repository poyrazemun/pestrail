"""Claude Desktop extension (mcpb/): launcher, manifest rules and bundle build.

Skipped when run from the sdist, which does not include mcpb/ or .github/.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "mcpb"
BUILD_SCRIPT = ROOT / ".github" / "scripts" / "build_mcpb.py"

if not (BUNDLE / "manifest.json").exists() or not BUILD_SCRIPT.exists():  # pragma: no cover
    pytest.skip("extension files are not in the sdist", allow_module_level=True)


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


launcher = _load("mcpb_launcher", BUNDLE / "server" / "main.py")
build_mcpb = _load("build_mcpb", BUILD_SCRIPT)
MANIFEST = json.loads((BUNDLE / "manifest.json").read_text("utf-8"))


# ----------------------------------------------------------------- launcher


def test_settings_from_the_form_are_kept():
    env = {"TESTRAIL_URL": "https://x.invalid", "TESTRAIL_READ_ONLY": "false", "PATH": "p"}
    launcher.prepare_environment(["--attachment-roots", "/a", "/b"], env)
    assert env == {
        "TESTRAIL_URL": "https://x.invalid",
        "TESTRAIL_READ_ONLY": "false",
        "PATH": "p",
        "TESTRAIL_ATTACHMENT_ROOTS": os.pathsep.join(["/a", "/b"]),
    }


def test_unsubstituted_or_empty_settings_count_as_unset():
    env = {
        "TESTRAIL_URL": "https://x.invalid",
        "TESTRAIL_ALLOWED_RUN_IDS": "${user_config.allowed_run_ids}",
        "PESTRAIL_LOG_LEVEL": "  ",
        "OTHER": "${keep_me}",  # not ours: left alone
    }
    launcher.prepare_environment(["--attachment-roots", "${user_config.attachment_roots}"], env)
    assert "TESTRAIL_ALLOWED_RUN_IDS" not in env and "PESTRAIL_LOG_LEVEL" not in env
    assert "TESTRAIL_ATTACHMENT_ROOTS" not in env
    assert env["OTHER"] == "${keep_me}"


@pytest.mark.parametrize("value", [None, "", "${user_config.read_only}"])
def test_missing_read_only_setting_means_read_only(value):
    # pestrail alone would default to read-write when the variable is unset.
    env = {} if value is None else {"TESTRAIL_READ_ONLY": value}
    launcher.prepare_environment([], env)
    assert env["TESTRAIL_READ_ONLY"] == "true"


def test_no_folder_arguments():
    env: dict[str, str] = {}
    launcher.prepare_environment(["--attachment-roots"], env)
    assert "TESTRAIL_ATTACHMENT_ROOTS" not in env


@pytest.mark.parametrize(
    ("read_only", "roots", "problem"),
    [
        ("false", [], True),  # seen live: Read-only off, no folder added
        ("off", [], True),
        ("false", ["/logs"], False),
        ("true", [], False),
        (None, [], False),  # missing = read-only
    ],
)
def test_write_mode_without_folders_is_explained_in_form_terms(read_only, roots, problem):
    env = {} if read_only is None else {"TESTRAIL_READ_ONLY": read_only}
    launcher.prepare_environment(["--attachment-roots", *roots], env)
    message = launcher.setup_problem(env)
    assert (message is not None) == problem
    if message:
        assert "Attachment folder" in message and "Read-only" in message
        assert "TESTRAIL_" not in message  # no environment-variable jargon


def test_any_path_escape_hatch_is_respected():
    env = {"TESTRAIL_READ_ONLY": "false", "TESTRAIL_ALLOW_ANY_ATTACHMENT_PATH": "true"}
    assert launcher.setup_problem(env) is None


# ----------------------------------------------------------------- manifest


def test_manifest_basics():
    assert MANIFEST["manifest_version"] == "0.4"
    assert MANIFEST["name"] == "pestrail"
    server = MANIFEST["server"]
    assert server["type"] == "uv"
    assert server["entry_point"] in build_mcpb.FILES
    assert server["mcp_config"]["command"] == "uv"
    assert set(MANIFEST["compatibility"]["platforms"]) == {"darwin", "win32", "linux"}


def test_every_placeholder_refers_to_a_setting():
    config = MANIFEST["user_config"]
    text = json.dumps(MANIFEST["server"])
    used = set(re.findall(r"\$\{user_config\.(\w+)\}", text))
    assert used == set(config)  # nothing undefined, nothing unused


def test_settings_form_is_safe_by_default():
    config = MANIFEST["user_config"]
    assert config["testrail_api_key"]["sensitive"] is True  # masked, stored securely
    for key in ("testrail_url", "testrail_username", "testrail_api_key"):
        assert config[key]["required"] is True
    assert config["read_only"] == {**config["read_only"], "type": "boolean", "default": True}
    roots = config["attachment_roots"]
    assert roots["type"] == "directory" and roots["multiple"] is True
    # Folders go through args (documented: one argument per folder), not an env var.
    args = MANIFEST["server"]["mcp_config"]["args"]
    assert args[-2:] == ["--attachment-roots", "${user_config.attachment_roots}"]
    assert "TESTRAIL_ATTACHMENT_ROOTS" not in MANIFEST["server"]["mcp_config"]["env"]
    # Only the form counts: a personal .env file must not add settings the form hides.
    assert MANIFEST["server"]["mcp_config"]["env"]["PESTRAIL_ENV_FILE"] == "none"


def test_manifest_has_only_known_top_level_fields():
    # The v0.4 schema rejects unknown top-level keys (additionalProperties: false).
    known = {
        "$schema", "manifest_version", "name", "display_name", "version", "description",
        "long_description", "author", "repository", "homepage", "documentation", "support",
        "icon", "icons", "screenshots", "localization", "server", "tools", "tools_generated",
        "prompts", "prompts_generated", "keywords", "license", "privacy_policies",
        "compatibility", "user_config", "_meta",
    }  # fmt: skip
    assert set(MANIFEST) <= known


# ----------------------------------------------------------------- build


def test_bundle_contents_and_reproducibility(tmp_path):
    first = build_mcpb.build(tmp_path / "a")
    second = build_mcpb.build(tmp_path / "b")
    assert first.name == f"pestrail-{MANIFEST['version']}.mcpb"
    assert first.read_bytes() == second.read_bytes()  # same commit, same bytes
    with zipfile.ZipFile(first) as bundle:
        assert bundle.namelist() == list(build_mcpb.FILES)
        assert json.loads(bundle.read("manifest.json")) == MANIFEST
        assert b"pestrail==" in bundle.read("pyproject.toml")
        # No code or secrets from the repository ride along.
        assert not any(n.startswith(("src/", "tests/", ".")) for n in bundle.namelist())
