"""Serialize tool results as compact JSON with one list item per line.

Fully compact JSON (one line) is cheapest in tokens, but when an MCP client saves a large
tool result to a file, a single line cannot be read in parts. Pretty-printing fixes that
but costs ~30-40% more tokens on every call. The middle ground used here: compact JSON,
but each top-level key, and each element of a top-level list of objects, starts on its
own line. The result is still one valid JSON document.
"""

from __future__ import annotations

import json
from typing import Any


def compact(value: Any) -> str:
    """Single-line compact JSON; non-ASCII text is kept readable, not \\u-escaped."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


def to_json_text(value: Any) -> str:
    """Compact JSON with each top-level key and each top-level list item on its own line.

    Newlines inside strings are escaped by JSON (``\\n``), so every list item stays on
    exactly one line and a saved result can be read by line ranges.
    """
    if not isinstance(value, dict):
        return compact(value)
    parts: list[str] = []
    for key, item in value.items():
        name = compact(key)
        if isinstance(item, list) and item and all(isinstance(x, dict) for x in item):
            rows = ",\n".join(compact(x) for x in item)
            parts.append(f"{name}:[\n{rows}\n]")
        else:
            parts.append(f"{name}:{compact(item)}")
    return "{" + ",\n".join(parts) + "}"
