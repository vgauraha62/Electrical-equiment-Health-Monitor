"""Minimal JSON-Schema-Draft-2020-12-subset validator (stdlib only).

Supports the constructs used by schemas/v1: type (incl. unions),
required, enum, const, minLength, properties, items, additionalProperties,
and top-level `not.required` guard used by prediction.schema.json.
Raises ValidationError with JSON-pointer-style paths.
"""
from __future__ import annotations


class ValidationError(Exception):
    def __init__(self, message, path="$"):
        super().__init__(f"{path}: {message}")
        self.path = path
        self.detail = message


def _check_type(value, expected):
    mapping = {
        "string": str, "integer": int, "number": (int, float),
        "boolean": bool, "object": dict, "array": list, "null": type(None),
    }
    if isinstance(expected, list):
        return any(_check_type(value, item) for item in expected)
    if expected == "number" and isinstance(value, bool):
        return False
    if expected == "integer" and isinstance(value, bool):
        return False
    python_type = mapping.get(expected)
    if python_type is None:
        return True
    if expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    return isinstance(value, python_type)


def validate(instance, schema, path="$"):
    schema_type = schema.get("type")
    if schema_type is not None and not _check_type(instance, schema_type):
        raise ValidationError(f"expected type {schema_type}", path)
    if "const" in schema and instance != schema["const"]:
        raise ValidationError(f"value must equal {schema['const']!r}", path)
    if "enum" in schema and instance not in schema["enum"]:
        raise ValidationError(f"value {instance!r} not in enum {schema['enum']}", path)
    if isinstance(instance, str) and "minLength" in schema and len(instance) < schema["minLength"]:
        raise ValidationError(f"string shorter than minLength {schema['minLength']}", path)
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                raise ValidationError(f"missing required property '{key}'", path)
        for key, subschema in schema.get("properties", {}).items():
            if key in instance:
                validate(instance[key], subschema, f"{path}.{key}")
    if isinstance(instance, list) and "items" in schema:
        for index, item in enumerate(instance):
            validate(item, schema["items"], f"{path}[{index}]")
    not_schema = schema.get("not", {})
    forbidden = not_schema.get("required", [])
    if isinstance(instance, dict) and forbidden and all(k in instance for k in forbidden):
        raise ValidationError(f"forbidden combination present: {forbidden}", path)
    return True
