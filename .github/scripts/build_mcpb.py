"""Build the Claude Desktop extension (MCP Bundle) from mcpb/ (stdlib only).

    python .github/scripts/build_mcpb.py OUT_DIR   -> writes OUT_DIR/pestrail-X.Y.Z.mcpb

An .mcpb file is a zip with manifest.json at its root. The bundle carries no pestrail code:
Claude Desktop runs server/main.py with uv, which installs the pinned release from PyPI.
Entries are written in a fixed order with a fixed timestamp, so the same commit always
produces a byte-identical bundle.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_check

#: Path inside the bundle -> path in the repository (relative to its root).
FILES = {
    "manifest.json": "mcpb/manifest.json",
    "pyproject.toml": "mcpb/pyproject.toml",
    "server/main.py": "mcpb/server/main.py",
    "LICENSE": "LICENSE",
}
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def build(out_dir: Path, root: Path | None = None) -> Path:
    root = root or release_check.ROOT
    version = release_check.package_version(root)
    release_check.check_bundle(version, root)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"pestrail-{version}.mcpb"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, source in FILES.items():
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            bundle.writestr(info, (root / source).read_bytes())
    return target


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        print(build(Path(argv[1])))
    except (release_check.ReleaseError, OSError, KeyError, ValueError) as exc:
        print(f"bundle build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
