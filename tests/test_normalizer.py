from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from semantic_normalizer.evaluation import evaluate_retrieval
from semantic_normalizer.loop import NormalizationLoop, StaticResolver
from semantic_normalizer.normalizer import SemanticNormalizer
from semantic_normalizer.operators import extract_operator_tokens
from semantic_normalizer.registry import ConceptRegistry


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "concepts.json"


class SemanticNormalizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = ConceptRegistry.from_path(REGISTRY_PATH)
        cls.normalizer = SemanticNormalizer(cls.registry)

    def test_registry_has_only_expected_ambiguity_warnings(self) -> None:
        diagnostics = self.registry.validate()
        self.assertFalse(any(item.severity == "error" for item in diagnostics))
        aliases = {
            item.details["alias"]
            for item in diagnostics
            if item.code == "ambiguous_alias"
        }
        self.assertEqual(aliases, {"remove", "remover", "remova", "removeu"})

    def test_portuguese_source_language_canonicalization(self) -> None:
        result = self.normalizer.normalize(
            "O operador deve começar o servidor APP-01.",
            source_language="pt",
        )
        self.assertEqual(
            result.canonical_text,
            "O operador deve iniciar o servidor APP-01.",
        )
        self.assertEqual(result.status.value, "accepted")
        self.assertEqual(
            result.concept_ids,
            ["role.operator", "action.start", "system.server"],
        )
        self.assertIn("modality__obligation", result.operator_tokens)
        self.assertEqual(result.protected_values, ["APP-01"])
        self.assertEqual(result.quantities, [])

    def test_identifier_code_and_quote_are_not_rewritten(self) -> None:
        text = 'Begin service APP-01 with `remove()` and "begin the service".'
        result = self.normalizer.normalize(text, source_language="en")
        self.assertEqual(result.original_text, text)
        self.assertIn("APP-01", result.canonical_text)
        self.assertIn("`remove()`", result.canonical_text)
        self.assertIn('"begin the service"', result.canonical_text)
        self.assertEqual(
            result.protected_values,
            ["APP-01", "`remove()`", '"begin the service"'],
        )

    def test_physical_remove_uses_context(self) -> None:
        result = self.normalizer.normalize(
            "Remove the panel.",
            source_language="en",
        )
        self.assertEqual(result.status.value, "accepted")
        self.assertIn("action.remove_physical", result.concept_ids)
        self.assertNotIn("action.delete_data", result.concept_ids)
        self.assertEqual(result.canonical_text, "Remove the panel.")

    def test_delete_data_uses_context(self) -> None:
        result = self.normalizer.normalize(
            "Remove the database row.",
            source_language="en",
        )
        self.assertEqual(result.status.value, "accepted")
        self.assertIn("action.delete_data", result.concept_ids)
        self.assertNotIn("action.remove_physical", result.concept_ids)
        self.assertEqual(result.canonical_text, "Delete the database row.")

    def test_surface_form_preserves_source_language_grammar(self) -> None:
        result = self.normalizer.normalize(
            "Remova o painel.",
            source_language="pt",
        )
        self.assertEqual(result.status.value, "accepted")
        self.assertEqual(result.canonical_text, "Remova o painel.")
        action = next(
            mapping for mapping in result.mappings if mapping.concept_id == "action.remove_physical"
        )
        self.assertEqual(action.preferred_label, "remover")
        self.assertEqual(action.canonical_label, "Remova")
        self.assertIn("c__action__remove__physical", result.concept_tokens)

    def test_context_is_local_to_each_occurrence(self) -> None:
        result = self.normalizer.normalize(
            "Remove the panel. Remove the database row.",
            source_language="en",
        )
        self.assertEqual(result.status.value, "accepted")
        self.assertEqual(
            result.canonical_text,
            "Remove the panel. Delete the database row.",
        )
        actions = [
            mapping.concept_id
            for mapping in result.mappings
            if mapping.source_surface.casefold() == "remove"
        ]
        self.assertEqual(actions, ["action.remove_physical", "action.delete_data"])

    def test_same_sentence_context_uses_nearest_object(self) -> None:
        result = self.normalizer.normalize(
            "Remove the panel, then remove the database row.",
            source_language="en",
        )
        self.assertEqual(result.status.value, "accepted")
        self.assertEqual(
            result.canonical_text,
            "Remove the panel, then delete the database row.",
        )
        actions = [
            mapping.concept_id
            for mapping in result.mappings
            if mapping.source_surface.casefold() == "remove"
        ]
        self.assertEqual(actions, ["action.remove_physical", "action.delete_data"])

    def test_ambiguous_remove_abstains(self) -> None:
        result = self.normalizer.normalize("Remove it.", source_language="en")
        self.assertEqual(result.status.value, "review")
        self.assertEqual(result.canonical_text, "Remove it.")
        self.assertEqual(len(result.unresolved_terms), 1)
        self.assertEqual(
            set(result.unresolved_terms[0].candidate_concept_ids),
            {"action.remove_physical", "action.delete_data"},
        )

    def test_static_resolver_can_resolve_existing_candidate(self) -> None:
        loop = NormalizationLoop(
            self.normalizer,
            StaticResolver({"remove": "action.remove_physical"}),
            max_attempts=2,
        )
        result = loop.run("Remove it.", source_language="en")
        self.assertEqual(result.status.value, "accepted")
        self.assertEqual(result.concept_ids, ["action.remove_physical"])
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.mappings[0].method, "resolver_override")

    def test_resolver_cannot_invent_concept(self) -> None:
        loop = NormalizationLoop(
            self.normalizer,
            StaticResolver({"remove": "action.nonexistent"}),
            max_attempts=2,
        )
        result = loop.run("Remove it.", source_language="en")
        self.assertEqual(result.status.value, "review")
        self.assertTrue(
            any(issue.code == "resolver_invented_concept" for issue in result.validation_issues)
        )

    def test_negation_modality_conditions_and_quantities_are_preserved(self) -> None:
        text = "If the service fails, do not restart server APP-01 before 10 min."
        result = self.normalizer.normalize(text, source_language="en")
        self.assertEqual(result.status.value, "accepted")
        self.assertEqual(result.quantities, ["10 min"])
        self.assertEqual(
            extract_operator_tokens(text),
            [
                "condition__if",
                "polarity__negative",
                "condition__before",
            ],
        )
        self.assertFalse(
            any(issue.code == "semantic_operator_changed" for issue in result.validation_issues)
        )

    def test_skos_export_has_multilingual_labels(self) -> None:
        turtle = self.registry.to_skos_turtle("https://example.test/normalizer/")
        self.assertIn('skos:prefLabel "start"@en', turtle)
        self.assertIn('skos:prefLabel "iniciar"@pt', turtle)
        self.assertIn('skos:altLabel "begin"@en', turtle)
        self.assertIn('skos:hiddenLabel "started"@en', turtle)

    def test_elasticsearch_synonym_export_targets_concept_tokens(self) -> None:
        rules = self.registry.to_elasticsearch_synonyms()
        self.assertIn("begin => c__action__start", rules)
        self.assertIn("começar => c__action__start", rules)
        self.assertIn("delete => c__action__delete__data", rules)
        self.assertNotIn("remove =>", rules)
        self.assertNotIn("remover =>", rules)
        self.assertNotIn("remova =>", rules)
        self.assertNotIn("removeu =>", rules)

    def test_synthetic_bm25_expansion_improves_mrr(self) -> None:
        report = evaluate_retrieval(
            normalizer=self.normalizer,
            documents_path=ROOT / "examples" / "documents.jsonl",
            queries_path=ROOT / "examples" / "queries.jsonl",
            k_values=(1, 3),
            target_language="en",
        )
        self.assertEqual(report["modes"]["raw"]["mrr"], 0.0)
        self.assertGreater(
            report["modes"]["canonical"]["mrr"],
            report["modes"]["raw"]["mrr"],
        )
        self.assertGreaterEqual(
            report["modes"]["expanded"]["mrr"],
            report["modes"]["canonical"]["mrr"],
        )

    def test_registry_round_trip_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "registry-copy.json"
            payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            registry = ConceptRegistry.from_path(output)
            self.assertEqual(registry.scheme_id, self.registry.scheme_id)
            self.assertEqual(len(registry.concepts), len(self.registry.concepts))


if __name__ == "__main__":
    unittest.main()
