"""MANIFEST.json must describe the tree that ships.

0.1.0 and 0.2.0 both shipped a MANIFEST declaring a hash for
`reports/validation-summary.md` that never matched the file — wrong from the first commit,
and surviving a hash refresh, because nothing ever recomputed it. This test is that missing
recomputation.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "MANIFEST.json"

# Imported from the generator instead of restated. This file kept its own copy of the two skip
# lists, and the copies drifted the moment `runs/` was excluded from cut_manifest.py: the
# manifest legitimately stopped describing the run directories and the test called that a
# missing file. Two lists that must agree, maintained in two places, is the same defect this
# test exists to catch — one level up.
sys.path.insert(0, str(ROOT / "scripts"))
from cut_manifest import is_tracked  # noqa: E402


def tracked_files() -> set[str]:
    return {
        str(path.relative_to(ROOT)) for path in ROOT.rglob("*") if is_tracked(path)
    }


class ManifestIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_every_declared_hash_matches_the_file(self):
        for entry in self.manifest["files"]:
            with self.subTest(path=entry["path"]):
                path = ROOT / entry["path"]
                self.assertTrue(path.is_file(), f"{entry['path']} is declared but missing")
                data = path.read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
                self.assertEqual(len(data), entry["size_bytes"])

    def test_no_file_is_missing_from_the_manifest(self):
        declared = {entry["path"] for entry in self.manifest["files"]}
        self.assertEqual(set(), tracked_files() - declared)

    def test_manifest_version_matches_the_package(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{self.manifest["version"]}"', pyproject)

    def test_declared_concept_count_matches_the_registry(self):
        registry = ROOT / "src" / "semantic_normalizer" / "data" / "registry.jsonl"
        records = [
            line for line in registry.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        self.assertEqual(len(records), self.manifest["validation"]["registry_concepts"])


if __name__ == "__main__":
    unittest.main()
