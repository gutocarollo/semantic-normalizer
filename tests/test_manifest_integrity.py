"""MANIFEST.json must describe the tree that ships.

0.1.0 and 0.2.0 both shipped a MANIFEST declaring a hash for
`reports/validation-summary.md` that never matched the file — wrong from the first commit,
and surviving a hash refresh, because nothing ever recomputed it. This test is that missing
recomputation.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "MANIFEST.json"
SKIP_DIRS = {".git", "__pycache__", "dist", "exports", ".venv", ".pytest_cache"}
SKIP_NAMES = {"MANIFEST.json", ".DS_Store", ".manifest.before"}


def tracked_files() -> set[str]:
    found = set()
    for path in ROOT.rglob("*"):
        if path.is_dir() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_NAMES or path.suffix == ".pyc":
            continue
        found.add(str(path.relative_to(ROOT)))
    return found


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
