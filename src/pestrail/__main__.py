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
    """Load the per-user .env file, or the one named by PESTRAIL_ENV_FILE.

    See config.env_file_location for which file is used (never the working directory's
    .env implicitly) and config.apply_env_file for the merge rules (real env wins, only
    TESTRAIL_*/PESTRAIL_* keys, no destination settings from the file when the API key
    comes from the environment).
    """
    from .config import ConfigError, apply_env_file, env_file_location

    path, explicit = env_file_location()
    if path is None:
        return
    if not path.is_file():
        if explicit:
            raise ConfigError(f"PESTRAIL_ENV_FILE not found: {path}")
        return
    from dotenv import dotenv_values

    applied, ignored = apply_env_file(dotenv_values(path), os.environ)
    log = logging.getLogger("pestrail")
    if applied:
        log.info("Loaded %s from %s", ", ".join(sorted(applied)), path)
    if ignored:
        log.warning("Ignored non-pestrail keys in %s: %s", path, ", ".join(sorted(ignored)))


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

    from .config import ConfigError, env_file_location, load_config

    try:
        _load_dotenv()
        config = load_config()
    except ConfigError as exc:
        message = f"pestrail: configuration error: {exc}"
        local_env = Path.cwd() / ".env"
        if "Missing required" in str(exc) and local_env.is_file():
            path, _ = env_file_location()
            message += (
                f"\npestrail: note: {local_env} exists but is not loaded automatically. "
                "Set PESTRAIL_ENV_FILE to its path to use it"
                + (f", or move it to {path}." if path else ".")
            )
        print(message, file=sys.stderr)
        sys.exit(2)

    from .server import create_server

    server = create_server(config)
    server.run("stdio")


if __name__ == "__main__":
    main()
