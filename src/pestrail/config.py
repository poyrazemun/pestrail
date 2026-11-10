"""Configuration loading and validation from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_TIMEOUT = 30.0
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


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
    timeout: float = DEFAULT_TIMEOUT


def normalize_url(raw: str) -> str:
    """Strip whitespace, trailing slashes and an accidental index.php suffix."""
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


def parse_timeout(raw: str | None) -> float:
    if raw is None or not raw.strip():
        return DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"TESTRAIL_TIMEOUT must be a number of seconds, got {raw!r}") from exc
    if value <= 0:
        raise ConfigError("TESTRAIL_TIMEOUT must be greater than 0")
    return value


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
    return Config(
        url=normalize_url(env["TESTRAIL_URL"]),
        username=env["TESTRAIL_USERNAME"].strip(),
        api_key=Secret(env["TESTRAIL_API_KEY"].strip()),
        read_only=parse_bool("TESTRAIL_READ_ONLY", env.get("TESTRAIL_READ_ONLY")),
        attachment_roots=parse_roots(env.get("TESTRAIL_ATTACHMENT_ROOTS")),
        timeout=parse_timeout(env.get("TESTRAIL_TIMEOUT")),
    )
