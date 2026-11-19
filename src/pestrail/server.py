"""MCP server app: builds an MCPServer and registers TestRail tools."""

import functools
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, model_validator

from . import __version__
from .client import TestRailClient, TestRailError
from .config import Config
from .schema import slim_schema
from .serialize import to_json_text

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
pestrail connects you to a TestRail instance for day-to-day QA work.

Typical navigation: get_projects -> get_suites / get_runs / get_plans -> get_tests (tests in a
run) -> get_results / add_result. Runs that belong to a test plan are only listed by get_plan,
not by get_runs.

IDs may be given as numbers or TestRail-style prefixed IDs: C123 (case), R45 (run), T678 (test),
P9 (plan/project), S3 (suite), M2 (milestone). Find them with the list tools (get_projects,
get_suites, get_sections, get_cases, get_runs, get_plans, get_tests, get_milestones,
get_user_by_email). A test ID (T) is a case inside a run, not a case ID (C).

Statuses: call get_statuses to see valid statuses on this instance (they can be customized).
Result tools accept a status ID or name ("Passed", "failed", "Blocked", "Retest"...). "Untested"
cannot be set.

Safety: this server cannot delete anything, cannot create/edit/move cases, suites or sections,
and cannot close runs or plans. Results are always added (never overwritten); TestRail keeps
the full history. Writes may also be limited to TESTRAIL_ALLOWED_PROJECT_IDS /
TESTRAIL_ALLOWED_RUN_IDS when configured. Before adding results, confirm the run/case IDs with
the user if there is any doubt, because added results cannot be removed through this server.
"""

READ_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)
WRITE_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)

ToolFn = Callable[..., Awaitable[Any]]
ArgModelT = TypeVar("ArgModelT", bound=BaseModel)


def _unknown_arguments_message(tool: str, unknown: list[str], allowed: set[str]) -> str:
    """Name the bad arguments and the valid ones, so the model's retry is the right call."""
    parts = []
    for name in unknown:
        # TestRail's API says status_id / assignedto_id where the tools say status / ...
        guess = name.removesuffix("_id")
        hint = f" (did you mean '{guess}'?)" if guess != name and guess in allowed else ""
        parts.append(f"'{name}'{hint}")
    return (
        f"Unknown parameter {', '.join(parts)} for {tool}; nothing was sent to TestRail. "
        f"Valid parameters: {', '.join(sorted(allowed))}."
    )


def _forbid_extra(model: type[ArgModelT], tool: str) -> type[ArgModelT]:
    """Subclass of a tool's argument model that rejects arguments it does not define.

    The MCP SDK silently drops unknown arguments, so get_tests(status_id=...) used to
    return every test, and add_result(status_id=5) would add a result without a status.
    """
    allowed = {field.alias or name for name, field in model.model_fields.items()}

    def reject_unknown(cls: type[BaseModel], data: Any) -> Any:
        if isinstance(data, dict):
            unknown = sorted(str(key) for key in data if key not in allowed)
            if unknown:
                raise ValueError(_unknown_arguments_message(tool, unknown, allowed))
        return data

    # Same name as the original, so validation errors still name the tool.
    strict: type[ArgModelT] = type(
        model.__name__,
        (model,),
        {
            "model_config": ConfigDict(extra="forbid"),
            "__module__": model.__module__,
            "_reject_unknown": model_validator(mode="before")(classmethod(reject_unknown)),
        },
    )
    return strict


class Registry:
    """Shared state handed to tool modules: the MCP app, the client and the config."""

    def __init__(self, mcp: MCPServer, client: TestRailClient, config: Config) -> None:
        self.mcp = mcp
        self.client = client
        self.config = config
        self.tool_names: list[str] = []

    def _register(self, fn: ToolFn, annotations: ToolAnnotations) -> ToolFn:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = await fn(*args, **kwargs)
            except TestRailError as exc:
                # Anticipated failure: give the model a clear message, no stack trace.
                raise ToolError(str(exc)) from exc
            # A ready-made content block is passed through by the SDK unchanged. The SDK
            # would pretty-print (indent=2); we emit compact JSON, one list item per line.
            return TextContent(type="text", text=to_json_text(result))

        self.mcp.tool(annotations=annotations)(wrapper)
        tool = self.mcp._tool_manager.get_tool(fn.__name__)
        if tool is None:  # pragma: no cover - SDK internals changed; caught by tests
            raise RuntimeError(f"tool {fn.__name__!r} was not registered")
        # Unknown arguments are an error, not silently dropped (see _forbid_extra).
        strict = _forbid_extra(tool.fn_metadata.arg_model, fn.__name__)
        tool.fn_metadata.arg_model = strict
        # Advertise a smaller, equivalent schema: it is sent with every conversation.
        # Arguments are still validated with the pydantic model.
        tool.parameters = slim_schema(strict.model_json_schema(by_alias=True))
        self.tool_names.append(fn.__name__)
        return wrapper

    def read(self, fn: ToolFn) -> ToolFn:
        return self._register(fn, READ_ANNOTATIONS)

    def write(self, fn: ToolFn) -> ToolFn:
        if self.config.read_only:  # pragma: no cover - guarded by caller as well
            raise RuntimeError("write tools must not be registered in read-only mode")
        return self._register(fn, WRITE_ANNOTATIONS)

    async def status_labels(self) -> dict[int, str]:
        """Map status ID -> label. Best effort: returns {} if statuses cannot be loaded."""
        try:
            statuses = await self.client.get_statuses()
        except TestRailError:
            return {}
        return {int(s["id"]): str(s.get("label") or s.get("name")) for s in statuses if "id" in s}


def create_server(config: Config, client: TestRailClient | None = None) -> MCPServer:
    """Build the MCP server. Write tools are only registered when not in read-only mode."""
    from . import read_tools, write_tools  # local import avoids cycles

    mode = "read-only" if config.read_only else "read-write"
    client = client or TestRailClient(config)

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[dict[str, Any]]:
        try:
            yield {}
        finally:
            # Close pooled HTTPS connections cleanly when the MCP session ends.
            await client.aclose()

    mcp = MCPServer(
        name="pestrail",
        title="pestrail (TestRail)",
        instructions=INSTRUCTIONS + f"\nThis server is running in {mode} mode.",
        version=__version__,
        log_level="WARNING",
        lifespan=lifespan,
    )
    registry = Registry(mcp, client, config)
    read_tools.register(registry)
    if not config.read_only:
        write_tools.register(registry)
    logger.info("pestrail %s ready (%s, %d tools)", __version__, mode, len(registry.tool_names))
    return mcp
