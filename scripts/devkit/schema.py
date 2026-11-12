"""Minimal JSON Schema validator (draft-07 subset).

Only the keywords used by this skill's schemas are implemented. Keeping it in
the standard library avoids a runtime dependency for developers and CI.

Supported: type, enum, const, required, properties, additionalProperties,
items, minItems, maxItems, minimum, maximum, minLength, maxLength, pattern,
allOf, anyOf, oneOf.
"""

from __future__ import annotations

import re
from typing import Any

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return isinstance(value, _TYPES.get(expected, object))


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def validate_schema(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Return a list of human-readable errors; an empty list means valid."""
    errors: list[str] = []
    if not isinstance(schema, dict):
        return errors

    if "allOf" in schema:
        for sub in schema["allOf"]:
            errors.extend(validate_schema(instance, sub, path))
    if "anyOf" in schema:
        if not any(not validate_schema(instance, sub, path) for sub in schema["anyOf"]):
            errors.append(f"{path}: does not match any allowed shape")
    if "oneOf" in schema:
        matches = sum(1 for sub in schema["oneOf"] if not validate_schema(instance, sub, path))
        if matches != 1:
            errors.append(f"{path}: must match exactly one allowed shape (matched {matches})")

    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(instance, t) for t in types):
            errors.append(f"{path}: expected {'/'.join(types)}, got {_type_name(instance)}")
            return errors

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']!r}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {}) or {}
        for key, value in instance.items():
            if key in properties:
                errors.extend(validate_schema(value, properties[key], f"{path}.{key}"))
            else:
                extra = schema.get("additionalProperties", True)
                if extra is False:
                    errors.append(f"{path}: unexpected property {key!r}")
                elif isinstance(extra, dict):
                    errors.extend(validate_schema(value, extra, f"{path}.{key}"))

    if isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, value in enumerate(instance):
                errors.extend(validate_schema(value, items, f"{path}[{index}]"))
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: allows at most {schema['maxItems']} item(s)")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']} character(s)")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']} character(s)")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: does not match pattern {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")

    return errors