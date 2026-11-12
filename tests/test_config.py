from __future__ import annotations

import os
from pathlib import Path

import pytest

from pestrail.config import (
    ConfigError,
    Secret,
    apply_env_file,
    env_file_location,
    load_config,
    normalize_url,
    parse_allowed_ids,
    user_config_dir,
)

BASE_ENV = {
    "TESTRAIL_URL": "https://testrail.invalid/",
    "TESTRAIL_USERNAME": "qa@example.invalid",
    "TESTRAIL_API_KEY": "secret-key",
    # Most unit tests opt into unrestricted uploads; fail-closed startup is covered separately.
    "TESTRAIL_ALLOW_ANY_ATTACHMENT_PATH": "true",
}


def test_loads_required_and_defaults(tmp_path: Path):
    root = tmp_path / "artifacts"
    root.mkdir()
    env = {
        k: v for k, v in BASE_ENV.items() if k != "TESTRAIL_ALLOW_ANY_ATTACHMENT_PATH"
    }
    cfg = load_config({**env, "TESTRAIL_ATTACHMENT_ROOTS": str(root)})
    assert cfg.url == "https://testrail.invalid"
    assert cfg.username == "qa@example.invalid"
    assert cfg.api_key.get() == "secret-key"
    assert cfg.read_only is False
    assert cfg.attachment_roots == (root.resolve(),)
    assert cfg.allow_any_attachment_path is False
    assert cfg.timeout == 30.0


def test_write_mode_requires_attachment_roots_or_opt_in():
    env = {k: v for k, v in BASE_ENV.items() if k != "TESTRAIL_ALLOW_ANY_ATTACHMENT_PATH"}
    with pytest.raises(ConfigError, match="TESTRAIL_ATTACHMENT_ROOTS is required"):
        load_config(env)


def test_write_mode_allows_explicit_any_path_opt_in():
    env = {k: v for k, v in BASE_ENV.items() if k != "TESTRAIL_ALLOW_ANY_ATTACHMENT_PATH"}
    cfg = load_config({**env, "TESTRAIL_ALLOW_ANY_ATTACHMENT_PATH": "true"})
    assert cfg.attachment_roots == ()
    assert cfg.allow_any_attachment_path is True


def test_read_only_does_not_require_attachment_roots():
    env = {k: v for k, v in BASE_ENV.items() if k != "TESTRAIL_ALLOW_ANY_ATTACHMENT_PATH"}
    cfg = load_config({**env, "TESTRAIL_READ_ONLY": "true"})
    assert cfg.read_only is True
    assert cfg.attachment_roots == ()
    assert cfg.allow_any_attachment_path is False


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


def test_allowed_ids_parse_prefixed_and_blank():
    assert parse_allowed_ids("P1, 2, P3", "project", "TESTRAIL_ALLOWED_PROJECT_IDS") == frozenset(
        {1, 2, 3}
    )
    assert parse_allowed_ids("R56, 78", "run", "TESTRAIL_ALLOWED_RUN_IDS") == frozenset({56, 78})
    assert parse_allowed_ids(None, "run", "TESTRAIL_ALLOWED_RUN_IDS") == frozenset()
    assert parse_allowed_ids("  ", "run", "TESTRAIL_ALLOWED_RUN_IDS") == frozenset()


def test_allowed_ids_reject_wrong_prefix():
    with pytest.raises(ConfigError, match="TESTRAIL_ALLOWED_RUN_IDS"):
        parse_allowed_ids("C1", "run", "TESTRAIL_ALLOWED_RUN_IDS")


def test_load_config_allowed_scopes():
    cfg = load_config(
        {
            **BASE_ENV,
            "TESTRAIL_ALLOWED_PROJECT_IDS": "P1,2",
            "TESTRAIL_ALLOWED_RUN_IDS": "R56",
        }
    )
    assert cfg.allowed_project_ids == frozenset({1, 2})
    assert cfg.allowed_run_ids == frozenset({56})


# ----------------------------------------------------------------- http:// refusal


def test_plain_http_rejected():
    with pytest.raises(ConfigError, match="unencrypted"):
        load_config({**BASE_ENV, "TESTRAIL_URL": "http://testrail.corp.invalid"})


@pytest.mark.parametrize(
    "url", ["http://localhost:8080", "http://127.0.0.1", "http://[::1]:8000/testrail"]
)
def test_plain_http_allowed_for_loopback(url):
    assert load_config({**BASE_ENV, "TESTRAIL_URL": url}).url == url


def test_plain_http_allowed_with_opt_in():
    cfg = load_config(
        {**BASE_ENV, "TESTRAIL_URL": "http://lab.invalid", "TESTRAIL_ALLOW_HTTP": "true"}
    )
    assert cfg.url == "http://lab.invalid"


def test_url_with_query_rejected():
    with pytest.raises(ConfigError, match="without"):
        normalize_url("https://x.testrail.io/?foo=bar")


