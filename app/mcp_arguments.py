"""Validate the JSON Schema constraints published by the MCP tool registry.

Only the schema vocabulary used by that registry is supported. Invalid input
raises ValueError before any tool can create or change business data.
"""
from __future__ import annotations


def validate_arguments(value, schema):
    expected = schema["type"]
    types = {"object": dict, "array": list, "string": str, "integer": int}
    valid_type = type(value) is types[expected]
    # JSON Schema counts numbers with no fractional part as integers (1.0 too),
    # but JSON booleans must never pass Python's bool-is-an-int relationship.
    if expected == "integer" and type(value) is float:
        valid_type = value.is_integer()
    if not valid_type:
        raise ValueError("Invalid tool argument type")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("Invalid tool argument choice")
    if expected == "object":
        if any(key not in value for key in schema.get("required", [])):
            raise ValueError("Missing required tool argument")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, child in value.items():
            if key in properties:
                validate_arguments(child, properties[key])
            elif additional is False:
                raise ValueError("Unknown tool argument")
            elif isinstance(additional, dict):
                validate_arguments(child, additional)
    elif expected == "array":
        if len(value) < schema.get("minItems", 0) or (
            "maxItems" in schema and len(value) > schema["maxItems"]
        ):
            raise ValueError("Invalid tool argument item count")
        for child in value:
            validate_arguments(child, schema["items"])
    elif expected == "string":
        if len(value) < schema.get("minLength", 0) or (
            "maxLength" in schema and len(value) > schema["maxLength"]
        ):
            raise ValueError("Invalid tool argument length")
    elif expected == "integer":
        if ("minimum" in schema and value < schema["minimum"]) or (
            "maximum" in schema and value > schema["maximum"]
        ):
            raise ValueError("Invalid tool argument range")
