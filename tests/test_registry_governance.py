"""The release record and the provenance ledger must describe the registry that ships.

`test_packaging.py` used to enforce part of this; it was dropped with the held-out custody
machinery in 0.3.0 because its `test_12` required the removed held-out evaluator. Without a
replacement, `registry.release.json` would be an unverified claim — which is exactly the defect
the 0.2.0 review found in `MANIFEST.json`, whose declared hash never matched its own file.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "src" / "semantic_normalizer" / "data"
REGISTRY = DATA / "registry.jsonl"
RELEASE = DATA / "registry.release.json"
PROVENANCE = DATA / "registry.provenance.jsonl"
SCHEMA = DATA / "registry.schema.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RegistryGovernanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.records = [
            json.loads(line)
            for line in REGISTRY.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        cls.release = json.loads(RELEASE.read_text(encoding="utf-8"))
        cls.provenance = [
            json.loads(line)
            for line in PROVENANCE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_declared_hashes_match_the_shipped_files(self):
        for name, path in (
            ("registry.jsonl", REGISTRY),
            ("registry.schema.json", SCHEMA),
            ("registry.provenance.jsonl", PROVENANCE),
        ):
            with self.subTest(file=name):
                self.assertIn(name, self.release["hashes"])
                self.assertEqual(
                    sha256(path),
                    self.release["hashes"][name],
                    f"{name}: release record declares a hash that does not match the file",
                )

    def test_every_declared_hash_in_the_release_matches_its_file(self):
        """The seal must cover everything it names, not the three files the sealer edits.

        `amend_registry.py` used to carry unchanged entries forward verbatim, so a file touched
        by any other path kept its old hash forever. An adversarial review found
        `heldout-downstream.schema.json` stale in a shipped release and no test failing, because
        the check above only covers the same three files the sealer rewrote. A seal that is only
        verified where it is written verifies nothing.
        """
        roots = (DATA, DATA.parents[2], DATA.parent)
        for name, declared in sorted(self.release.get("hashes", {}).items()):
            with self.subTest(file=name):
                path = next((root / name for root in roots if (root / name).is_file()), None)
                self.assertIsNotNone(path, f"{name}: release seals a file that does not exist")
                self.assertEqual(
                    sha256(path), declared,
                    f"{name}: release record declares a hash that does not match the file",
                )

    def test_release_version_matches_every_record(self):
        versions = {record["registry_version"] for record in self.records}
        self.assertEqual({self.release["version"]}, versions)

    def test_affected_concepts_is_the_registry(self):
        self.assertEqual(
            sorted(record["concept_id"] for record in self.records),
            sorted(self.release["affected_concepts"]),
        )

    def test_added_concepts_are_present_and_not_retired(self):
        live = {record["concept_id"] for record in self.records}
        for concept_id in self.release["added_concepts"]:
            with self.subTest(concept=concept_id):
                self.assertIn(concept_id, live)

    def test_rollback_version_is_a_real_predecessor(self):
        recorded = {event["target"]["registry_version"] for event in self.provenance}
        self.assertIn(self.release["rollback_version"], recorded | {"1.0.0"})

    def test_provenance_ends_at_the_shipped_version(self):
        last = self.provenance[-1]
        self.assertEqual(self.release["version"], last["target"]["registry_version"])
        self.assertEqual(len(self.records), last["target"]["record_count"])

    def test_every_provenance_event_declares_source_and_time(self):
        for event in self.provenance:
            with self.subTest(event=event["event_id"]):
                self.assertTrue(event.get("source"))
                self.assertIn("recorded_at", event)

    def test_current_import_declares_authority_and_license(self):
        """Events written from 2.1.0 on carry the governance fields 0.2.0 only promised.

        The two pre-2.1.0 seed events predate the requirement and are not rewritten: a
        provenance ledger is append-only, so back-filling them would be the forgery it exists
        to prevent.
        """
        event = self.provenance[-1]
        self.assertTrue(str(event.get("license", "")).strip())
        self.assertTrue(str(event.get("authority", "")).strip())
        self.assertTrue(str(event.get("importer", "")).strip())

    def test_provenance_event_ids_are_unique(self):
        """Append-only: an event id may never be rewritten in place.

        The importer previously filtered its own event out and re-added it, so a rerun
        silently rewrote `added_records: 13` to `0`. Duplicate or rewritten ids are the
        observable symptom; the importer now refuses the rerun outright.
        """
        ids = [event["event_id"] for event in self.provenance]
        self.assertEqual(len(ids), len(set(ids)))

    def test_provenance_record_counts_only_grow(self):
        counts = [event["target"]["record_count"] for event in self.provenance]
        self.assertEqual(sorted(counts), counts, f"record_count went backwards: {counts}")

    def test_authority_of_domain_concepts_uses_the_closed_set(self):
        """Plan D1 encodes verification state in `authority`, so the set must be closed.

        The schema has `additionalProperties: false`, so a dedicated field was not an option
        and the state rides in a string. A convention without a test is a suggestion; this is
        the enum, implemented where it fits. Scope is the CGA domain batch — the 86 concepts
        that predate D1 carry free-form authorities and are not retro-fitted.
        """
        prefixes = ("apostila-cga-2026", "openwordnet-pt:", "project-authored:")
        offenders = [
            record["concept_id"]
            for record in self.records
            if "cga" in record["domains"] and not record["authority"].startswith(prefixes)
        ]
        self.assertEqual([], offenders)

    def test_apostila_anchors_name_a_file_that_exists(self):
        """A prefix check is not a citation check.

        An adversarial review found all fifty concepts of the precision campaign citing chapter
        slugs that do not exist — `08-matematica-financeira` where the corpus has
        `08-gestao-de-carteiras-de-renda-fixa`. Every one passed the test above, because
        `apostila-cga-2026#anything-at-all` starts with `apostila-cga-2026`. That is the exact
        shape of a tautological gate: it asserts the string was written, not that it is true.

        This resolves the fragment against the corpus directory. A citation that does not
        resolve is worse than an absent one, because it reads as verified.
        """
        corpus = DATA.parents[3] / "cga-2026-markdown"
        if not corpus.is_dir():
            self.skipTest("corpus directory is not present in this checkout")
        files = {path.stem for path in corpus.glob("*.md")}
        unresolved = [
            (record["concept_id"], record["authority"])
            for record in self.records
            if record["authority"].startswith("apostila-cga-2026#")
            and record["authority"].split("#", 1)[1] not in files
        ]
        self.assertEqual(
            [], unresolved,
            "authority fragments must name a file in cga-2026-markdown/. "
            "Run scripts/fix_authority_anchors.py to derive them from where the terms occur.",
        )

    def test_every_domain_concept_is_bilingual_with_content(self):
        """The anchor asks for an EN/PT dictionary; a blank English side would satisfy the
        schema's `minItems` while delivering half the objective."""
        for record in self.records:
            if "cga" not in record["domains"]:
                continue
            with self.subTest(concept=record["concept_id"]):
                for language in ("en", "pt-BR"):
                    self.assertTrue(record["labels"][language]["pref"].strip())
                    self.assertTrue(record["lexical_forms"][language])
                    self.assertTrue(record["positive_examples"][language])

    # Form/concept pairs an exhaustive corpus sweep proved wrong. The key is the PAIR, not the
    # form: `crédito` is wrong on `risk.credit` and right as the preferred label of
    # `entity.credit`, and `exposição` is wrong on `risk.financial` and right on
    # `quantity.exposure`. That distinction is the whole lesson — a rejected form usually means
    # a missing concept, not a bad word.
    ADJUDICATED_NOT_AUTOMATIC = {
        ("entity.securities", "pt-BR", "valores"): "matched scores, notional amounts and accounting sums; 10 of 10 real occurrences wrong outside 'bolsa de valores'",
        ("quantity.value", "pt-BR", "valores"): "matched 'Bolsa de Valores'",
        ("risk.credit", "pt-BR", "crédito"): "matched 'curva de crédito', which is credit, not credit risk",
        ("risk.financial", "pt-BR", "exposição"): "gross exposure is a quantity, not a risk",
        ("risk.financial", "en", "exposure"): "same, in English",
        ("technical.hedge", "pt-BR", "proteção"): "matched regulatory investor protection",
        ("entity.class", "pt-BR", "classes"): "matched asset-class context as fund-share class",
        ("artifact.configuration", "pt-BR", "ajustes"): "matched daily futures margin",
        ("technical.long_position", "pt-BR", "comprado"): "matched a purchased CD, not a trading long",
        ("technical.short_position", "pt-BR", "vendido"): "same shape as `comprado`",
        ("technical.information_ratio", "pt-BR", "IR"): "shares the acronym with Imposto de Renda",
        ("entity.income_tax", "pt-BR", "IR"): "shares the acronym with Information Ratio",
        ("entity.resource", "en", "funds"): "matched 'Hedge Funds'",
        ("technical.bacen", "pt-BR", "Banco Central"): "matched the European Central Bank",
    }

    def test_forms_adjudication_rejected_never_return_as_automatic(self):
        """A form rejected on a concept must not come back automatic on that concept.

        This guards the one defect that recurred across batches: `valores` was removed from
        `quantity.value` in batch 3 for matching *Bolsa de Valores*, then reintroduced in
        batch 4 on `entity.securities` with the same bare shape and the same failure — 10 of
        10 real occurrences wrong. Three review rounds found it. A list makes a fourth
        unnecessary, and memory is not a guard.
        """
        for record in self.records:
            for language, entries in record["lexical_forms"].items():
                for entry in entries:
                    key = (record["concept_id"], language, entry["form"])
                    if key in self.ADJUDICATED_NOT_AUTOMATIC and entry["policy"] == "auto":
                        self.fail(
                            f"{record['concept_id']}.{language}: '{entry['form']}' is back as "
                            f"policy=auto. Adjudication rejected it because it "
                            f"{self.ADJUDICATED_NOT_AUTOMATIC[key]}. Use a multi-word form, or "
                            f"keep it as `review`."
                        )

    def test_retired_ids_are_not_referenced_as_concepts(self):
        """A renamed id is history, not a concept: it must not appear in any relation."""
        live = {record["concept_id"] for record in self.records}
        for record in self.records:
            for kind, targets in record["relations"].items():
                for target in targets:
                    with self.subTest(concept=record["concept_id"], relation=kind):
                        self.assertIn(target, live)


if __name__ == "__main__":
    unittest.main()
