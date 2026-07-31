"""The construction pipeline's deterministic parts, each with its canary.

A guard only ever seen passing is not a guard (registry lesson: the reseal test that compared
a function with itself). Every validator here is exercised in BOTH directions: the shape it
admits and the forged shape it must reject.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lexicon_pipeline as pipeline  # noqa: E402
from import_cga_batch import demote_ambiguous  # noqa: E402


def record(cid: str, contexts: list[str], forms_pt: list[str]) -> dict:
    return {
        "concept_id": cid,
        "contexts": contexts,
        "lexical_forms": {
            "pt-BR": [{"form": f, "features": {}, "policy": "auto"} for f in forms_pt],
            "en": [],
        },
    }


class TheDemoterIsContextAware(unittest.TestCase):
    def test_same_scope_collision_demotes_both_sides(self):
        current = [record("cga.a", ["cga"], ["opção"]),
                   record("cga.b", ["cga"], ["opção"])]
        demoted = demote_ambiguous(current)
        self.assertEqual(sorted(demoted),
                         ["cga.a.pt-BR:opção", "cga.b.pt-BR:opção"])

    def test_disjoint_domains_may_share_a_surface(self):
        """The plug-and-play property: `opção` in cga and in reasoning is NOT a collision,
        because a scoped matcher never loads both. The old demoter destroyed the incumbent."""
        current = [record("cga.option", ["finance", "cga"], ["opção"]),
                   record("reasoning.option", ["reasoning"], ["opção"])]
        self.assertEqual(demote_ambiguous(current), [])

    def test_only_the_intersecting_pair_is_demoted_never_the_disjoint_third(self):
        current = [record("cga.a", ["cga"], ["prazo"]),
                   record("cga.b", ["cga"], ["prazo"]),
                   record("reasoning.c", ["reasoning"], ["prazo"])]
        demoted = demote_ambiguous(current)
        self.assertEqual(sorted(demoted), ["cga.a.pt-BR:prazo", "cga.b.pt-BR:prazo"])
        reasoning = current[2]["lexical_forms"]["pt-BR"][0]
        self.assertEqual(reasoning["policy"], "auto")

    def test_empty_contexts_is_global_and_collides_with_everything(self):
        current = [record("old.global", [], ["taxa"]),
                   record("reasoning.rate", ["reasoning"], ["taxa"])]
        demoted = demote_ambiguous(current)
        self.assertEqual(sorted(demoted),
                         ["old.global.pt-BR:taxa", "reasoning.rate.pt-BR:taxa"])


class TheAdjudicationValidatorFailsClosed(unittest.TestCase):
    """Canaries for the anti-hallucination boundary."""

    PROSE = ("O viés de confirmação leva a aceitar apenas evidências favoráveis.\n"
             "Uma premissa oculta sustenta o argumento sem ser declarada.")

    def setUp(self):
        self.state = {"domain": "reasoning", "corpus": str(SCRIPTS.parent)}
        self.enums = {"semantic_class": ["technical_term", "entity"],
                      "pos": ["noun", "verb"]}
        self.candidate = {"term": "viés de confirmação"}
        self.attested = pipeline.fold(self.PROSE)

    def concept(self, **overrides) -> dict:
        base = {
            "id": "reasoning.confirmation_bias",
            "class": "technical_term", "pos": "noun",
            "en": "confirmation bias", "pt": "viés de confirmação",
            "alt_en": [], "alt_pt": [], "obs_en": [], "obs_pt": [],
            "def": "The tendency to accept only evidence that favours a prior belief.",
            "authority": "corpus:tests#confirmation-bias",
            "pos_pt": ["O viés de confirmação leva a aceitar apenas evidências favoráveis."],
            "neg_pt": ["O alfaiate corrigiu o viés do tecido antes de costurar."],
        }
        base.update(overrides)
        return base

    def validate(self, concept: dict) -> list[str]:
        decision = {"admit": True, "why": "domain concept", "concept": concept}
        return pipeline.validate_adjudication(
            decision, self.candidate, self.state, self.enums,
            self.attested, surfaces=set(), existing_ids=set())

    def test_a_sound_proposal_passes_every_gate_except_the_authority_file(self):
        errors = self.validate(self.concept())
        self.assertEqual(errors, ["authority cites 'tests', not a corpus file"])

    def test_a_fabricated_quote_is_rejected_by_the_byte_comparison(self):
        errors = self.validate(self.concept(
            pos_pt=["O viés de confirmação é um erro clássico de raciocínio."]))
        self.assertTrue(any("not a verbatim corpus quote" in e for e in errors), errors)

    def test_a_surface_the_prose_never_wrote_is_rejected(self):
        errors = self.validate(self.concept(pt="viés confirmatório"))
        self.assertTrue(any("does not occur in the corpus" in e for e in errors), errors)

    def test_admitting_a_concept_that_does_not_cover_the_term_is_rejected(self):
        errors = self.validate(self.concept(
            pt="premissa oculta",
            pos_pt=["Uma premissa oculta sustenta o argumento sem ser declarada."]))
        self.assertTrue(any("would not decide the term" in e for e in errors), errors)

    def test_an_example_in_an_attested_inflection_is_accepted(self):
        """The lemma is singular and the corpus writes the plural — the normal case.

        The first version of this guard demanded the preferred label verbatim and refused
        `premissa oculta` because every corpus sentence says `premissas ocultas`. It measured
        the wrong thing: the example has to SHOW the term, not agree with the lemma's number.
        """
        errors = self.validate(self.concept(
            pt="viés de confirmação", obs_pt=["vieses de confirmação"],
            pos_pt=["Os vieses de confirmação levam a aceitar apenas evidências favoráveis."]))
        self.assertFalse([e for e in errors if "declared surfaces" in e], errors)

    def test_an_example_that_shows_no_declared_surface_is_still_rejected(self):
        """The loosening must not turn the guard off."""
        errors = self.validate(self.concept(
            pos_pt=["Uma premissa oculta sustenta o argumento sem ser declarada."]))
        self.assertTrue(any("declared surfaces" in e for e in errors), errors)

    def test_a_negative_example_copied_from_the_corpus_is_rejected(self):
        errors = self.validate(self.concept(
            neg_pt=["Uma premissa oculta sustenta o argumento sem ser declarada."]))
        self.assertTrue(any("must be AUTHORED" in e for e in errors), errors)

    def test_a_rejection_without_a_reason_from_the_closed_set_is_rejected(self):
        errors = pipeline.validate_adjudication(
            {"admit": False, "why": "x", "rejected_as": "meh"},
            self.candidate, self.state, self.enums, self.attested, set(), set())
        self.assertTrue(any("rejected_as" in e for e in errors), errors)


class TheTwoWavesDoNotCollide(unittest.TestCase):
    """`adjudicate` and `adjudicate-retry` live in one directory; a prefix glob merges them.

    Canary for a real defect: with `glob("adjudicate-*")` the first wave swallowed the retry
    wave and tried to read a result file that did not exist yet — a crash where the loop should
    have simply waited.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.round_dir = Path(self.tmp.name)
        pending = self.round_dir / "tasks" / "pending"
        results = self.round_dir / "tasks" / "results"
        pending.mkdir(parents=True)
        results.mkdir(parents=True)
        for name, payload in (
            ("adjudicate-g01-aaaa.json", {"task_id": "a1", "kind": "adjudicate", "items": []}),
            ("adjudicate-retry-g01-bbbb.json",
             {"task_id": "r1", "kind": "adjudicate", "attempt": 2, "items": []}),
        ):
            (pending / name).write_text(json.dumps(payload), encoding="utf-8")
        (results / "adjudicate-g01-aaaa.json").write_text(
            json.dumps({"decisions": []}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_first_wave_is_complete_while_the_retry_wave_is_still_open(self):
        pairs, missing = pipeline.collect_results(self.round_dir, "adjudicate")
        self.assertEqual(missing, [])
        self.assertEqual([p["task"]["task_id"] for p in pairs], ["a1"])

    def test_the_retry_wave_reports_itself_as_pending_not_as_a_crash(self):
        _, missing = pipeline.collect_results(self.round_dir, "adjudicate-retry")
        self.assertEqual([p.name for p in missing], ["adjudicate-retry-g01-bbbb.json"])


class DuplicateConceptsMergeInsteadOfDisappearing(unittest.TestCase):
    """Two candidates resolving to one concept must not silently drop one of them.

    Real case from round 1 on the reasoning corpus: two agents independently proposed
    `reasoning.argument` for `argumento` and `argumentos`. Dropping the second leaves that term
    undecided, so it returns as a candidate every round — the loop stops converging.
    """

    @staticmethod
    def proposal(term: str, cid: str, pt: str, obs: list[str]) -> dict:
        return {"candidate": {"term": term},
                "decision": {"why": "w", "concept": {"id": cid, "pt": pt, "obs_pt": obs}}}

    def test_same_id_and_same_pref_merges_and_keeps_both_terms(self):
        merged, conflicts, notes = pipeline.merge_proposals([
            self.proposal("argumentos", "reasoning.argument", "argumento", ["argumentos"]),
            self.proposal("argumento", "reasoning.argument", "argumento", []),
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(conflicts, [])
        self.assertEqual(sorted(merged[0]["terms"]), ["argumento", "argumentos"])
        self.assertEqual(merged[0]["decision"]["concept"]["obs_pt"], ["argumentos"])
        self.assertTrue(notes)

    def test_the_winner_does_not_depend_on_arrival_order(self):
        forward = pipeline.merge_proposals([
            self.proposal("argumento", "reasoning.argument", "argumento", []),
            self.proposal("argumentos", "reasoning.argument", "argumento", ["argumentos"]),
        ])[0]
        backward = pipeline.merge_proposals([
            self.proposal("argumentos", "reasoning.argument", "argumento", ["argumentos"]),
            self.proposal("argumento", "reasoning.argument", "argumento", []),
        ])[0]
        self.assertEqual(forward[0]["candidate"]["term"], backward[0]["candidate"]["term"])
        self.assertEqual(forward[0]["terms"], backward[0]["terms"])

    def test_same_id_with_different_prefs_is_a_conflict_not_a_merge(self):
        merged, conflicts, notes = pipeline.merge_proposals([
            self.proposal("argumento", "reasoning.argument", "argumento", []),
            self.proposal("tese", "reasoning.argument", "tese", []),
        ])
        self.assertEqual(merged, [])
        self.assertEqual(len(conflicts), 2)
        self.assertTrue(any("needs-owner" in note for note in notes))

    def test_distinct_concepts_are_left_alone(self):
        merged, conflicts, _ = pipeline.merge_proposals([
            self.proposal("premissa", "reasoning.premise", "premissa", []),
            self.proposal("falácia", "reasoning.fallacy", "falácia", []),
        ])
        self.assertEqual(len(merged), 2)
        self.assertEqual(conflicts, [])
        self.assertEqual([m["terms"] for m in merged], [["falácia"], ["premissa"]])


class TheRevertIsWholeNotPartial(unittest.TestCase):
    """Every artifact APPLY regenerates must be in the snapshot, or a revert is a half-revert.

    The Makefile already learned this one file at a time: a gate watching MANIFEST.json alone
    shipped a stale release-manifest.json. Deriving the list from the generators' own constants
    means the next generated artifact cannot be silently missed.
    """

    def test_every_regenerated_artifact_is_snapshotted(self):
        import build_release
        import cut_manifest
        regenerated = {
            build_release.RELEASE_MANIFEST.resolve(),
            build_release.CHECKSUMS.resolve(),
            cut_manifest.MANIFEST.resolve(),
        }
        snapshotted = {path.resolve() for path in pipeline.SNAPSHOT_FILES}
        self.assertEqual(regenerated - snapshotted, set())

    def test_the_registry_triplet_and_the_version_module_are_snapshotted(self):
        names = {path.name for path in pipeline.SNAPSHOT_FILES}
        self.assertLessEqual(
            {"registry.jsonl", "registry.release.json", "registry.provenance.jsonl",
             "registry.py"}, names)

    def test_snapshot_names_are_unique_so_no_backup_overwrites_another(self):
        names = [path.name for path in pipeline.SNAPSHOT_FILES]
        self.assertEqual(len(names), len(set(names)))


class CitationsMustContainTheTerm(unittest.TestCase):
    """Evidence handed to a model must actually contain the word it is being asked about.

    The adversarial node caught this in round 2: four of the six citations offered for `trair`
    were substring hits inside `extrair` and `atrair`. Asking a model to decide a sense from
    sentences that do not contain the term is asking it to hallucinate, and it duly produced an
    entry that was refuted on that exact ground.
    """

    SEGMENTS = [
        {"prose": True, "source": "c.md", "index": 0,
         "text": "Ele pode decidir trair você no último instante."},
        {"prose": True, "source": "c.md", "index": 1,
         "text": "É preciso extrair a informação e atrair o leitor."},
        {"prose": True, "source": "c.md", "index": 2,
         "text": "O come-cotas reduz as cotas do investidor."},
        {"prose": False, "source": "c.md", "index": 3, "text": "# Trair e teoria dos jogos"},
    ]

    def texts(self, term: str) -> list[str]:
        return [c["text"] for c in pipeline.citations_for(term, self.SEGMENTS)]

    def test_a_substring_hit_is_not_a_citation(self):
        found = self.texts("trair")
        self.assertEqual(found, ["Ele pode decidir trair você no último instante."])

    def test_a_hyphenated_term_is_one_unit(self):
        self.assertEqual(self.texts("come-cotas"),
                         ["O come-cotas reduz as cotas do investidor."])
        self.assertEqual(self.texts("cotas"),
                         ["O come-cotas reduz as cotas do investidor."])

    def test_headings_are_never_citations(self):
        for citation in pipeline.citations_for("trair", self.SEGMENTS):
            self.assertFalse(citation["text"].startswith("#"))


class TheSemanticDigestAnswersTheRightQuestion(unittest.TestCase):
    """Stripping the seal must remove the release identity and nothing else.

    Adding the reasoning pack changed the byte digest of the CGA output while every annotation
    stayed identical — every record echoes `registry_version` and `registry_sha256`, so the byte
    digest reports a difference on every release and cannot falsify "packs do not interfere".
    A canary in both directions: a seal-only change must vanish, a behaviour change must not.
    """

    BASE = {"registry_version": "2.36.0", "registry_sha256": "aaa", "lexicon_version": "2.36.0",
            "lexicon_sha256": "bbb", "tool_version": "1.0", "schema_version": "v1",
            "original": "O argumento tem premissas.", "concept_ids": ["reasoning.argument"],
            "canonical_text": "O argumento tem premissas."}

    @staticmethod
    def strip(record: dict) -> dict:
        import verify_reproducibility
        return {k: v for k, v in record.items()
                if k not in verify_reproducibility.SEAL_FIELDS}

    def test_a_release_bump_alone_leaves_the_semantic_view_identical(self):
        bumped = {**self.BASE, "registry_version": "2.37.0", "registry_sha256": "zzz",
                  "lexicon_version": "2.37.0", "lexicon_sha256": "yyy"}
        self.assertEqual(self.strip(self.BASE), self.strip(bumped))

    def test_a_different_concept_still_shows_up(self):
        changed = {**self.BASE, "concept_ids": ["reasoning.premise"]}
        self.assertNotEqual(self.strip(self.BASE), self.strip(changed))

    def test_a_different_rewrite_still_shows_up(self):
        changed = {**self.BASE, "canonical_text": "O argumento tem premissa."}
        self.assertNotEqual(self.strip(self.BASE), self.strip(changed))


class TheAccountingIsHonest(unittest.TestCase):
    def test_wilson_matches_the_convention_report_precision_publishes(self):
        """One repo, one Wilson. The pipeline's bound must equal report_precision.py's."""
        from report_precision import wilson as published
        for successes, n in ((60, 60), (59, 60), (238, 240), (0, 7)):
            self.assertAlmostEqual(pipeline.wilson_lower(successes, n),
                                   published(successes, n)[0], places=10)
        self.assertEqual(pipeline.wilson_lower(0, 0), 0.0)

    def test_task_ids_are_content_hashes(self):
        a = pipeline.stable_id("adjudicate-g01", {"items": [1, 2]})
        b = pipeline.stable_id("adjudicate-g01", {"items": [1, 2]})
        c = pipeline.stable_id("adjudicate-g01", {"items": [1, 3]})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_partition_charges_no_ai_for_covered_sentences(self):
        segments = [
            {"prose": True, "text": "O come-cotas era sobre os fundos.",
             "concept_ids": ["technical.come_cotas"], "match_events": [],
             "unresolved": [{"original": " era sobre os "}]},
            {"prose": True, "text": "Uma frase com zetaflux inédito.",
             "concept_ids": [], "match_events": [],
             "unresolved": [{"original": "Uma frase com zetaflux inédito."}]},
            {"prose": False, "text": "# Um título", "concept_ids": [],
             "match_events": [], "unresolved": []},
        ]
        result = pipeline.partition(segments, decided={})
        self.assertEqual(result["prose_sentences"], 2)
        self.assertEqual(result["covered_with_concepts"], 1)
        self.assertEqual(result["pending_sentences"], 1)
        self.assertIn("zetaflux", result["pending_tokens"])
        decided = {"zetaflux": {"state": "common-language"},
                   "inédito": {"state": "common-language"},
                   "frase": {"state": "common-language"}}
        after = pipeline.partition(segments, decided)
        self.assertEqual(after["pending_sentences"], 0)
        self.assertEqual(after["decided_all_common"], 1)


if __name__ == "__main__":
    unittest.main()
