"""JSON Schema helpers for tool definitions."""

from typing import Any


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Replace every `$ref` into `$defs` with the definition itself and drop `$defs`.

    Pydantic emits nested models as `{"$ref": "#/$defs/Evidence"}`. Models fill flat,
    self-contained schemas more reliably than ones that need reference resolution (see the
    README's engineering notes), and the API accepts either, so we send the flat form.
    """
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = resolve(defs[node["$ref"].rsplit("/", 1)[-1]])
                return {**target, **{k: resolve(v) for k, v in node.items() if k != "$ref"}}
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)
