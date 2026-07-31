from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import concurrent.futures
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from semantic_normalizer import ContractError, apply_response, load_registry, make_request
from semantic_normalizer.normalizer import normalize_text
from semantic_normalizer.schema_validation import validate_sidecar_record


class RuntimeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry()

    def cli(self, *arguments):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "controlled_language.py"), *map(str, arguments)],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_sidecar_schema_matches_en_pt_ambiguity_and_protection(self):
        cases = [
            ("Retain the file.", "text"),
            ("Preserve o arquivo.", "text"),
            ("Check the base.", "text"),
            ("Use SHA-256 with APP-01.", "text"),
            ("Preserve `delete file`.", "markdown"),
        ]
        for text, kind in cases:
            for record in normalize_text(text, "<test>", kind, self.registry):
                self.assertIs(record, validate_sidecar_record(record))

    def test_sidecar_schema_rejects_extra_property_and_wrong_type(self):
        record = normalize_text("Retain the file.", "<test>", "text", self.registry)[0]
        extra = copy.deepcopy(record)
        extra["invented"] = True
        with self.assertRaisesRegex(ContractError, "unexpected properties"):
            validate_sidecar_record(extra)
        wrong_type = copy.deepcopy(record)
        wrong_type["line"] = "1"
        with self.assertRaisesRegex(ContractError, "invalid type"):
            validate_sidecar_record(wrong_type)

    def test_programmatic_states_are_bound_to_registry_occurrence(self):
        request1 = make_request(
            "Check the base.", 10, 14, "en", attempt=1, registry=self.registry
        )
        self.assertEqual(
            ["entity.facility_base", "technical.numeral_base"],
            request1["allowed_candidates"],
        )
        accepted = apply_response(request1, {
            "candidate_id": "entity.facility_base",
            "confidence": 0.9,
            "evidence_span": {"start": 10, "end": 14, "text": "base"},
            "reason_code": "CONTEXT_DISAMBIGUATED",
        }, self.registry)
        self.assertEqual("accepted", accepted.state)
        abstain1 = apply_response(request1, {
            "candidate_id": "ABSTAIN",
            "confidence": 0,
            "evidence_span": {"start": 10, "end": 14, "text": "base"},
            "reason_code": "INSUFFICIENT_CONTEXT",
        }, self.registry)
        self.assertEqual("unresolved", abstain1.state)
        request2 = make_request(
            "Check the base.", 10, 14, "en", attempt=2, registry=self.registry
        )
        review = apply_response(request2, {
            "candidate_id": "ABSTAIN",
            "confidence": 0,
            "evidence_span": {"start": 10, "end": 14, "text": "base"},
            "reason_code": "CONFLICTING_EVIDENCE",
        }, self.registry)
        rejected = apply_response(request2, {
            "candidate_id": "ABSTAIN",
            "confidence": 0,
            "evidence_span": {"start": 10, "end": 14, "text": "base"},
            "reason_code": "NO_ALLOWED_CANDIDATE",
        }, self.registry)
        self.assertEqual("review", review.state)
        self.assertEqual("rejected", rejected.state)

    def test_workspace_ledgers_forgery_drift_duplicate_and_attempt_limit(self):
        package_data = ROOT / "src" / "semantic_normalizer" / "data"
        before = {path.name: path.read_bytes() for path in package_data.iterdir() if path.is_file()}
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            initialized = self.cli("init-workspace", workspace)
            self.assertEqual(0, initialized.returncode, initialized.stderr)

            first = self.cli(
                "reconcile-request", "--workspace", workspace,
                "--context", "Check the base.", "--start", 10, "--end", 14,
                "--language", "en",
            )
            self.assertEqual(0, first.returncode, first.stderr)
            request = json.loads(first.stdout)
            self.assertEqual(
                ["entity.facility_base", "technical.numeral_base"],
                request["allowed_candidates"],
            )
            request_path = Path(directory) / "request.json"
            response_path = Path(directory) / "response.json"
            request_path.write_text(first.stdout, encoding="utf-8")
            response_path.write_text(json.dumps({
                "candidate_id": "entity.facility_base",
                "confidence": 0.9,
                "evidence_span": {"start": 10, "end": 14, "text": "base"},
                "reason_code": "CONTEXT_DISAMBIGUATED",
            }), encoding="utf-8")
            apply_args = (
                "reconcile-apply", "--workspace", workspace,
                "--request", request_path, "--response", response_path,
                "--reviewer", "reviewer@example.test",
                "--rationale", "facility context was confirmed",
                "--protected-slot-comparison", "not-applicable",
                "--timestamp", "2026-07-30T12:00:00Z",
            )
            applied = self.cli(*apply_args)
            self.assertEqual(0, applied.returncode, applied.stderr)
            decision = json.loads(applied.stdout)
            self.assertEqual("accepted", decision["result"]["state"])
            self.assertEqual(1, len((workspace / "candidates.jsonl").read_text().splitlines()))
            self.assertEqual(1, len((workspace / "decisions.jsonl").read_text().splitlines()))
            duplicate = self.cli(*apply_args)
            self.assertEqual(2, duplicate.returncode)

            forged = dict(request)
            forged["allowed_candidates"] = ["entity.file"]
            forged_path = Path(directory) / "forged.json"
            forged_path.write_text(json.dumps(forged), encoding="utf-8")
            forged_result = self.cli(
                "reconcile-apply", "--workspace", workspace,
                "--request", forged_path, "--response", response_path,
                "--reviewer", "reviewer@example.test", "--rationale", "forged",
                "--protected-slot-comparison", "not-applicable",
            )
            self.assertEqual(2, forged_result.returncode)

            second = self.cli(
                "reconcile-request", "--workspace", workspace,
                "--context", "Check the base.", "--start", 10, "--end", 14,
                "--language", "en",
            )
            self.assertEqual(2, second.returncode)
            self.assertIn("terminal reconciliation result", second.stderr)

            retry_workspace = Path(directory) / "retry-workspace"
            self.assertEqual(0, self.cli("init-workspace", retry_workspace).returncode)
            attempt1 = self.cli(
                "reconcile-request", "--workspace", retry_workspace,
                "--context", "Check the base.", "--start", 10, "--end", 14,
                "--language", "en",
            )
            attempt1_path = Path(directory) / "attempt1.json"
            abstain_path = Path(directory) / "abstain.json"
            attempt1_path.write_text(attempt1.stdout, encoding="utf-8")
            abstain_path.write_text(json.dumps({
                "candidate_id": "ABSTAIN",
                "confidence": 0,
                "evidence_span": {"start": 10, "end": 14, "text": "base"},
                "reason_code": "INSUFFICIENT_CONTEXT",
            }), encoding="utf-8")
            unresolved = self.cli(
                "reconcile-apply", "--workspace", retry_workspace,
                "--request", attempt1_path, "--response", abstain_path,
                "--reviewer", "reviewer@example.test", "--rationale", "insufficient",
                "--protected-slot-comparison", "not-applicable",
            )
            self.assertEqual(0, unresolved.returncode, unresolved.stderr)
            attempt2 = self.cli(
                "reconcile-request", "--workspace", retry_workspace,
                "--context", "Check the base.", "--start", 10, "--end", 14,
                "--language", "en",
            )
            self.assertEqual(0, attempt2.returncode, attempt2.stderr)
            third = self.cli(
                "reconcile-request", "--workspace", retry_workspace,
                "--context", "Check the base.", "--start", 10, "--end", 14,
                "--language", "en",
            )
            self.assertEqual(2, third.returncode)

            nonexistent = self.cli(
                "reconcile-request", "--workspace", workspace,
                "--context", "Check the file.", "--start", 10, "--end", 14,
                "--language", "en",
            )
            self.assertEqual(2, nonexistent.returncode)

        after = {path.name: path.read_bytes() for path in package_data.iterdir() if path.is_file()}
        self.assertEqual(before, after)

    def test_workspace_lock_serializes_concurrent_request_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            self.assertEqual(0, self.cli("init-workspace", workspace).returncode)

            def create():
                return self.cli(
                    "reconcile-request", "--workspace", workspace,
                    "--context", "Check the base.", "--start", 10, "--end", 14,
                    "--language", "en",
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: create(), range(2)))
            self.assertEqual([0, 2], sorted(item.returncode for item in results))
            self.assertEqual(
                1, len((workspace / "candidates.jsonl").read_text().splitlines())
            )
            self.assertTrue((workspace / ".reconciliation.lock").is_file())

    def test_language_and_cross_ledger_tampering_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            self.assertEqual(0, self.cli("init-workspace", workspace).returncode)
            wrong_language = self.cli(
                "reconcile-request", "--workspace", workspace,
                "--context", "Check the base.", "--start", 10, "--end", 14,
                "--language", "pt-BR",
            )
            self.assertEqual(2, wrong_language.returncode)
            self.assertIn("language differs", wrong_language.stderr)

            created = self.cli(
                "reconcile-request", "--workspace", workspace,
                "--context", "Check the base.", "--start", 10, "--end", 14,
                "--language", "en",
            )
            request_path = Path(directory) / "request.json"
            response_path = Path(directory) / "response.json"
            request_path.write_text(created.stdout, encoding="utf-8")
            response_path.write_text(json.dumps({
                "candidate_id": "ABSTAIN",
                "confidence": 0,
                "evidence_span": {"start": 10, "end": 14, "text": "base"},
                "reason_code": "INSUFFICIENT_CONTEXT",
            }), encoding="utf-8")
            applied = self.cli(
                "reconcile-apply", "--workspace", workspace,
                "--request", request_path, "--response", response_path,
                "--reviewer", "reviewer@example.test", "--rationale", "insufficient",
                "--protected-slot-comparison", "not-applicable",
                "--timestamp", "2026-07-30T12:00:00Z",
            )
            self.assertEqual(0, applied.returncode, applied.stderr)
            decision_path = workspace / "decisions.jsonl"
            decision = json.loads(decision_path.read_text())
            decision["request_sha256"] = "0" * 64
            decision_path.write_text(json.dumps(decision) + "\n", encoding="utf-8")
            retry = self.cli(
                "reconcile-request", "--workspace", workspace,
                "--context", "Check the base.", "--start", 10, "--end", 14,
                "--language", "en",
            )
            self.assertEqual(2, retry.returncode)
            self.assertIn("decision_id differs", retry.stderr)

    def test_registry_drift_rejects_previously_bound_request(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            self.assertEqual(0, self.cli("init-workspace", workspace).returncode)
            created = self.cli(
                "reconcile-request", "--workspace", workspace,
                "--context", "Check the base.", "--start", 10, "--end", 14,
                "--language", "en",
            )
            self.assertEqual(0, created.returncode, created.stderr)
            request_path = Path(directory) / "request.json"
            response_path = Path(directory) / "response.json"
            request_path.write_text(created.stdout, encoding="utf-8")
            response_path.write_text(json.dumps({
                "candidate_id": "ABSTAIN",
                "confidence": 0,
                "evidence_span": {"start": 10, "end": 14, "text": "base"},
                "reason_code": "INSUFFICIENT_CONTEXT",
            }), encoding="utf-8")
            registry_path = workspace / "registry.jsonl"
            registry_path.write_bytes(registry_path.read_bytes().replace(b"\n", b" \n", 1))
            drifted = self.cli(
                "reconcile-apply", "--workspace", workspace,
                "--request", request_path, "--response", response_path,
                "--reviewer", "reviewer@example.test", "--rationale", "abstain",
                "--protected-slot-comparison", "not-applicable",
            )
            self.assertEqual(2, drifted.returncode)
            self.assertIn("registry binding differs", drifted.stderr)


if __name__ == "__main__":
    unittest.main()
