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
_OFF = {"0", "false", "no", "off"}
_ON = {"1", "true", "yes", "on"}

#: pestrail's own message names environment variables the extension user never sees.
NO_FOLDERS_MESSAGE = (
    "pestrail extension: Read-only is off, but no Attachment folder is set. pestrail does not "
    "start in this state, so Claude cannot upload arbitrary files from your computer. In "
    "Claude Desktop open Settings > Extensions > pestrail and either add at least one "
    "Attachment folder or turn Read-only back on, then Save."
)


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


def setup_problem(env: MutableMapping[str, str]) -> str | None:
    """A settings combination pestrail would refuse, explained in the form's terms."""
    write_mode = env.get("TESTRAIL_READ_ONLY", "true").strip().lower() in _OFF
    any_path = env.get("TESTRAIL_ALLOW_ANY_ATTACHMENT_PATH", "").strip().lower() in _ON
    if write_mode and not env.get("TESTRAIL_ATTACHMENT_ROOTS") and not any_path:
        return NO_FOLDERS_MESSAGE
    return None


def main() -> None:
    prepare_environment(sys.argv[1:], os.environ)
    problem = setup_problem(os.environ)
    if problem:
        print(problem, file=sys.stderr)  # shown in Claude Desktop's extension log
        sys.exit(2)

    from pestrail.__main__ import main as run_pestrail

    run_pestrail([])


if __name__ == "__main__":
    main()
