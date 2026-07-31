from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from semantic_normalizer.cli import main
from semantic_normalizer.evaluator import _automatic_expansions, _rg_sidecar
from semantic_normalizer.exporters import export_skos, export_synonym_graph
from semantic_normalizer.normalizer import normalize_text
from semantic_normalizer.registry import automatic_surfaces, load_registry


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "src/semantic_normalizer/data/registry.jsonl"


class SemanticRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()

    def normalize(self, text: str, registry: dict | None = None) -> dict:
        return normalize_text(
            text, "<test>", "text", registry or self.registry
        )[0]

    @staticmethod
    def relation_concepts(record: dict) -> set[tuple[str, str, str]]:
        concepts = {
            unit["unit_id"]: unit["concept_id"]
            for unit in record["semantic_units"]
        }
        return {
            (
                relation["type"],
                concepts[relation["source_unit"]],
                concepts[relation["target_unit"]],
            )
            for relation in record["semantic_relations"]
        }

    @staticmethod
    def synthetic_registry(
        directory: str, *, english_policies: dict[str, str]
    ) -> dict:
        records = [
            json.loads(line)
            for line in REGISTRY.read_text(encoding="utf-8").splitlines()
        ]
        target = next(
            record for record in records
            if record["concept_id"] == "action.approve"
        )
        surfaces = {
            "pref": "approve-pref-x",
            "alt": "approve-alt-x",
            "hidden": "approve-hidden-x",
            "observed": "approve-observed-x",
        }
        target["labels"]["en"] = {
            "pref": surfaces["pref"],
            "alt": [surfaces["alt"]],
            "hidden": [surfaces["hidden"]],
            "observed": [surfaces["observed"]],
        }
        target["lexical_forms"]["en"] = [
            {
                "form": surfaces[kind],
                "features": {},
                "policy": english_policies[kind],
            }
            for kind in ("pref", "alt", "hidden", "observed")
        ]
        path = Path(directory) / "registry.jsonl"
        path.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )
        return load_registry(path)

    def test_temporal_infix_and_prefix_directions_en_and_pt(self) -> None:
        cases = {
            "Validate before delete.": {
                ("before", "action.validate", "action.delete"),
                ("after", "action.delete", "action.validate"),
            },
            "Before validate, delete.": {
                ("before", "action.delete", "action.validate"),
                ("after", "action.validate", "action.delete"),
            },
            "Delete after validate.": {
                ("after", "action.delete", "action.validate"),
                ("before", "action.validate", "action.delete"),
            },
            "After validate, delete.": {
                ("after", "action.delete", "action.validate"),
                ("before", "action.validate", "action.delete"),
            },
            "Validar antes de excluir.": {
                ("before", "action.validate", "action.delete"),
                ("after", "action.delete", "action.validate"),
            },
            "Antes de validar, excluir.": {
                ("before", "action.delete", "action.validate"),
                ("after", "action.validate", "action.delete"),
            },
            "Excluir depois de validar.": {
                ("after", "action.delete", "action.validate"),
                ("before", "action.validate", "action.delete"),
            },
            "Depois de validar, excluir.": {
                ("after", "action.delete", "action.validate"),
                ("before", "action.validate", "action.delete"),
            },
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                record = self.normalize(text)
                self.assertEqual(expected, self.relation_concepts(record))
                self.assertFalse(record["needs_review"])

    def test_temporal_structure_without_infix_or_prefix_comma_abstains(self) -> None:
        for text in ("Before validate delete.", "Antes de validar excluir."):
            with self.subTest(text=text):
                record = self.normalize(text)
                self.assertEqual(set(), self.relation_concepts(record))
                self.assertTrue(record["needs_review"])
                self.assertIn(
                    "finite_grammar_abstained_ambiguous_temporal_structure",
                    record["warnings"],
                )

    def test_real_review_forms_never_expand_query_or_index_rg(self) -> None:
        create = self.normalize("Criar.")
        index = self.normalize("Indexar.")
        condition = self.normalize("If validate, delete.")
        self.assertEqual(["create"], create["preferred_terms"]["en"])
        self.assertEqual(["index"], index["preferred_terms"]["en"])
        self.assertEqual("", create["search_fields"]["cross_language_terms"])
        self.assertEqual("", index["search_fields"]["cross_language_terms"])
        self.assertNotIn("create", create["text_expanded"].split())
        self.assertNotIn("index", index["text_expanded"].split())
        self.assertNotIn("se", condition["text_expanded"].casefold().split())

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(
                0,
                main(["query", "--text", "If validate, delete.", "--kind", "text"]),
            )
        search_text = json.loads(stdout.getvalue())["search_text"].casefold().split()
        self.assertNotIn("se", search_text)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            output = Path(directory) / "output"
            source.mkdir()
            (source / "input.txt").write_text(
                "Criar.\nIndexar.\nIf validate, delete.\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    0,
                    main(["index", str(source), "--output-dir", str(output)]),
                )
            rg = (output / "rg.txt").read_text(encoding="utf-8").casefold()
        self.assertNotIn("\tcreate\t", rg)
        self.assertNotIn("\tindex\t", rg)
        self.assertNotIn("\tse\t", rg)

    def test_auto_projection_includes_pref_alt_hidden_and_excludes_observed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = self.synthetic_registry(
                directory,
                english_policies={
                    "pref": "auto",
                    "alt": "auto",
                    "hidden": "auto",
                    "observed": "review",
                },
            )
            record = registry["by_id"]["action.approve"]
            self.assertEqual(
                (
                    "approve-pref-x",
                    "approve-alt-x",
                    "approve-hidden-x",
                ),
                automatic_surfaces(record, "en"),
            )
            normalized = self.normalize("Approve-hidden-x.", registry)
            self.assertEqual("approve-pref-x.", normalized["canonical_text"])
            self.assertEqual(
                "approve-pref-x approve-alt-x approve-hidden-x",
                normalized["search_fields"]["same_language_terms"],
            )
            self.assertNotIn("approve-observed-x", normalized["text_expanded"])
            self.assertEqual(
                [
                    "approve-pref-x",
                    "approve-alt-x",
                    "approve-hidden-x",
                    "aprovar",
                ],
                _automatic_expansions(
                    {"concept_ids": ["action.approve"]}, registry
                ),
            )
            synonym_terms = {
                term.strip()
                for line in export_synonym_graph(registry).splitlines()
                for term in line.split(",")
            }
            self.assertTrue(
                {
                    "approve-pref-x",
                    "approve-alt-x",
                    "approve-hidden-x",
                }.issubset(synonym_terms)
            )
            self.assertNotIn("approve-observed-x", synonym_terms)

    def test_review_pref_alt_hidden_remain_skos_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = self.synthetic_registry(
                directory,
                english_policies={
                    "pref": "review",
                    "alt": "review",
                    "hidden": "review",
                    "observed": "review",
                },
            )
            record = registry["by_id"]["action.approve"]
            self.assertEqual((), automatic_surfaces(record, "en"))
            normalized = self.normalize("Aprovar.", registry)
            for review_surface in (
                "approve-pref-x",
                "approve-alt-x",
                "approve-hidden-x",
                "approve-observed-x",
            ):
                self.assertNotIn(review_surface, normalized["text_expanded"])
                self.assertNotIn(
                    review_surface,
                    normalized["search_fields"]["cross_language_terms"],
                )
                self.assertNotIn(
                    review_surface,
                    _rg_sidecar("Aprovar.", "<test>", "text", registry),
                )
                self.assertNotIn(
                    review_surface,
                    _automatic_expansions(
                        {"concept_ids": ["action.approve"]}, registry
                    ),
                )
            synonym_terms = {
                term.strip()
                for line in export_synonym_graph(registry).splitlines()
                for term in line.split(",")
            }
            self.assertTrue(
                {
                    "approve-pref-x",
                    "approve-alt-x",
                    "approve-hidden-x",
                    "approve-observed-x",
                }.isdisjoint(synonym_terms)
            )
            skos = export_skos(registry)
            self.assertIn('"approve-pref-x"@en', skos)
            self.assertIn('"approve-alt-x"@en', skos)
            self.assertIn('"approve-hidden-x"@en', skos)
            self.assertNotIn('"approve-observed-x"@en', skos)

    def test_canonical_projection_falls_back_to_first_auto_surface(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = self.synthetic_registry(
                directory,
                english_policies={
                    "pref": "review",
                    "alt": "auto",
                    "hidden": "auto",
                    "observed": "review",
                },
            )
            normalized = self.normalize("Approve-hidden-x.", registry)
            self.assertEqual("approve-alt-x.", normalized["canonical_text"])
            self.assertNotIn("approve-pref-x", normalized["text_expanded"])
            self.assertEqual(
                "approve-alt-x approve-hidden-x",
                normalized["search_fields"]["same_language_terms"],
            )


if __name__ == "__main__":
    unittest.main()
