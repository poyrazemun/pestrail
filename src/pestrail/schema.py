"""Make the advertised tool schemas smaller without changing what they mean.

MCP clients put every tool's JSON schema into the model's context in every conversation,
so pydantic's verbose output costs tokens each time. This only rewrites the schema that is
*advertised*; arguments are still validated by the SDK with the original pydantic model.
"""

from typing import Any

#: Keywords that only constrain one JSON type. A branch of ``anyOf`` that uses only these
#: (for its own type) can be merged into a single ``type: [...]`` schema without changing
#: which values are valid: e.g. ``minimum`` is ignored for strings and null.
_TYPE_KEYWORDS: dict[str, frozenset[str]] = {
    "integer": frozenset({"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}),
    "number": frozenset({"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}),
    "string": frozenset({"minLength", "maxLength", "pattern", "format"}),
    "array": frozenset({"items", "minItems", "maxItems", "uniqueItems"}),
    "object": frozenset({"properties", "additionalProperties", "required"}),
    "boolean": frozenset(),
    "null": frozenset(),
}


def _merge_any_of(branches: list[Any]) -> dict[str, Any] | None:
    """``anyOf`` of simple, distinct types -> one schema with ``type: [...]``, else None."""
    merged: dict[str, Any] = {}
    types: list[str] = []
    for branch in branches:
        if not isinstance(branch, dict):
            return None
        kind = branch.get("type")
        if not isinstance(kind, str) or kind not in _TYPE_KEYWORDS or kind in types:
            return None
        extra = set(branch) - {"type"}
        if not extra <= _TYPE_KEYWORDS[kind] or extra & set(merged):
            return None
        types.append(kind)
        merged.update({k: v for k, v in branch.items() if k != "type"})
    # integer and number would share numeric keywords, so they are never merged together.
    if {"integer", "number"} <= set(types):
        return None
    merged["type"] = types
    return merged


def _slim(node: Any) -> Any:
    if isinstance(node, list):
        return [_slim(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title" and isinstance(value, str):
            continue  # auto-generated label ("Run Id"); the property name says the same
        if key in ("properties", "$defs") and isinstance(value, dict):
            # Keys here are names chosen by us (a property may be called "title").
            out[key] = {name: _slim(sub) for name, sub in value.items()}
        else:
            out[key] = _slim(value)
    if "default" in out and out["default"] is None:
        del out["default"]  # optional parameters are already not in "required"
    branches = out.get("anyOf")
    if isinstance(branches, list):
        merged = _merge_any_of(branches)
        if merged is not None:
            del out["anyOf"]
            out = {**merged, **out}
    return out


def slim_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a smaller, equivalent copy of a tool's input JSON schema."""
    result: dict[str, Any] = _slim(schema)
    return result
