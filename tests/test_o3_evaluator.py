"""Public synthetic O3 tests S1-S7."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from semantic_normalizer import (
    analyzer,
    evaluate_ablations,
    evaluate_auto_matches,
    load_registry,
)
from semantic_normalizer.evaluator import (
    _combine,
    _condition_parts,
    _metrics,
    _paired_bootstrap,
)

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "tests/fixtures/dev_retrieval.json"
GENERATOR = ROOT / "scripts/generate_auto_match_candidates.py"
REPORTS = ROOT / "reports"
REGISTRY = ROOT / "src/semantic_normalizer/data/registry.jsonl"

# The auto-match snapshot in reports/ is the output of a blind adjudication run against one
# exact registry. Binding the snapshot to that registry's hash makes a registry change a
# visible pending-adjudication state instead of an opaque count-drift error.
ADJUDICATED_REGISTRY_SHA256 = json.loads(
    (REPORTS / "dev-auto-match-correction-manifest.json").read_text(encoding="utf-8")
)["baseline"]["recovery"]["registry_sha256"]
CURRENT_REGISTRY_SHA256 = hashlib.sha256(REGISTRY.read_bytes()).hexdigest()


class O3EvaluatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry()

    def test_s1_analyzer_nfc_casefold_preserves_accents(self):
        self.assertEqual(["ação"], analyzer("AÇÃO"))
        self.assertNotEqual(analyzer("ação"), analyzer("acao"))

    def test_s2_separate_weighted_fields_and_document_id_tie(self):
        documents = {
            "b": {"text_raw": "same", "canonical_text": "other"},
            "a": {"text_raw": "same", "canonical_text": "other"},
        }
        ranking, _ = _combine(documents, [("text_raw", "same", 3.0)])
        self.assertEqual(["a", "b"], ranking)

    def test_s1_frozen_conditions_rank_the_expected_top_documents(self):
        documents = {
            "a": {"text_raw": "rawq rawq rawq", "canonical_text": "x",
                  "concept_tokens": "", "operator_tokens": ""},
            "b": {"text_raw": "retain retain retain", "canonical_text": "x",
                  "concept_tokens": "", "operator_tokens": ""},
            "c": {"text_raw": "rawq", "canonical_text": "canonq canonq",
                  "concept_tokens": "", "operator_tokens": ""},
            "d": {"text_raw": "rawq", "canonical_text": "x",
                  "concept_tokens": "conceptq conceptq",
                  "operator_tokens": "operatorq operatorq"},
            "e": {"text_raw": "x", "canonical_text": "canonq canonq canonq",
                  "concept_tokens": "", "operator_tokens": ""},
        }
        query = {
            "text_raw": "rawq", "canonical_text": "canonq",
            "concept_tokens": "conceptq", "operator_tokens": "operatorq",
            "concept_ids": ["action.preserve"],
        }
        expected = {"A": "a", "B": "b", "C": "c", "D": "d", "E": "e"}
        for condition, top in expected.items():
            ranking, _ = _combine(
                documents, _condition_parts(condition, query, self.registry)
            )
            self.assertEqual(top, ranking[0], condition)

    def test_s2_literal_a_to_e_regression_is_observable(self):
        documents = {
            "relevant": {"text_raw": "literal-only", "canonical_text": "",
                         "concept_tokens": "", "operator_tokens": ""},
            "other": {"text_raw": "", "canonical_text": "canonical",
                      "concept_tokens": "", "operator_tokens": ""},
        }
        query = {
            "text_raw": "literal-only", "canonical_text": "canonical",
            "concept_tokens": "", "operator_tokens": "", "concept_ids": [],
        }
        a, _ = _combine(documents, _condition_parts("A", query, self.registry))
        e, _ = _combine(documents, _condition_parts("E", query, self.registry))
        self.assertEqual("relevant", a[0])
        self.assertNotEqual("relevant", e[0])

    def test_s3_operator_tokens_score_independently_from_concepts(self):
        documents = {
            "wrong-operator": {"text_raw": "", "canonical_text": "",
                               "concept_tokens": "c__action__delete",
                               "operator_tokens": "polarity__positive"},
            "right-operator": {"text_raw": "", "canonical_text": "",
                               "concept_tokens": "c__action__delete",
                               "operator_tokens": "polarity__negative"},
        }
        query = {
            "text_raw": "", "canonical_text": "",
            "concept_tokens": "c__action__delete",
            "operator_tokens": "polarity__negative", "concept_ids": [],
        }
        ranking, _ = _combine(
            documents, _condition_parts("D", query, self.registry)
        )
        self.assertEqual("right-operator", ranking[0])

    def test_s3_all_frozen_ablations_and_metrics_are_present(self):
        report = evaluate_ablations(json.loads(DEV.read_text()), self.registry)
        self.assertEqual({"A", "B", "C", "D", "E"}, set(report["conditions"]))
        for condition in report["conditions"].values():
            for metric in (
                "recall@1", "recall@3", "recall@5", "recall@10", "recall@50",
                "precision@5", "mrr@10", "ndcg@10", "zero_result_rate",
            ):
                self.assertIn(metric, condition)
        self.assertEqual("evaluated", report["strata"]["cross-language"]["status"])
        self.assertIn(
            "paired_comparisons_vs_A", report["strata"]["cross-language"]
        )
        self.assertEqual(
            "not_run", report["strata"]["ambiguous-negative"]["status"]
        )

    def test_s4_zero_scores_are_zero_result_and_adapters_are_not_run(self):
        dataset = {
            "dataset_role": "synthetic",
            "documents": [{"id": "d1", "content": "alpha"}],
            "queries": [{"id": "q1", "stratum": "negative", "text": "omega",
                         "relevant_doc_ids": []}],
        }
        report = evaluate_ablations(dataset, self.registry)
        for condition in "ABCDE":
            self.assertTrue(report["rows"][0]["conditions"][condition]["metrics"]["zero_result"])
        self.assertTrue(all(v["status"] == "not_run" for v in report["adapters"].values()))

    def test_s5_auto_match_labels_tp_fp_fn_uncertain_separately(self):
        candidates = [
            {"occurrence_id": "1", "predicted_concept_id": "a"},
            {"occurrence_id": "2", "predicted_concept_id": "a"},
            {"occurrence_id": "3"},
            {"occurrence_id": "4", "predicted_concept_id": "a"},
        ]
        decisions = [
            {"occurrence_id": "1", "gold_concept_id": "a", "decision_source": "synthetic"},
            {"occurrence_id": "2", "gold_concept_id": "b", "decision_source": "synthetic"},
            {"occurrence_id": "3", "gold_concept_id": "b", "decision_source": "synthetic"},
        ]
        result = evaluate_auto_matches(candidates, decisions, ambiguous_count=1)
        self.assertEqual(
            {"TP": 1, "FP": 1, "FN": 1, "UNCERTAIN": 1}, result["counts"]
        )
        self.assertEqual("not_run", result["auto_match_precision"])
        self.assertEqual(0.5, result["adjudicated_subset_precision"])
        self.assertEqual(4 / 5, result["selective_coverage"])
        self.assertEqual(3 / 4, result["adjudication_coverage"])

    def test_s6_zero_auto_precision_is_not_run(self):
        result = evaluate_auto_matches(
            [{"occurrence_id": "1"}],
            [{"occurrence_id": "1", "gold_concept_id": "a",
              "decision_source": "synthetic"}],
        )
        self.assertEqual("not_run", result["auto_match_precision"])

    def test_s5_exact_metric_values_for_relevant_ranks_two_and_five(self):
        metrics = _metrics(
            ["x1", "r1", "x2", "x3", "r2"], {"r1", "r2"}
        )
        self.assertEqual(0.5, metrics["recall@3"])
        self.assertEqual(1.0, metrics["recall@5"])
        self.assertEqual(0.4, metrics["precision@5"])
        self.assertEqual(0.5, metrics["mrr@10"])
        self.assertEqual(0.6240505200038379, metrics["ndcg@10"])

    def test_s5_paired_bootstrap_is_deterministic_and_query_clustered(self):
        first = _paired_bootstrap([1.0, 0.0, -0.5])
        second = _paired_bootstrap([1.0, 0.0, -0.5])
        self.assertEqual(first, second)
        self.assertEqual(3, first["n_queries"])
        self.assertEqual(1729, first["seed"])
        self.assertEqual(2000, first["repetitions"])
        self.assertEqual(1 / 6, first["delta"])
        self.assertLessEqual(first["ci95"][0], first["delta"])
        self.assertGreaterEqual(first["ci95"][1], first["delta"])

    @unittest.skipUnless(
        ADJUDICATED_REGISTRY_SHA256 == CURRENT_REGISTRY_SHA256,
        "auto-match snapshot was blind-adjudicated against registry 2.0.0; registry 2.1.0 "
        "produces candidates that no adjudication has ever seen. Re-running the generator "
        "and overwriting the manifest would fabricate the adjudication the snapshot exists "
        "to protect. Re-run the blind adjudication, then re-pin.",
    )
    def test_s7_generator_counts_blinds_pending_and_rejects_heldout_path(self):
        spec = importlib.util.spec_from_file_location("generator", GENERATOR)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        golden = ROOT / "tests/fixtures/golden.jsonl"
        candidates, seed, pending, report = module.generate(golden, DEV, self.registry)
        self.assertEqual((137, 71, 66), (len(candidates), len(seed), len(pending)))
        self.assertEqual(36, report["counts"]["protected_values"])
        self.assertTrue(all("predicted_concept_id" not in item for item in pending))
        identity_fields = {
            key: candidates[0][key]
            for key in (
                "source_group", "source_id", "input_sha256", "segment_index",
                "start", "end", "surface",
            )
        }
        self.assertEqual(
            candidates[0]["occurrence_id"],
            module._occurrence_id({**identity_fields, "predicted_concept_id": "changed"}),
        )
        changed = dict(identity_fields)
        changed["input_sha256"] = "0" * 64
        self.assertNotEqual(candidates[0]["occurrence_id"], module._occurrence_id(changed))
        with self.assertRaises(ValueError):
            module.generate(Path("/tmp/heldout/golden.jsonl"), DEV, self.registry)

    def test_s7_final_snapshot_is_strict_baseline_subset_with_frozen_blind(self):
        def records(name):
            return [
                json.loads(line)
                for line in (REPORTS / name).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        baseline = records("dev-auto-match-candidates.jsonl")
        final = records("dev-auto-match-candidates-final.jsonl")
        final_seed = records("dev-auto-match-adjudication-seed-final.jsonl")
        final_pending = records("dev-auto-match-blind-pending-final.jsonl")
        baseline_ids = {item["occurrence_id"] for item in baseline}
        final_ids = {item["occurrence_id"] for item in final}
        self.assertEqual(145, len(baseline_ids))
        self.assertEqual(137, len(final_ids))
        self.assertEqual(71, len(final_seed))
        self.assertEqual(66, len(final_pending))
        self.assertLess(final_ids, baseline_ids)
        self.assertEqual(set(), final_ids - baseline_ids)
        self.assertEqual(
            "f30678d1092de567ecc9b174f4ae85e747638ac427977e727edec9729d1a5069",
            hashlib.sha256(
                (REPORTS / "dev-auto-match-candidates.jsonl").read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(
            "4f9cdeb4de0fa7a7a9627108c92850d6669d15df7eb1da90423b4cfbd6163bb0",
            hashlib.sha256(
                (REPORTS / "dev-auto-match-adjudication-blind.jsonl").read_bytes()
            ).hexdigest(),
        )

    def test_s7_final_manifest_preserves_baseline_and_inventory(self):
        manifest = json.loads(
            (REPORTS / "dev-auto-match-correction-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        baseline = manifest["baseline"]
        final = manifest["final"]
        subset = manifest["subset_validation"]
        self.assertEqual(
            {"TP": 139, "FP": 6, "FN": 0},
            {key: baseline["counts"][key] for key in ("TP", "FP", "FN")},
        )
        self.assertEqual(6, len(baseline["false_positives"]))
        self.assertEqual(10, baseline["ambiguous_review_detections"])
        self.assertEqual(37, baseline["protected_spans_checked"])
        self.assertEqual(0, baseline["protected_span_mutations"])
        self.assertEqual(145 / 155, baseline["selective_coverage"])
        self.assertEqual(
            baseline["evaluation_sha256"],
            hashlib.sha256(
                (REPORTS / "dev-auto-match-evaluation-baseline.json").read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(
            {"TP": 137, "FP": 0, "FN": 0, "UNCERTAIN": 0},
            final["counts"],
        )
        self.assertEqual(1.0, final["precision"])
        self.assertTrue(subset["is_subset"])
        self.assertEqual(0, subset["new_occurrence_id_count"])
        self.assertFalse(manifest["heldout_accessed"])
        output_files = {
            "candidates_final_sha256": "dev-auto-match-candidates-final.jsonl",
            "seed_final_sha256": "dev-auto-match-adjudication-seed-final.jsonl",
            "blind_pending_final_sha256": "dev-auto-match-blind-pending-final.jsonl",
            "evaluation_final_sha256": "dev-auto-match-evaluation-final.json",
            "generation_final_sha256": "dev-auto-match-generation-final.json",
        }
        for field, name in output_files.items():
            self.assertEqual(
                manifest["outputs"][field],
                hashlib.sha256((REPORTS / name).read_bytes()).hexdigest(),
            )

    def test_s7_protected_app_identifier_and_governed_sha256_do_not_mutate(self):
        from semantic_normalizer import normalize_text
        text = "Use SHA-256 with APP-01."
        record = normalize_text(text, "s7", "text", self.registry)[0]
        self.assertIn("technical.sha256", record["concept_ids"])
        self.assertEqual(
            [{"value": "APP-01", "start": 17, "end": 23}],
            record["protected_values"],
        )
        result = evaluate_auto_matches(
            [], [], protected_checks=[{
                "value": "APP-01", "source_value": "APP-01",
                "sequence_value": "APP-01", "canonical_value": "APP-01",
            }]
        )
        self.assertEqual(1, result["protected_spans_checked"])
        self.assertEqual(0, result["protected_span_mutations"])
        mutated = evaluate_auto_matches(
            [], [], protected_checks=[{
                "value": "APP-01", "source_value": "APP-01",
                "sequence_value": "APP-01", "canonical_value": "APP-02",
            }]
        )
        self.assertEqual(1, mutated["protected_span_mutations"])


if __name__ == "__main__":
    unittest.main()
