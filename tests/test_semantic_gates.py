from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from semantic_normalizer import (
    evaluate_phrase_gold,
    evaluate_rg_gate,
    load_registry,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/semantic_gates.json"
SCRIPT = ROOT / "scripts/evaluate_semantic_gates.py"
REPORT = ROOT / "reports/dev-semantic-gates.json"
ABLATION_FINAL = ROOT / "reports/dev-retrieval-ablations-final.json"
ABLATION_BASELINE = ROOT / "reports/dev-retrieval-ablations-baseline.json"


class SemanticGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry()
        cls.dataset = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_phrase_gold_all_or_nothing_public_gate(self):
        report = evaluate_phrase_gold(
            self.dataset["phrase_gold"], self.registry
        )
        self.assertEqual((9, 9, 8, 8 / 9), (
            report["contract_passed"],
            report["safety_passed"],
            report["semantically_represented"],
            report["coverage"],
        ))
        self.assertTrue(report["majority_pass"])
        self.assertEqual(
            (25, 25, 1.0),
            (
                report["governed_units_matched"],
                report["governed_units_expected"],
                report["governed_unit_coverage"],
            ),
        )
        self.assertEqual((1, 1), (
            report["abstentions_expected"], report["abstentions_passed"]
        ))
        for field in (
            "false_merges", "missing_units", "wrong_roles", "wrong_senses",
            "wrong_relations", "protected_mutations",
        ):
            self.assertEqual([], report[field], field)
        raw = next(
            row for row in report["rows"]
            if row["id"] == "phrase-en-raw-abstain"
        )
        self.assertTrue(raw["contract_passed"])
        self.assertTrue(raw["safety_passed"])
        self.assertFalse(raw["semantically_represented"])
        self.assertEqual(0, raw["actual_governed_units"])
        self.assertFalse(report["heldout_accessed"])

    def test_phrase_diagnostics_detect_each_exact_contract_failure(self):
        dataset = copy.deepcopy(self.dataset["phrase_gold"])
        phrase = dataset["phrases"][0]
        phrase["units"][0]["role"] = "object"
        phrase["units"][1]["sense"] = "Wrong sense."
        phrase["units"][2]["start"] = 21
        phrase["relations"][0]["type"] = "wrong_relation"
        report = evaluate_phrase_gold(dataset, self.registry)
        row = report["rows"][0]
        self.assertFalse(row["contract_passed"])
        self.assertFalse(row["semantically_represented"])
        self.assertTrue(row["diagnostics"]["false_merges"])
        self.assertTrue(row["diagnostics"]["missing_units"])
        self.assertTrue(row["diagnostics"]["wrong_roles"])
        self.assertTrue(row["diagnostics"]["wrong_senses"])
        self.assertTrue(row["diagnostics"]["wrong_relations"])

        protected = copy.deepcopy(self.dataset["phrase_gold"])
        protected["phrases"][7]["protected"][0]["kind"] = "wrong_kind"
        report = evaluate_phrase_gold(protected, self.registry)
        self.assertTrue(report["protected_mutations"])

    def test_phrase_gold_rejects_duplicate_signatures(self):
        dataset = copy.deepcopy(self.dataset["phrase_gold"])
        duplicate = dict(dataset["phrases"][0]["units"][0])
        duplicate["id"] = "duplicate"
        dataset["phrases"][0]["units"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "duplicate gold unit signature"):
            evaluate_phrase_gold(dataset, self.registry)

    def test_dataset_role_metadata_is_derived_for_both_paths(self):
        public = evaluate_phrase_gold(
            self.dataset["phrase_gold"], self.registry
        )
        heldout_dataset = copy.deepcopy(self.dataset["phrase_gold"])
        heldout_dataset["dataset_role"] = "heldout-evaluation"
        heldout = evaluate_phrase_gold(heldout_dataset, self.registry)
        self.assertFalse(public["heldout_accessed"])
        self.assertEqual(
            "public_development_only_not_heldout", public["claim_scope"]
        )
        self.assertTrue(heldout["heldout_accessed"])
        self.assertEqual("heldout_evaluation", heldout["claim_scope"])

        heldout_rg = copy.deepcopy(self.dataset["rg"])
        heldout_rg["dataset_role"] = "heldout-evaluation"
        rg_report = evaluate_rg_gate(heldout_rg, self.registry)
        self.assertTrue(rg_report["heldout_accessed"])
        self.assertEqual("heldout_evaluation", rg_report["claim_scope"])

    @unittest.skipIf(
        shutil.which("rg") is None,
        "rg is not an executable on PATH; evaluate_rg_gate degrades to not_run by design",
    )
    def test_rg_executes_fixed_string_sidecar_gate_and_dash_query(self):
        report = evaluate_rg_gate(self.dataset["rg"], self.registry)
        self.assertEqual("executed", report["status"])
        self.assertTrue(report["executed"])
        self.assertTrue(report["rg_version"].startswith("ripgrep "))
        self.assertEqual(
            ["rg", "-F", "-i", "-l", "--", "<query>", "<corpus-dir>"],
            report["command"],
        )
        self.assertTrue(report["hyphen_query_executed"])
        self.assertEqual([], report["exact_literal_regressions"])
        self.assertEqual(1.0, report["conditions"]["sidecar"]["hit_rate"])
        self.assertEqual(0.0, report["conditions"]["sidecar"]["false_hit_rate"])
        self.assertEqual(1.0, report["conditions"]["sidecar"]["strata"]["literal"]["hit_rate"])
        self.assertEqual(1.0, report["conditions"]["sidecar"]["strata"]["synonym"]["hit_rate"])

    def test_rg_missing_binary_is_explicit_not_run(self):
        with mock.patch(
            "semantic_normalizer.evaluator.shutil.which", return_value=None
        ):
            report = evaluate_rg_gate(self.dataset["rg"], self.registry)
        self.assertEqual("not_run", report["status"])
        self.assertFalse(report["executed"])
        self.assertIn("not found", report["reason"])

    def test_script_is_deterministic_and_rejects_heldout_paths(self):
        first = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, first.returncode, first.stderr)
        first_hashes = (
            hashlib.sha256(REPORT.read_bytes()).hexdigest(),
            hashlib.sha256(ABLATION_FINAL.read_bytes()).hexdigest(),
        )
        second = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(first_hashes, (
            hashlib.sha256(REPORT.read_bytes()).hexdigest(),
            hashlib.sha256(ABLATION_FINAL.read_bytes()).hexdigest(),
        ))
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--semantic-output",
                    str(Path(directory) / "heldout-report.json"),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("heldout path is prohibited", result.stderr)

    def test_reports_pin_current_inputs_and_preserve_baseline(self):
        semantic = json.loads(REPORT.read_text(encoding="utf-8"))
        final = json.loads(ABLATION_FINAL.read_text(encoding="utf-8"))
        self.assertEqual(self.registry["hash"], semantic["reproducibility"]["registry_sha256"])
        self.assertEqual(self.registry["schema_hash"], semantic["reproducibility"]["registry_schema_sha256"])
        self.assertEqual(
            hashlib.sha256(
                (
                    ROOT / "src/semantic_normalizer/data/sidecar.schema.json"
                ).read_bytes()
            ).hexdigest(),
            semantic["reproducibility"]["sidecar_schema_sha256"],
        )
        self.assertEqual(self.registry["hash"], final["reproducibility"]["registry_sha256"])
        self.assertNotIn("latency_ns_observed", ABLATION_FINAL.read_text(encoding="utf-8"))
        self.assertEqual(
            "e47e76eade772de9379c11612870fe2f90e3832649d30047196afe566d6ba390",
            hashlib.sha256(ABLATION_BASELINE.read_bytes()).hexdigest(),
        )
        self.assertFalse(semantic["heldout_accessed"])
        self.assertFalse(final["heldout_accessed"])


if __name__ == "__main__":
    unittest.main()
