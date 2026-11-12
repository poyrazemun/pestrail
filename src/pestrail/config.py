"""Configuration loading and validation from environment variables."""

from __future__ import annotations

import ipaddress
import math
import os
import ssl
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_TIMEOUT = 30.0
DEFAULT_UPLOAD_TIMEOUT = 300.0
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}

#: Only these keys may be taken from a .env file; anything else (proxies, SSL_CERT_FILE,
#: PATH, ...) is ignored so a stray .env cannot reconfigure the process.
ENV_FILE_PREFIXES = ("TESTRAIL_", "PESTRAIL_")
#: Settings that decide *where* credentials are sent. If the API key comes from the real
#: environment, these must come from there too, never from a .env file.
DESTINATION_KEYS = ("TESTRAIL_URL", "TESTRAIL_CA_BUNDLE", "TESTRAIL_ALLOW_HTTP")


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


class Secret:
    """Wraps a secret string so it never shows up in repr/str/logs by accident."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def get(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('********')"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and other._value == self._value

    def __hash__(self) -> int:
        return hash(self._value)


@dataclass(frozen=True)
class Config:
    url: str
    username: str
    api_key: Secret = field(repr=False)
    read_only: bool = False
    attachment_roots: tuple[Path, ...] = ()
    #: When True and attachment_roots is empty, any readable file may be uploaded.
    #: Must be set explicitly; write mode refuses to start with neither roots nor this flag.
    allow_any_attachment_path: bool = False
    #: When non-empty, write tools may only target these projects (and runs inside them).
    allowed_project_ids: frozenset[int] = field(default_factory=frozenset)
    #: When non-empty, write tools may only target these runs (or tests/results in them).
    allowed_run_ids: frozenset[int] = field(default_factory=frozenset)
    timeout: float = DEFAULT_TIMEOUT
    ca_bundle: Path | None = None
    upload_timeout: float = DEFAULT_UPLOAD_TIMEOUT


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_url(raw: str, *, allow_http: bool = False) -> str:
    """Strip whitespace, trailing slashes and an accidental index.php suffix, and validate.

    Plain http:// is refused (credentials would travel unencrypted) unless the host is
    loopback or ``allow_http`` is set.
    """
    url = raw.strip().rstrip("/")
    for suffix in ("/index.php?/api/v2", "/index.php?", "/index.php"):
        if url.lower().endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ConfigError(
            "TESTRAIL_URL must be a full http(s) URL, e.g. https://example.testrail.io"
        )
    if parts.username or parts.password:
        raise ConfigError(
            "TESTRAIL_URL must not contain credentials. "
            "Use TESTRAIL_USERNAME and TESTRAIL_API_KEY instead."
        )
    if parts.query or parts.fragment:
        raise ConfigError(
            "TESTRAIL_URL must be the base address of your instance, without '?' or '#'."
        )
    if parts.scheme == "http" and not allow_http and not _is_loopback(parts.hostname):
        raise ConfigError(
            "TESTRAIL_URL uses plain http://, which would send your API key unencrypted. "
            "Use https://, or set TESTRAIL_ALLOW_HTTP=true if you accept the risk "
            "(e.g. an isolated lab network)."
        )
    return url


def parse_bool(name: str, raw: str | None) -> bool:
    if raw is None:
        return False
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def parse_roots(raw: str | None) -> tuple[Path, ...]:
    if not raw or not raw.strip():
        return ()
    roots: list[Path] = []
    for part in raw.split(os.pathsep):
        part = part.strip().strip('"')
        if not part:
            continue
        root = Path(part).expanduser()
        try:
            resolved = root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ConfigError(f"TESTRAIL_ATTACHMENT_ROOTS entry does not exist: {part!r}") from exc
        if not resolved.is_dir():
            raise ConfigError(f"TESTRAIL_ATTACHMENT_ROOTS entry is not a directory: {part!r}")
        roots.append(resolved)
    return tuple(roots)


def parse_timeout(
    raw: str | None, name: str = "TESTRAIL_TIMEOUT", default: float = DEFAULT_TIMEOUT
) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number of seconds, got {raw!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{name} must be a finite number greater than 0")
    return value


def parse_ca_bundle(raw: str | None) -> Path | None:
    """Validate TESTRAIL_CA_BUNDLE: an existing PEM file that OpenSSL can load."""
    if raw is None or not raw.strip():
        return None
    path = Path(raw.strip().strip('"')).expanduser()
    if not path.is_file():
        raise ConfigError(f"TESTRAIL_CA_BUNDLE file not found: {raw!r}")
    try:
        ssl.create_default_context(cafile=str(path))
    except (ssl.SSLError, OSError, ValueError) as exc:
        raise ConfigError(
            f"TESTRAIL_CA_BUNDLE could not be loaded as PEM certificates: {raw!r}"
        ) from exc
    return path.resolve()


def parse_allowed_ids(raw: str | None, kind: str, name: str) -> frozenset[int]:
    """Parse a comma-separated allowlist of TestRail IDs (numbers or prefixed IDs)."""
    if raw is None or not raw.strip():
        return frozenset()
    from .client import TestRailError
    from .ids import parse_id_list

    try:
        values = parse_id_list(raw, kind)
    except TestRailError as exc:
        raise ConfigError(f"{name} is invalid: {exc}") from exc
    return frozenset(values or [])


def user_config_dir(environ: Mapping[str, str] | None = None, *, os_name: str = os.name) -> Path:
    """Per-user config directory: %APPDATA%\\pestrail on Windows, else
    $XDG_CONFIG_HOME/pestrail or ~/.config/pestrail."""
    environ = os.environ if environ is None else environ
    if os_name == "nt":
        base = environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "pestrail"


def env_file_location(
    environ: Mapping[str, str] | None = None, *, os_name: str = os.name
) -> tuple[Path | None, bool]:
    """Which .env file to load, as (path or None, explicitly_requested).

    * PESTRAIL_ENV_FILE=none (or the null device): no file.
    * PESTRAIL_ENV_FILE=<path>: that file (explicit opt-in, relative paths use the cwd).
    * Otherwise: the per-user file in user_config_dir(). A .env in the working directory
      is never loaded implicitly, because MCP clients may start pestrail inside a cloned
      repository whose .env could point it at someone else's server.
    """
    environ = os.environ if environ is None else environ
    explicit = (environ.get("PESTRAIL_ENV_FILE") or "").strip()
    if explicit.lower() == "none" or explicit == os.devnull:
        return None, True
    if explicit:
        return Path(explicit).expanduser(), True
    return user_config_dir(environ, os_name=os_name) / ".env", False


def apply_env_file(
    values: Mapping[str, str | None], environ: MutableMapping[str, str]
) -> tuple[list[str], list[str]]:
    """Merge values read from a .env file into ``environ``, safely.

    * Real environment variables always win.
    * Only TESTRAIL_* / PESTRAIL_* keys are applied; others are ignored.
    * If TESTRAIL_API_KEY is already in the real environment, the .env file may not
      supply TESTRAIL_URL / TESTRAIL_CA_BUNDLE / TESTRAIL_ALLOW_HTTP. Otherwise a .env file
      in a cloned repository could redirect your key to a server of its choosing.

    Returns (applied_keys, ignored_keys). Raises ConfigError on an unsafe mix.
    """
    key_in_env = bool((environ.get("TESTRAIL_API_KEY") or "").strip())
    if key_in_env:
        unsafe = sorted(k for k in DESTINATION_KEYS if values.get(k) and k not in environ)
        if unsafe:
            raise ConfigError(
                "Refusing to read " + ", ".join(unsafe) + " from a .env file while "
                "TESTRAIL_API_KEY comes from the environment: that would let a .env file "
                "decide where your API key is sent. Set these in the same place as the key "
                "(e.g. your MCP client config), or put the key in the .env file too."
            )
    applied: list[str] = []
    ignored: list[str] = []
    for key, value in values.items():
        if value is None or key in environ:
            continue
        if not key.startswith(ENV_FILE_PREFIXES) or key == "PESTRAIL_ENV_FILE":
            ignored.append(key)
            continue
        environ[key] = value
        applied.append(key)
    return applied, ignored


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build a Config from the given mapping (defaults to os.environ)."""
    env = os.environ if env is None else env
    required = ("TESTRAIL_URL", "TESTRAIL_USERNAME", "TESTRAIL_API_KEY")
    missing = [name for name in required if not (env.get(name) or "").strip()]
    if missing:
        raise ConfigError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". See .env.example or the README for setup."
        )
    allow_http = parse_bool("TESTRAIL_ALLOW_HTTP", env.get("TESTRAIL_ALLOW_HTTP"))
    read_only = parse_bool("TESTRAIL_READ_ONLY", env.get("TESTRAIL_READ_ONLY"))
    attachment_roots = parse_roots(env.get("TESTRAIL_ATTACHMENT_ROOTS"))
    allow_any_attachment_path = parse_bool(
        "TESTRAIL_ALLOW_ANY_ATTACHMENT_PATH", env.get("TESTRAIL_ALLOW_ANY_ATTACHMENT_PATH")
    )
    if not read_only and not attachment_roots and not allow_any_attachment_path:
        raise ConfigError(
            "TESTRAIL_ATTACHMENT_ROOTS is required when write tools are enabled, so an "
            "assistant cannot upload arbitrary files (e.g. SSH keys) to TestRail. "
            "Set it to your test-artifact directories, use TESTRAIL_READ_ONLY=true, or set "
            "TESTRAIL_ALLOW_ANY_ATTACHMENT_PATH=true if you accept the risk."
        )
    return Config(
        url=normalize_url(env["TESTRAIL_URL"], allow_http=allow_http),
        username=env["TESTRAIL_USERNAME"].strip(),
        api_key=Secret(env["TESTRAIL_API_KEY"].strip()),
        read_only=read_only,
        attachment_roots=attachment_roots,
        allow_any_attachment_path=allow_any_attachment_path,
        allowed_project_ids=parse_allowed_ids(
            env.get("TESTRAIL_ALLOWED_PROJECT_IDS"), "project", "TESTRAIL_ALLOWED_PROJECT_IDS"
        ),
        allowed_run_ids=parse_allowed_ids(
            env.get("TESTRAIL_ALLOWED_RUN_IDS"), "run", "TESTRAIL_ALLOWED_RUN_IDS"
        ),
        timeout=parse_timeout(env.get("TESTRAIL_TIMEOUT")),
        ca_bundle=parse_ca_bundle(env.get("TESTRAIL_CA_BUNDLE")),
        upload_timeout=parse_timeout(
            env.get("TESTRAIL_UPLOAD_TIMEOUT"), "TESTRAIL_UPLOAD_TIMEOUT", DEFAULT_UPLOAD_TIMEOUT
        ),
    )
