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

    def test_operator_concepts_are_attested_in_the_corpus(self):
        """A controlled language whose operators match nothing is a vocabulary, not a language.

        Ten polarity, modality, conditional and quantity concepts shipped with every Portuguese
        automatic form absent from the corpus: `é proibido` where the material writes `é
        vedado`, `quando a condição ocorrer` where it writes `caso`. They passed every existing
        test because nothing checked that a label is a word anyone uses. This is the same defect
        as the fabricated anchors — a string asserted rather than a fact verified — on the layer
        the original request calls `os termos essenciais`.

        Genuinely ambiguous operators are excluded: `deve` is both obligation and logical
        necessity and belongs at review, so a concept is only required to have SOME attested
        form, automatic or not.

        Scope is concepts that claim the `cga` domain. `condition.only_if` and
        `condition.unless` carry `domains: [documentation, controlled_instruction]` and have no
        Portuguese wording in this corpus — `somente se` and `a menos que` occur zero times, and
        the functions they serve are carried here by `condition.provided_that` (`desde que`, 46
        occurrences) and `polarity.exception` (`salvo`/`ressalvado`, 26). Requiring a
        technical-instruction concept to appear in a finance textbook would be a false gate, and
        inventing a Portuguese form so it passes would be the fabrication this test exists to
        prevent.
        """
        import re
        import unicodedata

        corpus = DATA.parents[3] / "cga-2026-markdown"
        if not corpus.is_dir():
            self.skipTest("corpus directory is not present in this checkout")
        text = unicodedata.normalize("NFC", " ".join(
            path.read_text(encoding="utf-8", errors="replace") for path in corpus.glob("*.md")
        )).casefold()

        operator_classes = {"polarity", "modality", "condition_marker", "quantity_marker"}
        dead = []
        for record in self.records:
            if record["semantic_class"] not in operator_classes:
                continue
            if "cga" not in record["domains"]:
                continue
            forms = [entry["form"] for entry in record["lexical_forms"]["pt-BR"]]
            if not any(
                re.search(rf"(?<![a-zà-ÿ0-9]){re.escape(unicodedata.normalize('NFC', form).casefold())}"
                          rf"(?![a-zà-ÿ0-9])", text)
                for form in forms
            ):
                dead.append((record["concept_id"], forms))
        self.assertEqual(
            [], dead,
            "these operator concepts match nothing in the corpus. Register the word the "
            "material actually uses rather than a paraphrase of the English label.",
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
        # REMOVED after measurement. This entry was reasoning by analogy — `comprado` was
        # measured wrong, `vendido` looked similar, so it was rejected without being read. The
        # corpus has one occurrence, `está vendido (short) no valor de R$ 15 milhões`, and it is
        # the short position. A guard entered on a resemblance is the same defect this whole
        # campaign kept finding, in the guard rather than in the registry. `comprado` stays: its
        # single occurrence, `os CDs comprado por meio de um banco segurado`, really is wrong.
        ("technical.information_ratio", "pt-BR", "IR"): "shares the acronym with Imposto de Renda",
        ("entity.income_tax", "pt-BR", "IR"): "shares the acronym with Information Ratio",
        ("entity.resource", "en", "funds"): "matched 'Hedge Funds'",
        ("technical.bacen", "pt-BR", "Banco Central"): "matched the European Central Bank",
    }

    def test_forms_adjudication_rejected_never_return_as_automatic(self):
        """A form rejected on a concept must not come back automatic on that concept.

        Every entry must name a MEASURED failure, not a resemblance to one. `vendido` sat here
        for looking like `comprado` until someone read its single occurrence and found it
        correct; an unmeasured guard costs recall exactly as silently as an unmeasured form
        costs precision.

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


    def test_no_concept_gathers_two_opposite_senses(self):
        """A concept may hold many spellings of one sense, never two that are opposites.

        The canonical rewrite substitutes any of a concept's automatic forms for its preferred one,
        so a concept holding both sides of an opposition rewrites one into the other.
        `technical.option` held `opção de venda` (put) and `opção de compra` (call) with the call
        preferred, and every put in the corpus came out a call — the instrument inverted, in the
        field the README calls a primary deliverable, across thirteen occurrences.

        The engine now refuses that substitution outright, but a guard catching a modelling error
        is not a reason to keep the modelling error. This asserts the model: the forms of one
        concept never straddle a declared opposition. Proven by canary — restoring the put forms to
        `technical.option` fails it.
        """
        import sys
        sys.path.insert(0, str(DATA.parents[2]))
        from semantic_normalizer.normalizer import CANONICAL_ANTONYMS
        from semantic_normalizer.registry import automatic_surfaces, nfc_casefold
        offenders = []
        for record in self.records:
            for language in ("en", "pt-BR"):
                tokens = [set(nfc_casefold(form).split())
                          for form in automatic_surfaces(record, language)]
                for left, right in CANONICAL_ANTONYMS:
                    if any(left in group for group in tokens) and any(
                        right in group for group in tokens
                    ):
                        offenders.append(f"{record['concept_id']}.{language}: {left}/{right}")
        self.assertEqual(
            [], sorted(set(offenders)),
            "these concepts gather both sides of an opposition, so the canonical rewrite would "
            "substitute one sense for its opposite. Split them the way technical.put_option and "
            "technical.call_option were split out of technical.option.",
        )

    def test_operators_that_end_in_a_preposition_are_not_treated_as_fragments(self):
        """`DANGLING` decides what counts as a truncation, and some labels end that way by right.

        Eleven canonical surfaces end in a preposition or conjunction. Nine are operators whose
        complete form is exactly that shape — `sem prejuízo de`, `antes de`, `is able to`,
        `in proportion as` — where the preposition IS the operator rather than a phrase cut short.
        The truncation rule never fires on them because it only refuses a replacement that is a
        strict PREFIX of the alias it replaces, and `sem prejuízo de` is not a prefix of
        `sem prejuízo das`: they differ at the last token rather than one being the head of the
        other.

        The remaining two are `ativo em` and `passivo em`, and those are fragment-shaped on purpose.
        Every corpus form of those concepts carries the index the leg is denominated in
        (`ativo em dólar`, `passivo em prefixado`), so a complete-looking canonical would rewrite
        `passivo em dólar` into something that silently drops the currency — the guard would not
        fire, because the replacement would no longer be a prefix. Keeping the label a visible
        fragment is what keeps the rewrite refused. Documented here because it looks like an
        oversight and is the opposite of one.
        """
        import sys
        sys.path.insert(0, str(DATA.parents[2]))
        from semantic_normalizer.normalizer import DANGLING, _rewrite_is_safe
        from semantic_normalizer.registry import automatic_surfaces, nfc_casefold

        for record in self.records:
            for language in ("en", "pt-BR"):
                surfaces = automatic_surfaces(record, language)
                if not surfaces:
                    continue
                tokens = nfc_casefold(surfaces[0]).split()
                if len(tokens) < 2 or tokens[-1] not in DANGLING:
                    continue
                for other in surfaces[1:]:
                    with self.subTest(concept=record["concept_id"], form=other[:34]):
                        # Either the pair is not a truncation at all, or it is one and is refused.
                        other_tokens = nfc_casefold(other).split()
                        is_truncation = other_tokens[:len(tokens)] == tokens and len(
                            other_tokens
                        ) > len(tokens)
                        self.assertTrue(
                            not is_truncation or not _rewrite_is_safe(other, surfaces[0]),
                            f"{other!r} would be rewritten to the fragment {surfaces[0]!r}.",
                        )

    def test_the_rewrite_guard_refuses_the_two_shapes_it_exists_for(self):
        """The safety net itself, exercised on both defects that motivated it."""
        import sys
        sys.path.insert(0, str(DATA.parents[2]))
        from semantic_normalizer.normalizer import _rewrite_is_safe
        self.assertFalse(_rewrite_is_safe("opção de venda", "opção de compra"),
                         "a put must never be rewritten into a call")
        self.assertFalse(_rewrite_is_safe("é vedada", "vedado"),
                         "dropping the copula removes the sentence's only verb")
        self.assertFalse(_rewrite_is_safe("ativo em dólar", "ativo em"),
                         "truncating to a dangling preposition strands the phrase")
        self.assertTrue(_rewrite_is_safe("cotas", "cota"), "inflection is what the field is for")
        # The 125 rewrites the first version of the truncation rule blocked by mistake. A guard
        # that suppresses ordinary normalisation to catch a fragment is a regression with a good
        # excuse, and these are the shapes that proved it.
        for alias, replacement in (("fundo de investimento", "fundo"),
                                   ("assembleia de cotistas", "assembleia"),
                                   ("gestor de recursos", "gestor"),
                                   ("ativo financeiro", "ativo"),
                                   ("ISE B3", "ISE")):
            self.assertTrue(
                _rewrite_is_safe(alias, replacement),
                f"{alias!r} -> {replacement!r} names the same thing more briefly, which is the "
                f"normalisation this tool exists to perform.",
            )
        self.assertTrue(_rewrite_is_safe("classificação de risco", "rating"),
                        "a shorter synonym of the same sense is a legitimate canonicalisation")


if __name__ == "__main__":
    unittest.main()
