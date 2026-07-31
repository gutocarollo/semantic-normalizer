from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from semantic_normalizer import ContractError
from semantic_normalizer.schema_validation import (
    load_package_schema,
    validate_instance,
)


def _reachable_schema_nodes(schema: dict) -> tuple[list[tuple[str, dict]], set[str]]:
    """Return schema nodes and local definitions reachable from the root."""
    definitions = schema.get("$defs", {})
    nodes: list[tuple[str, dict]] = []
    reachable_definitions: set[str] = set()
    visited_nodes: set[int] = set()

    def visit(node: object, path: str) -> None:
        if not isinstance(node, dict) or id(node) in visited_nodes:
            return
        visited_nodes.add(id(node))
        nodes.append((path, node))

        reference = node.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
                raise AssertionError(f"{path}: unsupported local reference {reference!r}")
            definition = reference.removeprefix("#/$defs/")
            if definition not in definitions:
                raise AssertionError(f"{path}: unresolved definition {definition!r}")
            reachable_definitions.add(definition)
            visit(definitions[definition], f"#/$defs/{definition}")

        for name, child in node.get("properties", {}).items():
            visit(child, f"{path}.properties.{name}")
        if "items" in node:
            visit(node["items"], f"{path}.items")
        for index, option in enumerate(node.get("oneOf", [])):
            visit(option, f"{path}.oneOf[{index}]")

    visit({key: value for key, value in schema.items() if key != "$defs"}, "$")
    return nodes, reachable_definitions


class SchemaKeywordContractTests(unittest.TestCase):
    def tearDown(self):
        load_package_schema.cache_clear()

    def test_max_length_one_regression_is_inclusive_and_counts_codepoints(self):
        schema = {"type": "string", "maxLength": 1}
        for value in ("a", "é", "😀"):
            with self.subTest(value=value):
                validate_instance(value, schema)
        for value in ("ab", "e\u0301", "😀😀"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ContractError, "longer than maxLength"):
                    validate_instance(value, schema)

    def test_max_items_is_inclusive_and_rejects_excess(self):
        schema = {"type": "array", "maxItems": 1}
        validate_instance([], schema)
        validate_instance(["one"], schema)
        with self.assertRaisesRegex(ContractError, "longer than maxItems"):
            validate_instance(["one", "two"], schema)

    def test_max_length_is_enforced_through_refs_and_one_of(self):
        referenced = {
            "$defs": {"short": {"type": "string", "maxLength": 1}},
            "$ref": "#/$defs/short",
        }
        validate_instance("é", referenced)
        with self.assertRaisesRegex(ContractError, "longer than maxLength"):
            validate_instance("éé", referenced)

        variant = {
            "oneOf": [
                {"type": "string", "maxLength": 1},
                {"const": "exception"},
            ]
        }
        validate_instance("x", variant)
        validate_instance("exception", variant)
        with self.assertRaisesRegex(ContractError, "exactly one schema variant"):
            validate_instance("xx", variant)

    def test_runtime_schemas_use_only_enforced_keywords_and_known_annotations(self):
        for filename in (
            "sidecar.schema.json",
            "reconciliation-request.schema.json",
            "reconciliation-response.schema.json",
            "reconciliation-decision.schema.json",
        ):
            with self.subTest(filename=filename):
                self.assertIsInstance(load_package_schema(filename), dict)

        validate_instance(
            "x",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "urn:test:supported-annotations",
                "title": "Supported annotations",
                "description": "Annotations do not change validation.",
                "type": "string",
            },
        )

    def test_sidecar_schema_reachable_objects_are_closed_and_defs_are_live(self):
        schema_path = ROOT / "src" / "semantic_normalizer" / "data" / "sidecar.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        nodes, reachable_definitions = _reachable_schema_nodes(schema)

        for path, node in nodes:
            with self.subTest(path=path):
                if node.get("type") == "object":
                    self.assertIs(
                        node.get("additionalProperties"),
                        False,
                        "reachable object schemas must reject extra properties",
                    )

        self.assertEqual(set(schema["$defs"]), reachable_definitions)

    def test_validator_rejects_unsupported_keyword_in_nested_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "maxProperties": 1},
            },
        }
        with self.assertRaisesRegex(
            ContractError, r"unsupported JSON Schema keywords \['maxProperties'\]"
        ):
            validate_instance({"name": "x"}, schema)

    def test_package_schema_loader_rejects_unsupported_keyword(self):
        with tempfile.TemporaryDirectory() as directory:
            schema_path = Path(directory) / "unsupported.schema.json"
            schema_path.write_text(
                json.dumps({"type": "string", "format": "email"}),
                encoding="utf-8",
            )
            load_package_schema.cache_clear()
            with patch(
                "semantic_normalizer.schema_validation.resources.files",
                return_value=Path(directory),
            ):
                with self.assertRaisesRegex(
                    ContractError, r"unsupported JSON Schema keywords \['format'\]"
                ):
                    load_package_schema(schema_path.name)


if __name__ == "__main__":
    unittest.main()
