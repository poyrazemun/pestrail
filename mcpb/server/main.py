"""Claude Desktop extension entry point: map the extension's settings onto pestrail.

Claude Desktop starts this with ``uv run`` (see manifest.json), passing the settings form as
environment variables, and the "Attachment folders" as separate arguments after
``--attachment-roots`` (one per folder; the documented way to pass a multi-value setting).

Settings left empty may reach us as an unsubstituted ``${user_config.x}`` placeholder;
those are treated as not set, so pestrail never sees a bogus value. A missing read-only
setting means read-only.
"""

from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping

ROOTS_FLAG = "--attachment-roots"
PLACEHOLDER = "${"


def _is_set(value: str) -> bool:
    return bool(value.strip()) and PLACEHOLDER not in value


def prepare_environment(argv: list[str], env: MutableMapping[str, str]) -> None:
    """Drop unsubstituted settings and turn the folder arguments into an env var."""
    for name in [k for k in env if k.startswith(("TESTRAIL_", "PESTRAIL_"))]:
        if not _is_set(env[name]):
            del env[name]
    # The form's "Read-only" defaults to on; if its value is missing, stay read-only
    # (pestrail itself defaults to read-write when the variable is unset).
    env.setdefault("TESTRAIL_READ_ONLY", "true")
    if ROOTS_FLAG in argv:
        roots = [r for r in argv[argv.index(ROOTS_FLAG) + 1 :] if _is_set(r)]
        if roots:
            env["TESTRAIL_ATTACHMENT_ROOTS"] = os.pathsep.join(roots)


def main() -> None:
    prepare_environment(sys.argv[1:], os.environ)

    from pestrail.__main__ import main as run_pestrail

    run_pestrail([])


if __name__ == "__main__":
    main()