def test_upload_timeout():
    assert load_config(BASE_ENV).upload_timeout == 300.0
    assert load_config({**BASE_ENV, "TESTRAIL_UPLOAD_TIMEOUT": "900"}).upload_timeout == 900.0
    with pytest.raises(ConfigError, match="TESTRAIL_UPLOAD_TIMEOUT"):
        load_config({**BASE_ENV, "TESTRAIL_UPLOAD_TIMEOUT": "-1"})


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf"])
def test_timeout_must_be_finite(raw):
    with pytest.raises(ConfigError, match="finite"):
        load_config({**BASE_ENV, "TESTRAIL_TIMEOUT": raw})


# ----------------------------------------------------------------- CA bundle


def test_ca_bundle_valid():
    import certifi

    cfg = load_config({**BASE_ENV, "TESTRAIL_CA_BUNDLE": certifi.where()})
    assert cfg.ca_bundle == Path(certifi.where()).resolve()


def test_ca_bundle_missing(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_config({**BASE_ENV, "TESTRAIL_CA_BUNDLE": str(tmp_path / "nope.pem")})


def test_ca_bundle_garbage(tmp_path: Path):
    bad = tmp_path / "bad.pem"
    bad.write_text("not a certificate", encoding="utf-8")
    with pytest.raises(ConfigError, match="PEM"):
        load_config({**BASE_ENV, "TESTRAIL_CA_BUNDLE": str(bad)})


# ----------------------------------------------------------------- .env merging


def test_env_file_fills_missing_values_only():
    environ = {"TESTRAIL_USERNAME": "real@example.invalid"}
    applied, ignored = apply_env_file(
        {
            "TESTRAIL_URL": "https://from-file.invalid",
            "TESTRAIL_USERNAME": "file@example.invalid",
            "TESTRAIL_API_KEY": "file-key",
        },
        environ,
    )
    assert environ["TESTRAIL_USERNAME"] == "real@example.invalid"  # real env wins
    assert environ["TESTRAIL_URL"] == "https://from-file.invalid"
    assert sorted(applied) == ["TESTRAIL_API_KEY", "TESTRAIL_URL"]
    assert ignored == []


def test_env_file_ignores_foreign_keys():
    environ: dict[str, str] = {}
    applied, ignored = apply_env_file(
        {
            "HTTPS_PROXY": "http://evil.invalid:8080",
            "SSL_CERT_FILE": "/tmp/evil.pem",
            "PATH": "/evil",
            "PESTRAIL_ENV_FILE": "/other/.env",
            "TESTRAIL_READ_ONLY": "true",
            "EMPTY": None,
        },
        environ,
    )
    assert environ == {"TESTRAIL_READ_ONLY": "true"}
    assert applied == ["TESTRAIL_READ_ONLY"]
    assert sorted(ignored) == ["HTTPS_PROXY", "PATH", "PESTRAIL_ENV_FILE", "SSL_CERT_FILE"]


@pytest.mark.parametrize("key", ["TESTRAIL_URL", "TESTRAIL_CA_BUNDLE", "TESTRAIL_ALLOW_HTTP"])
def test_env_file_cannot_redirect_key_from_environment(key):
    environ = {"TESTRAIL_API_KEY": "real-key", "TESTRAIL_USERNAME": "u"}
    with pytest.raises(ConfigError, match="decide where your API key is sent"):
        apply_env_file({key: "x"}, environ)
    assert key not in environ


def test_env_file_destination_ok_when_already_in_environment():
    # URL is set in the real env too, so the .env value is simply ignored (real env wins).
    environ = {"TESTRAIL_API_KEY": "k", "TESTRAIL_URL": "https://real.invalid"}
    apply_env_file({"TESTRAIL_URL": "https://file.invalid"}, environ)
    assert environ["TESTRAIL_URL"] == "https://real.invalid"


# ----------------------------------------------------------------- .env location


def test_user_config_dir_windows():
    env = {"APPDATA": r"C:\Users\qa\AppData\Roaming"}
    assert user_config_dir(env, os_name="nt") == Path(env["APPDATA"]) / "pestrail"


def test_user_config_dir_posix_xdg_and_default():
    assert user_config_dir({"XDG_CONFIG_HOME": "/cfg"}, os_name="posix") == Path("/cfg/pestrail")
    assert user_config_dir({}, os_name="posix") == Path.home() / ".config" / "pestrail"


def test_env_file_default_is_per_user_not_cwd():
    path, explicit = env_file_location({"XDG_CONFIG_HOME": "/cfg"}, os_name="posix")
    assert path == Path("/cfg/pestrail/.env")
    assert explicit is False
    assert path != Path.cwd() / ".env"


def test_env_file_explicit_and_disabled():
    assert env_file_location({"PESTRAIL_ENV_FILE": "my.env"}) == (Path("my.env"), True)
    assert env_file_location({"PESTRAIL_ENV_FILE": "NONE"}) == (None, True)
    assert env_file_location({"PESTRAIL_ENV_FILE": os.devnull}) == (None, True)


def test_secret_is_never_rendered():
    cfg = load_config(BASE_ENV)
    assert "secret-key" not in repr(cfg)
    assert "secret-key" not in str(cfg)
    assert "secret-key" not in repr(cfg.api_key)
    assert "secret-key" not in f"{cfg.api_key}"
    assert Secret("a") == Secret("a")
