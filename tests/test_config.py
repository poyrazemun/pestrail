from __future__ import annotations

import os
from pathlib import Path

import pytest

from pestrail.config import ConfigError, Secret, load_config, normalize_url

BASE_ENV = {
    "TESTRAIL_URL": "https://testrail.invalid/",
    "TESTRAIL_USERNAME": "qa@example.invalid",
    "TESTRAIL_API_KEY": "secret-key",
}


def test_loads_required_and_defaults():
    cfg = load_config(BASE_ENV)
    assert cfg.url == "https://testrail.invalid"
    assert cfg.username == "qa@example.invalid"
    assert cfg.api_key.get() == "secret-key"
    assert cfg.read_only is False
    assert cfg.attachment_roots == ()
    assert cfg.timeout == 30.0


@pytest.mark.parametrize("missing", ["TESTRAIL_URL", "TESTRAIL_USERNAME", "TESTRAIL_API_KEY"])
def test_missing_required_var(missing):
    env = {k: v for k, v in BASE_ENV.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        load_config(env)


def test_blank_required_var_counts_as_missing():
    with pytest.raises(ConfigError, match="TESTRAIL_API_KEY"):
        load_config({**BASE_ENV, "TESTRAIL_API_KEY": "   "})


@pytest.mark.parametrize(
    "raw",
    [
        "https://x.testrail.io",
        "https://x.testrail.io/",
        "https://x.testrail.io///",
        "https://x.testrail.io/index.php?/api/v2",
        "https://x.testrail.io/index.php",
        "  https://x.testrail.io/  ",
    ],
)
def test_normalize_url(raw):
    assert normalize_url(raw) == "https://x.testrail.io"


def test_url_with_subpath_kept():
    assert normalize_url("https://corp.invalid/testrail/") == "https://corp.invalid/testrail"


@pytest.mark.parametrize("raw", ["x.testrail.io", "ftp://x.testrail.io", "https://"])
def test_invalid_url(raw):
    with pytest.raises(ConfigError):
        normalize_url(raw)


def test_url_with_credentials_rejected():
    with pytest.raises(ConfigError, match="credentials"):
        normalize_url("https://user:pass@x.testrail.io")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("TRUE", True), ("1", True), ("yes", True), ("false", False), ("0", False)],
)
def test_read_only(raw, expected):
    assert load_config({**BASE_ENV, "TESTRAIL_READ_ONLY": raw}).read_only is expected


def test_read_only_invalid():
    with pytest.raises(ConfigError, match="TESTRAIL_READ_ONLY"):
        load_config({**BASE_ENV, "TESTRAIL_READ_ONLY": "maybe"})


def test_timeout():
    assert load_config({**BASE_ENV, "TESTRAIL_TIMEOUT": "12.5"}).timeout == 12.5
    with pytest.raises(ConfigError):
        load_config({**BASE_ENV, "TESTRAIL_TIMEOUT": "abc"})
    with pytest.raises(ConfigError):
        load_config({**BASE_ENV, "TESTRAIL_TIMEOUT": "0"})


def test_attachment_roots(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    cfg = load_config({**BASE_ENV, "TESTRAIL_ATTACHMENT_ROOTS": f"{a}{os.pathsep}{b}"})
    assert cfg.attachment_roots == (a.resolve(), b.resolve())


def test_attachment_root_must_exist(tmp_path: Path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_config({**BASE_ENV, "TESTRAIL_ATTACHMENT_ROOTS": str(tmp_path / "nope")})


def test_secret_is_never_rendered():
    cfg = load_config(BASE_ENV)
    assert "secret-key" not in repr(cfg)
    assert "secret-key" not in str(cfg)
    assert "secret-key" not in repr(cfg.api_key)
    assert "secret-key" not in f"{cfg.api_key}"
    assert Secret("a") == Secret("a")
