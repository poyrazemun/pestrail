"""TestRail API v2 HTTP client.

Safety model
------------
Every request goes through :meth:`TestRailClient._request`, which refuses any
API method that is not in :data:`ALLOWED_METHODS` *before* a request is built.
There are deliberately no ``delete_*``, ``close_*``, ``update_*``, ``move_*``
or case/suite/section ``add_*`` methods in the allowlist. Adding one requires
editing this file, and ``tests/test_client.py`` fails if anything
destructive appears here.
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
import re
import ssl
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from . import __version__
from .config import Config

logger = logging.getLogger(__name__)

#: API methods that only read data.
READ_METHODS: frozenset[str] = frozenset(
    {
        "get_projects",
        "get_project",
        "get_suites",
        "get_suite",
        "get_sections",
        "get_cases",
        "get_case",
        "get_case_fields",
        "get_result_fields",
        "get_statuses",
        "get_priorities",
        "get_milestones",
        "get_milestone",
        "get_plans",
        "get_plan",
        "get_runs",
        "get_run",
        "get_tests",
        "get_test",
        "get_results",
        "get_results_for_case",
        "get_results_for_run",
        "get_attachments_for_case",
        "get_attachments_for_test",
        "get_attachments_for_run",
        "get_attachments_for_plan",
        "get_users",
        "get_user_by_email",
        "get_configs",
    }
)

#: API methods that add data. None of them can remove or overwrite anything.
WRITE_METHODS: frozenset[str] = frozenset(
    {
        "add_result",
        "add_result_for_case",
        "add_results_for_cases",
        "add_attachment_to_result",
        "add_attachment_to_run",
        "add_attachment_to_case",
        "add_attachment_to_plan",
        "add_attachment_to_plan_entry",
    }
)

ALLOWED_METHODS: frozenset[str] = READ_METHODS | WRITE_METHODS

#: Prefixes that must never appear in the allowlist (checked at import time and in tests).
FORBIDDEN_PREFIXES: tuple[str, ...] = ("delete_", "close_", "update_", "move_", "copy_")

if any(m.startswith(FORBIDDEN_PREFIXES) for m in ALLOWED_METHODS):  # pragma: no cover
    raise RuntimeError("Destructive TestRail method found in allowlist; refusing to load.")

MAX_RETRIES_429 = 3
#: Upper bound for a single parsed Retry-After value (sanity clamp for absurd headers).
MAX_RETRY_DELAY = 3600.0
#: Total time we are willing to sleep on 429s for one tool call. MCP clients time out
#: requests (often after ~60s), so waiting longer would just turn into a client timeout.
MAX_TOTAL_RETRY_WAIT = 30.0
DEFAULT_RETRY_DELAY = 5.0
#: Safety cap for helpers that walk through all pages.
DEFAULT_MAX_ITEMS = 1000

PathArg = int | str
Params = Mapping[str, Any]
_GUID_RE = re.compile(
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
)


class TestRailError(Exception):
    """An error reported by TestRail, or a refusal by this client."""

    __test__ = False  # keep pytest from collecting this class

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:
        if self.status_code:
            return f"TestRail API error {self.status_code}: {self.message}"
        return self.message


class MethodNotAllowedError(TestRailError):
    """Raised when code attempts to call a TestRail method outside the allowlist."""


_STATUS_HINTS = {
    401: "Authentication failed. Check TESTRAIL_USERNAME and TESTRAIL_API_KEY, "
    "and that the API is enabled (Administration > Site Settings > API).",
    403: "Permission denied. The TestRail user lacks permission for this action or project.",
    404: "Not found. Check the ID; use the corresponding get_* list tool to find valid IDs.",
    429: "Rate limited by TestRail and retries were exhausted. Try again later.",
}


def _format_param(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (list, tuple, set, frozenset)):
        return ",".join(str(v) for v in value)
    return str(value)


def _parse_retry_after(value: str | None) -> float:
    if not value:
        return DEFAULT_RETRY_DELAY
    value = value.strip()
    try:
        delay = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
            delay = when.timestamp() - time.time()
        except (TypeError, ValueError):
            delay = DEFAULT_RETRY_DELAY
    return max(0.0, min(delay, MAX_RETRY_DELAY))


def build_ssl_context(config: Config) -> ssl.SSLContext:
    """TLS context for TestRail requests.

    TESTRAIL_CA_BUNDLE wins if set. Otherwise the operating system's trust store is used
    (via truststore), so corporate CAs installed on the machine are trusted, which is what
    users behind TLS-inspecting proxies need. Falls back to certifi if truststore is missing.
    """
    if config.ca_bundle is not None:
        return ssl.create_default_context(cafile=str(config.ca_bundle))
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # pragma: no cover - truststore unavailable on this platform
        import certifi

        return ssl.create_default_context(cafile=certifi.where())


_MAX_ERROR_DETAIL = 300


def describe_network_error(method: str, exc: httpx.HTTPError, secret: str) -> str:
    """Turn an httpx transport error into an actionable message without leaking secrets."""
    detail = (str(exc) or type(exc).__name__).strip()
    if secret:
        detail = detail.replace(secret, "********")
    if len(detail) > _MAX_ERROR_DETAIL:
        detail = detail[:_MAX_ERROR_DETAIL] + "..."
    lowered = detail.lower()
    if isinstance(exc, httpx.ProxyError):
        hint = "Check your proxy settings (HTTPS_PROXY / HTTP_PROXY / NO_PROXY)."
    elif "certificate" in lowered or "ssl" in lowered or "tls" in lowered:
        hint = (
            "TLS certificate verification failed. If your network inspects TLS traffic or "
            "TestRail uses a private CA, install that CA in the OS certificate store or set "
            "TESTRAIL_CA_BUNDLE to the CA certificate file (PEM)."
        )
    elif "getaddrinfo" in lowered or "name or service" in lowered or "nodename" in lowered:
        hint = "The host name could not be resolved. Check TESTRAIL_URL and your network/VPN."
    elif isinstance(exc, httpx.ConnectError):
        hint = "Check TESTRAIL_URL, your network/VPN and proxy settings."
    else:
        hint = "Check your network connection and TESTRAIL_URL."
    return f"Could not reach TestRail ({method}): {type(exc).__name__}: {detail}. {hint}"


def extract_items(data: Any, key: str) -> tuple[list[Any], dict[str, Any]]:
    """Return (items, paging_meta) for both paginated-object and bare-list responses."""
    if isinstance(data, list):
        return data, {"paginated": False}
    if isinstance(data, dict):
        items = data.get(key)
        if items is None:
            items = []
        links = data.get("_links") or {}
        return list(items), {
            "paginated": True,
            "offset": data.get("offset"),
            "limit": data.get("limit"),
            "size": data.get("size"),
            "has_more": bool(links.get("next")),
        }
    raise TestRailError(f"Unexpected response shape for {key!r}: {type(data).__name__}")


class TestRailClient:
    """Async client for the TestRail API v2 that only calls allowlisted methods."""

    __test__ = False

    def __init__(
        self,
        config: Config,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_retries: int = MAX_RETRIES_429,
        max_total_wait: float = MAX_TOTAL_RETRY_WAIT,
    ) -> None:
        self._config = config
        self._base = f"{config.url}/index.php?/api/v2/"
        self._sleep = sleep
        self._max_retries = max_retries
        self._max_total_wait = max_total_wait
        # Uploads of large files need more time to send and for TestRail to process,
        # but connecting should fail as fast as any other request.
        self._upload_timeout = httpx.Timeout(config.upload_timeout, connect=config.timeout)
        self._http = httpx.AsyncClient(
            auth=httpx.BasicAuth(config.username, config.api_key.get()),
            timeout=config.timeout,
            headers={"Accept": "application/json", "User-Agent": f"pestrail/{__version__}"},
            follow_redirects=False,
            verify=build_ssl_context(config),
            transport=transport,
        )
        self._status_cache: list[dict[str, Any]] | None = None

    def __repr__(self) -> str:
        return f"TestRailClient(url={self._config.url!r}, read_only={self._config.read_only})"

    @property
    def read_only(self) -> bool:
        return self._config.read_only

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ core

    def build_url(
        self, method: str, path_args: tuple[PathArg, ...] = (), params: Params | None = None
    ) -> str:
        """Build ``{base}/index.php?/api/v2/{method}/{args}&k=v``.

        The ``?`` is already part of the base path, so query params are joined with ``&``.
        """
        self._check_method(method)
        segments = [method]
        for arg in path_args:
            if isinstance(arg, bool):
                raise TestRailError(f"Invalid ID {arg!r}.")
            if isinstance(arg, int):
                if arg < 0:
                    raise TestRailError(f"Invalid ID {arg!r}: IDs must be non-negative.")
            elif not (isinstance(arg, str) and _GUID_RE.fullmatch(arg)):
                # Strings are only allowed for GUID-style IDs (plan entry IDs).
                raise TestRailError(f"Invalid ID {arg!r}: expected an integer or GUID.")
            segments.append(str(arg))
        url = self._base + "/".join(segments)
        if params:
            for key, value in params.items():
                if value is None:
                    continue
                url += f"&{quote(str(key), safe='_')}={quote(_format_param(value), safe=',')}"
        return url

    def _check_method(self, method: str) -> None:
        if method not in ALLOWED_METHODS:
            raise MethodNotAllowedError(
                f"TestRail API method {method!r} is not allowed by pestrail. "
                "This server never deletes, closes or edits cases/suites/sections."
            )
        if self._config.read_only and method in WRITE_METHODS:
            raise MethodNotAllowedError(
                f"TestRail API method {method!r} is a write method and pestrail is in "
                "read-only mode (TESTRAIL_READ_ONLY=true)."
            )

    async def _request(
        self,
        method: str,
        path_args: tuple[PathArg, ...] = (),
        *,
        params: Params | None = None,
        json_body: Any = None,
        file_path: Path | None = None,
    ) -> Any:
        url = self.build_url(method, path_args, params)  # validates allowlist first
        http_method = "GET" if method in READ_METHODS else "POST"

        attempt = 0
        waited = 0.0
        while True:
            try:
                if file_path is not None:
                    with file_path.open("rb") as fh:
                        response = await self._http.post(
                            url,
                            files={"attachment": (file_path.name, fh)},
                            timeout=self._upload_timeout,
                        )
                elif http_method == "POST":
                    response = await self._http.post(
                        url,
                        json=json_body if json_body is not None else {},
                        headers={"Content-Type": "application/json"},
                    )
                else:
                    response = await self._http.get(url)
            except httpx.TimeoutException as exc:
                if file_path is not None:
                    raise TestRailError(
                        f"Upload of {file_path.name} timed out ({method}). For large files, "
                        f"raise TESTRAIL_UPLOAD_TIMEOUT (now {self._config.upload_timeout:.0f}s)."
                    ) from exc
                raise TestRailError(
                    f"Request to TestRail timed out after {self._config.timeout}s ({method})."
                ) from exc
            except httpx.HTTPError as exc:
                raise TestRailError(
                    describe_network_error(method, exc, self._config.api_key.get())
                ) from exc

            if response.status_code == 429 and attempt < self._max_retries:
                delay = _parse_retry_after(response.headers.get("Retry-After"))
                if waited + delay > self._max_total_wait:
                    # Waiting would outlast the MCP client's patience: fail now with a
                    # message that says how long TestRail wants us to wait.
                    break
                attempt += 1
                waited += delay
                logger.warning(
                    "TestRail rate limit hit on %s; retry %d/%d in %.1fs",
                    method,
                    attempt,
                    self._max_retries,
                    delay,
                )
                await self._sleep(delay)
                continue
            break

        logger.debug("%s %s -> %d", http_method, method, response.status_code)
        return self._handle_response(method, response)

    @staticmethod
    def _handle_response(method: str, response: httpx.Response) -> Any:
        status = response.status_code
        if 200 <= status < 300:
            if not response.content:
                return None
            try:
                return response.json()
            except ValueError as exc:
                raise TestRailError(
                    f"TestRail returned a non-JSON response for {method}.", status
                ) from exc

        message = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                message = str(body.get("error") or "")
        except ValueError:
            pass
        if 300 <= status < 400:
            message = (
                "TestRail redirected the request. Check TESTRAIL_URL "
                "(use the exact https:// address of your instance)."
            )
        hint = _STATUS_HINTS.get(status, "")
        if status == 429 and response.headers.get("Retry-After"):
            wait = _parse_retry_after(response.headers.get("Retry-After"))
            hint = f"{hint} TestRail asked to wait {wait:.0f}s before retrying."
        if message and hint:
            message = f"{message} ({hint})"
        raise TestRailError(message or hint or f"Request failed for {method}.", status)

    # --------------------------------------------------------------- helpers

    async def get(self, method: str, *path_args: PathArg, params: Params | None = None) -> Any:
        if method not in READ_METHODS:
            raise MethodNotAllowedError(f"{method!r} is not a read method.")
        return await self._request(method, path_args, params=params)

    async def post(self, method: str, *path_args: PathArg, body: Any = None) -> Any:
        if method not in WRITE_METHODS:
            raise MethodNotAllowedError(f"{method!r} is not an allowed write method.")
        return await self._request(method, path_args, json_body=body)

    async def upload(self, method: str, *path_args: PathArg, file_path: Path) -> Any:
        if not method.startswith("add_attachment_to_"):
            raise MethodNotAllowedError(f"{method!r} is not an attachment upload method.")
        if method not in WRITE_METHODS:
            raise MethodNotAllowedError(f"{method!r} is not an allowed write method.")
        return await self._request(method, path_args, file_path=file_path)

    async def get_page(
        self,
        method: str,
        *path_args: PathArg,
        key: str,
        params: Params | None = None,
    ) -> tuple[list[Any], dict[str, Any]]:
        """Fetch one page of a bulk endpoint. Returns (items, paging_meta)."""
        data = await self.get(method, *path_args, params=params)
        return extract_items(data, key)

    async def get_all(
        self,
        method: str,
        *path_args: PathArg,
        key: str,
        params: Params | None = None,
        max_items: int = DEFAULT_MAX_ITEMS,
    ) -> tuple[list[Any], bool]:
        """Follow ``_links.next`` until exhausted or ``max_items`` reached.

        The next link is only used as a signal; the follow-up request is rebuilt from
        the same allowlisted method with an advanced ``offset``, so a server-provided
        URL can never redirect us to a different method.

        Returns (items, truncated).
        """
        base_params = dict(params or {})
        offset = int(base_params.pop("offset", 0) or 0)
        collected: list[Any] = []
        while True:
            page_params = dict(base_params)
            if offset:
                page_params["offset"] = offset
            items, meta = await self.get_page(method, *path_args, key=key, params=page_params)
            if not meta.get("paginated"):
                # TestRail < 6.7 returns the whole list as a bare array and ignores
                # limit/offset, so apply the offset here. There are no further pages.
                items = items[offset:]
                more = False
            else:
                more = bool(meta.get("has_more")) and bool(items)
            collected.extend(items)
            if len(collected) > max_items or (len(collected) == max_items and more):
                return collected[:max_items], True
            if not more:
                return collected, False
            offset += len(items)

    async def get_statuses(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        """Return result statuses, cached for the lifetime of the client."""
        if self._status_cache is None or refresh:
            data = await self.get("get_statuses")
            self._status_cache = list(data or [])
        return self._status_cache
