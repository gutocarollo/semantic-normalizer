from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [sys.executable, "-m", "semantic_normalizer", *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_validate_registry(self) -> None:
        process = self.run_cli("validate-registry")
        self.assertEqual(process.returncode, 0, process.stderr)
        payload = json.loads(process.stdout)
        self.assertEqual(payload["concept_count"], 21)

    def test_strict_returns_two_for_review(self) -> None:
        process = self.run_cli(
            "normalize",
            "--text",
            "Remove it.",
            "--lang",
            "en",
            "--strict",
        )
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["status"], "review")

    def test_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            skos = directory_path / "concepts.ttl"
            synonyms = directory_path / "synonyms.txt"
            process_skos = self.run_cli("export-skos", "--output", str(skos))
            process_synonyms = self.run_cli("export-synonyms", "--output", str(synonyms))
            self.assertEqual(process_skos.returncode, 0, process_skos.stderr)
            self.assertEqual(process_synonyms.returncode, 0, process_synonyms.stderr)
            self.assertTrue(skos.exists())
            self.assertTrue(synonyms.exists())
            self.assertIn("skos:ConceptScheme", skos.read_text(encoding="utf-8"))
            self.assertIn("c__action__start", synonyms.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
