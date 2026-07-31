"""Small fail-closed JSON Schema subset used by runtime contracts."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources

from .registry import ContractError


_SUPPORTED_SCHEMA_KEYWORDS = {
    "$defs",
    "$id",
    "$ref",
    "$schema",
    "additionalProperties",
    "const",
    "description",
    "enum",
    "items",
    "maximum",
    "maxItems",
    "maxLength",
    "minimum",
    "minItems",
    "minLength",
    "oneOf",
    "pattern",
    "properties",
    "required",
    "title",
    "type",
    "uniqueItems",
}


def _validate_schema_contract(schema: object, *, path: str = "$") -> None:
    """Reject schema keywords or shapes that this validator cannot enforce."""
    if not isinstance(schema, dict):
        raise ContractError(f"{path}: JSON Schema node is not an object")
    unsupported = sorted(set(schema) - _SUPPORTED_SCHEMA_KEYWORDS)
    if unsupported:
        raise ContractError(f"{path}: unsupported JSON Schema keywords {unsupported}")
    for keyword in ("$defs", "properties"):
        children = schema.get(keyword, {})
        if not isinstance(children, dict):
            raise ContractError(f"{path}.{keyword}: must be an object")
        for name, child in children.items():
            _validate_schema_contract(child, path=f"{path}.{keyword}.{name}")
    if "items" in schema:
        _validate_schema_contract(schema["items"], path=f"{path}.items")
    if "oneOf" in schema:
        options = schema["oneOf"]
        if not isinstance(options, list) or not options:
            raise ContractError(f"{path}.oneOf: must be a non-empty array")
        for index, option in enumerate(options):
            _validate_schema_contract(option, path=f"{path}.oneOf[{index}]")
    if "additionalProperties" in schema and not isinstance(
        schema["additionalProperties"], bool
    ):
        raise ContractError(
            f"{path}.additionalProperties: schema-valued form is unsupported"
        )


def _type_matches(value: object, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    raise ContractError(f"unsupported JSON Schema type: {expected}")


def _resolve(root: dict, reference: str) -> dict:
    if not reference.startswith("#/"):
        raise ContractError(f"unsupported JSON Schema reference: {reference}")
    value: object = root
    for part in reference[2:].split("/"):
        if not isinstance(value, dict) or part not in value:
            raise ContractError(f"unresolved JSON Schema reference: {reference}")
        value = value[part]
    if not isinstance(value, dict):
        raise ContractError(f"JSON Schema reference is not an object: {reference}")
    return value


def validate_instance(
    value: object,
    schema: dict,
    *,
    root: dict | None = None,
    path: str = "$",
) -> None:
    """Validate the closed subset used by package schemas."""
    if root is None:
        _validate_schema_contract(schema)
        root = schema
    if "$ref" in schema:
        validate_instance(value, _resolve(root, schema["$ref"]), root=root, path=path)
    if "oneOf" in schema:
        matches = 0
        for option in schema["oneOf"]:
            try:
                validate_instance(value, option, root=root, path=path)
            except ContractError:
                continue
            matches += 1
        if matches != 1:
            raise ContractError(f"{path}: expected exactly one schema variant, got {matches}")
    if "const" in schema and value != schema["const"]:
        raise ContractError(f"{path}: value differs from schema const")
    if "enum" in schema and value not in schema["enum"]:
        raise ContractError(f"{path}: value is outside schema enum")
    expected = schema.get("type")
    if expected is not None:
        expected_types = [expected] if isinstance(expected, str) else expected
        if not any(_type_matches(value, item) for item in expected_types):
            raise ContractError(f"{path}: value has invalid type")
    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise ContractError(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise ContractError(f"{path}: unexpected properties {extras}")
        for key, item in value.items():
            if key in properties:
                validate_instance(item, properties[key], root=root, path=f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ContractError(f"{path}: array is shorter than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ContractError(f"{path}: array is longer than maxItems")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                raise ContractError(f"{path}: array items are not unique")
        if "items" in schema:
            for index, item in enumerate(value):
                validate_instance(item, schema["items"], root=root, path=f"{path}[{index}]")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ContractError(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ContractError(f"{path}: string is longer than maxLength")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise ContractError(f"{path}: string does not match pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ContractError(f"{path}: number is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ContractError(f"{path}: number is above maximum")


@lru_cache(maxsize=None)
def load_package_schema(filename: str) -> dict:
    try:
        raw = resources.files("semantic_normalizer.data").joinpath(filename).read_text(
            encoding="utf-8"
        )
        schema = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"{filename}: invalid package schema: {exc}") from exc
    if not isinstance(schema, dict):
        raise ContractError(f"{filename}: package schema is not an object")
    _validate_schema_contract(schema)
    return schema


def validate_sidecar_record(record: object) -> dict:
    schema = load_package_schema("sidecar.schema.json")
    validate_instance(record, schema)
    return record
