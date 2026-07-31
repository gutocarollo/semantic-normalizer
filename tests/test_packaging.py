from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path, PurePosixPath

SOURCE_ROOT = Path(__file__).resolve().parents[1]
ROOT = SOURCE_ROOT
BUILD = ROOT / "scripts" / "build_release.py"
WHEEL = ROOT / "dist" / "semantic_normalizer-0.4.0-py3-none-any.whl"
SKILL_ZIP = ROOT / "dist" / "semantic-normalizer-skill-0.4.0.zip"
REPORT = ROOT / "reports" / "package-validation.json"
MANIFEST = ROOT / "reports" / "release-manifest.json"
CHECKSUMS = ROOT / "checksums.sha256"


class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global ROOT, BUILD, WHEEL, SKILL_ZIP, REPORT, MANIFEST, CHECKSUMS
        cls.temporary = tempfile.TemporaryDirectory(
            prefix="semantic-normalizer-packaging-test-"
        )
        ROOT = Path(cls.temporary.name) / "semantic-normalizer"
        shutil.copytree(
            SOURCE_ROOT,
            ROOT,
            ignore=shutil.ignore_patterns(
                "dist",
                "__pycache__",
                "*.pyc",
                "package-validation.json",
                "release-manifest.json",
                "checksums.sha256",
            ),
        )
        BUILD = ROOT / "scripts" / "build_release.py"
        WHEEL = ROOT / "dist" / "semantic_normalizer-0.4.0-py3-none-any.whl"
        SKILL_ZIP = ROOT / "dist" / "semantic-normalizer-skill-0.4.0.zip"
        REPORT = ROOT / "reports" / "package-validation.json"
        MANIFEST = ROOT / "reports" / "release-manifest.json"
        CHECKSUMS = ROOT / "checksums.sha256"
        cls.first = subprocess.run(
            [sys.executable, str(BUILD), "--json"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if cls.first.returncode:
            raise AssertionError(cls.first.stderr)
        cls.first_hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (WHEEL, SKILL_ZIP, REPORT, MANIFEST, CHECKSUMS)
        }
        cls.second = subprocess.run(
            [sys.executable, str(BUILD), "--json"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if cls.second.returncode:
            raise AssertionError(cls.second.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_01_build_script_reports_offline_reproducible_outputs(self):
        payload = json.loads(self.second.stdout)
        self.assertTrue(payload["offline"])
        self.assertTrue(payload["reproducible"])
        self.assertTrue(WHEEL.is_file())
        self.assertTrue(SKILL_ZIP.is_file())

    def test_02_full_build_reexecution_is_byte_identical(self):
        second_hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (WHEEL, SKILL_ZIP, REPORT, MANIFEST, CHECKSUMS)
        }
        self.assertEqual(self.first_hashes, second_hashes)

    def test_03_wheel_metadata_record_and_zip_are_canonical(self):
        with zipfile.ZipFile(WHEEL) as archive:
            infos = [item for item in archive.infolist() if not item.is_dir()]
            self.assertEqual(infos, sorted(infos, key=lambda item: item.filename))
            self.assertTrue(all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in infos))
            self.assertTrue(all(item.compress_type == zipfile.ZIP_STORED for item in infos))
            metadata = archive.read(
                "semantic_normalizer-0.4.0.dist-info/METADATA"
            ).decode()
            self.assertIn("Requires-Python: >=3.14", metadata)
            record_name = "semantic_normalizer-0.4.0.dist-info/RECORD"
            rows = list(csv.reader(io.StringIO(archive.read(record_name).decode())))
            self.assertEqual(
                {row[0] for row in rows},
                {item.filename for item in infos},
            )

    def test_04_source_data_equals_wheel_members(self):
        data = ROOT / "src" / "semantic_normalizer" / "data"
        with zipfile.ZipFile(WHEEL) as archive:
            for source in sorted(data.iterdir()):
                if source.is_file():
                    self.assertEqual(
                        source.read_bytes(),
                        archive.read(f"semantic_normalizer/data/{source.name}"),
                    )
        report = json.loads(REPORT.read_text())
        reported = report["wheel"]["governed_data_sha256"]
        self.assertEqual(
            {
                source.name: hashlib.sha256(source.read_bytes()).hexdigest()
                for source in sorted(data.iterdir())
                if source.is_file() and source.suffix != ".py"
            },
            reported,
        )
        self.assertIn("reconciliation-decision.schema.json", reported)
        self.assertIn("downstream-eval-config-v2.json", reported)
        self.assertTrue({
            "phrase-gold-independent.schema.json",
            "phrase-sense-crosswalk.schema.json",
            "heldout-retrieval.schema.json",
            "heldout-rg.schema.json",
            "heldout-downstream.schema.json",
            "heldout-manifest.schema.json",
            "downstream-answerer-packet.schema.json",
            "downstream-answerer-output.schema.json",
            "downstream-judge-packet.schema.json",
            "downstream-judge-output.schema.json",
        }.issubset(reported))

    def test_05_offline_install_and_runtime_evidence_passed(self):
        report = json.loads(REPORT.read_text())
        self.assertEqual("PASS", report["offline_install"]["pip_install"])
        self.assertEqual("No broken requirements found.", report["offline_install"]["pip_check"])
        self.assertTrue(report["offline_install"]["console_help"])
        self.assertTrue(report["offline_install"]["site_packages_unchanged"])
        self.assertEqual([], report["offline_install"]["site_packages_changed_files"])
        self.assertGreater(report["offline_install"]["site_packages_files_compared"], 0)
        self.assertTrue(report["offline_install"]["installed_data_equal_source"])
        public_contract_smoke = report["offline_install"]["public_contract_smoke"]
        self.assertGreater(public_contract_smoke["registry_records"], 0)
        self.assertTrue(public_contract_smoke["release_governs_registry"])
        self.assertGreater(public_contract_smoke["provenance_events"], 0)
        self.assertTrue(public_contract_smoke["source_preserved"])
        self.assertTrue(public_contract_smoke["identifier_protected"])
        # The 0.4.0 invariant: the installed wheel must not accept unknown vocabulary.
        self.assertNotEqual("accepted", public_contract_smoke["unknown_vocabulary_status"])
        self.assertEqual([
            "registry.schema.json",
            "sidecar.schema.json",
            "reconciliation-request.schema.json",
            "reconciliation-response.schema.json",
            "reconciliation-decision.schema.json",
        ], public_contract_smoke["schemas"])
        canonical_example = report["offline_install"]["canonical_example"]
        # 2.1.0 shipped action.start, so the installed wheel must resolve it...
        self.assertTrue(canonical_example["resolves_action_start"])
        # ...and must still not invent a concept the registry never defined.
        self.assertFalse(canonical_example["invented_entity_server"])
        self.assertEqual("APP-01", canonical_example["protected_value"])
        reconciliation = report["offline_install"]["reconciliation"]
        self.assertEqual(
            ["entity.facility_base", "technical.numeral_base"],
            reconciliation["allowed_candidates"],
        )
        self.assertEqual("accepted", reconciliation["state"])
        self.assertEqual(1, reconciliation["candidate_ledger_records"])
        self.assertEqual(1, reconciliation["decision_ledger_records"])
        self.assertTrue(reconciliation["workspace_external_to_site_packages"])
        self.assertTrue(reconciliation["registry_seed_unchanged"])
        self.assertEqual([], reconciliation["mutable_ledgers_in_site_packages"])

    def test_06_skill_zip_has_one_prefix_and_no_forbidden_payload(self):
        with zipfile.ZipFile(SKILL_ZIP) as archive:
            names = [PurePosixPath(item.filename) for item in archive.infolist()]
        self.assertEqual({"semantic-normalizer-0.4.0"}, {path.parts[0] for path in names})
        forbidden = {"heldout", "__pycache__", ".venv", "candidates.jsonl", "decisions.jsonl"}
        self.assertFalse(any(forbidden.intersection(path.parts) for path in names))
        self.assertFalse(any(
            "heldout" in {part.casefold() for part in path.parts} for path in names
        ))
        self.assertFalse(any(str(path).endswith(".pyc") for path in names))
        basenames = {path.name for path in names}
        self.assertIn("schema_validation.py", basenames)
        self.assertIn("normalizer.py", basenames)
        self.assertIn("registry.py", basenames)
        self.assertIn("reconciliation.py", basenames)
        self.assertIn("evaluator.py", basenames)
        self.assertTrue({
            "phrase-gold-independent.schema.json",
            "phrase-sense-crosswalk.schema.json",
            "heldout-retrieval.schema.json",
            "heldout-rg.schema.json",
            "heldout-downstream.schema.json",
            "heldout-manifest.schema.json",
            "downstream-eval-config-v2.json",
            "downstream-answerer-packet.schema.json",
            "downstream-answerer-output.schema.json",
            "downstream-judge-packet.schema.json",
            "downstream-judge-output.schema.json",
        }.issubset(basenames))
        self.assertIn("reconciliation-decision.schema.json", basenames)
        self.assertIn("test_runtime_contracts.py", basenames)
        self.assertIn("test_registry_governance.py", basenames)
        self.assertIn("test_controlled_language.py", basenames)
        self.assertIn(
            PurePosixPath(
                "semantic-normalizer-0.4.0/tests/test_registry_governance.py"
            ),
            names,
        )
        self.assertIn(
            PurePosixPath(
                "semantic-normalizer-0.4.0/tests/test_registry_governance.py"
            ),
            names,
        )
        excluded_report_ledgers = {
            "dev-auto-match-adjudication-blind.jsonl",
            "dev-auto-match-adjudication-seed-final.jsonl",
            "dev-auto-match-adjudication-seed.jsonl",
            "dev-auto-match-blind-pending-final.jsonl",
            "dev-auto-match-blind-pending.jsonl",
            "dev-auto-match-candidates-final.jsonl",
            "dev-auto-match-candidates.jsonl",
        }
        self.assertTrue(excluded_report_ledgers.isdisjoint(basenames))
        expected_summaries = {
            "dev-auto-match-evaluation-baseline.json",
            "dev-auto-match-evaluation-final.json",
            "dev-retrieval-ablations-baseline.json",
            "dev-retrieval-ablations-final.json",
            "dev-semantic-gates.json",
        }
        self.assertTrue(expected_summaries.issubset(basenames))

    def test_07_internal_manifest_avoids_hash_cycles(self):
        manifest = json.loads(MANIFEST.read_text())
        listed = {item["path"] for item in manifest["payload"]}
        self.assertNotIn("reports/release-manifest.json", listed)
        self.assertNotIn("checksums.sha256", listed)
        self.assertFalse(any(path.endswith(".zip") for path in listed))
        with zipfile.ZipFile(SKILL_ZIP) as archive:
            prefix = manifest["archive_prefix"]
            actual = {
                str(PurePosixPath(item.filename).relative_to(prefix))
                for item in archive.infolist()
                if not item.is_dir()
                and item.filename != f"{prefix}/reports/release-manifest.json"
            }
            self.assertEqual(listed, actual)
            for item in manifest["payload"]:
                data = archive.read(f"{prefix}/{item['path']}")
                self.assertEqual(item["sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(item["size"], len(data))

    def test_08_external_checksums_verify(self):
        for line in CHECKSUMS.read_text().splitlines():
            expected, relative = line.split("  ", 1)
            actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(expected, actual, relative)

    def test_09_package_report_records_literal_offline_commands(self):
        report = json.loads(REPORT.read_text())
        commands = report["commands"]
        self.assertTrue(any("wheel pack" in command for command in commands))
        self.assertTrue(any("PIP_NO_INDEX=1" in command and "--no-index" in command for command in commands))
        self.assertTrue(any("validate-registry" in command for command in commands))
        self.assertTrue(any("init-workspace" in command for command in commands))
        self.assertTrue(any("reconcile-request" in command for command in commands))
        self.assertTrue(any("reconcile-apply" in command for command in commands))
        self.assertTrue(any(
            "public-contract-smoke-without-heldout-corpus" in command
            for command in commands
        ))

    def test_10_pyproject_is_the_package_data_authority(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(
            ["data/*.json", "data/*.jsonl"],
            config["tool"]["semantic-normalizer"]["package-data"]["include"],
        )
        with zipfile.ZipFile(WHEEL) as archive:
            names = {item.filename for item in archive.infolist()}
        self.assertIn(
            "semantic_normalizer/data/reconciliation-decision.schema.json", names
        )
        self.assertIn("semantic_normalizer/schema_validation.py", names)
        self.assertTrue({
            "semantic_normalizer/normalizer.py",
            "semantic_normalizer/registry.py",
            "semantic_normalizer/reconciliation.py",
            "semantic_normalizer/schema_validation.py",
            "semantic_normalizer/data/registry.jsonl",
            "semantic_normalizer/data/registry.schema.json",
            "semantic_normalizer/data/registry.release.json",
            "semantic_normalizer/data/registry.provenance.jsonl",
            "semantic_normalizer/data/sidecar.schema.json",
        }.issubset(names))
        self.assertNotIn(
            "semantic_normalizer/tests/test_downstream_evaluation_v2.py", names
        )
        self.assertNotIn("tests/test_downstream_evaluation_v2.py", names)
        self.assertNotIn(
            "semantic_normalizer/tests/test_registry_governance.py", names
        )
        self.assertNotIn("tests/test_registry_governance.py", names)
        self.assertFalse(any(
            {"tests", "test", "fixtures", "reports", "heldout"}.intersection(
                PurePosixPath(name).parts
            )
            for name in names
        ))

    def test_11_registry_release_governs_downstream_and_current_sidecar(self):
        release = json.loads(
            (
                ROOT
                / "src"
                / "semantic_normalizer"
                / "data"
                / "registry.release.json"
            ).read_text(encoding="utf-8")
        )
        hashes = release["hashes"]
        expected = {
            "sidecar.schema.json":
                "6bceb909ca83e56313a21a59eb358581203c464d9825b29543573c978422489c",
            "downstream-eval-config-v2.json":
                "918866786f4209d88aee52e77383b02d9a44ef8e815ed77b9496e9ed1106578e",
            "heldout-retrieval.schema.json":
                "ff3a072c435b3134a201a864cbba72b9f5f24c6bfead0231515087a16694edbd",
            "heldout-rg.schema.json":
                "941c47d82c7bf430382cda313a730339b8c6f4165b432d5a6749766fda842866",
            "downstream-answerer-packet.schema.json":
                "acae5b0bf874a2d5194ea14c9f5e95a29bbd824b2a3fb80ce77654db686e2b33",
            "downstream-answerer-output.schema.json":
                "c5c626dc345ab38e1bac36b9bb0d1b04862dfe7b43c66721d708314def4b91c1",
            "downstream-judge-packet.schema.json":
                "9e12c4f94855074cda8a1bc201bb31677a71cd6705be34cc633f1937783b6303",
            "downstream-judge-output.schema.json":
                "4769dfc32efac7a9f211aba93004ecb2b566584f83d4837bf1850a8bdf82f0b5",
        }
        self.assertEqual(expected, {name: hashes[name] for name in expected})
        data = ROOT / "src" / "semantic_normalizer" / "data"
        self.assertEqual(
            expected,
            {
                name: hashlib.sha256((data / name).read_bytes()).hexdigest()
                for name in expected
            },
        )

if __name__ == "__main__":
    unittest.main()
