"""Entry point: ``pestrail`` / ``python -m pestrail`` runs the MCP server over stdio.

stdout carries MCP protocol messages only; all diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__


def _load_dotenv() -> None:
    """Load a .env file from the current directory (or PESTRAIL_ENV_FILE) if present.

    Real environment variables always win over values in the file.
    """
    explicit = os.environ.get("PESTRAIL_ENV_FILE")
    path = Path(explicit).expanduser() if explicit else Path.cwd() / ".env"
    if not path.is_file():
        if explicit:
            print(f"pestrail: PESTRAIL_ENV_FILE not found: {path}", file=sys.stderr)
        return
    from dotenv import load_dotenv

    load_dotenv(path, override=False)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="pestrail", description="Safety-first MCP server for TestRail (stdio)."
    )
    parser.add_argument("--version", action="version", version=f"pestrail {__version__}")
    parser.add_argument(
        "--log-level",
        default=os.environ.get("PESTRAIL_LOG_LEVEL", "WARNING"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level for stderr output (default: WARNING).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request URL at INFO; keep that out of normal logs.
    logging.getLogger("httpx").setLevel(max(logging.WARNING, logging.getLogger().level))

    _load_dotenv()

    from .config import ConfigError, load_config

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"pestrail: configuration error: {exc}", file=sys.stderr)
        sys.exit(2)

    from .server import create_server

    server = create_server(config)
    server.run("stdio")


if __name__ == "__main__":
    main()
