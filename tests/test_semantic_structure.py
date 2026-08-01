"""Antonym derivation, redundancy detection, hierarchy linking — each with its canary.

These three exist because of one measurement: rank the shipped concepts by how similar their
definitions are and the top of the list is NOT synonyms, it is opposites. `state.enabled` and
`state.disabled` score 1.00 because the only token separating them is `not` — three characters,
dropped by every stopword filter. Anything built on textual similarity has to survive that fact
before it is allowed near the registry.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "src"))

import derive_antonyms as antonyms  # noqa: E402
import find_redundant_concepts as redundancy  # noqa: E402
import link_hierarchy as hierarchy  # noqa: E402
from import_cga_batch import demote_ambiguous  # noqa: E402

REGISTRY = ROOT / "src" / "semantic_normalizer" / "data" / "registry.jsonl"


def records() -> list[dict]:
    return [json.loads(line)
            for line in REGISTRY.read_text(encoding="utf-8").splitlines() if line.strip()]


class ShortWordsSurviveBecauseTheyCarryTheOpposition(unittest.TestCase):
    """The whole method rests on NOT dropping three-letter words."""

    def test_not_survives_the_content_filter(self):
        self.assertIn("not", antonyms.content("A state in which a function is not available."))

    def test_the_enabled_disabled_pair_is_detected_as_negation(self):
        left = antonyms.content("A state in which a function is available for operation.")
        right = antonyms.content("A state in which a function is not available for operation.")
        kind, evidence = antonyms.opposition(left, right)
        self.assertEqual(kind, "negation")
        self.assertIn("not", evidence)

    def test_dropping_short_words_would_have_hidden_it(self):
        """Canary for the failure mode, stated as a test so it cannot come back quietly."""
        naive = lambda text: {w for w in text.lower().replace(".", "").split() if len(w) > 3}
        left = naive("A state in which a function is available for operation")
        right = naive("A state in which a function is not available for operation")
        self.assertEqual(left, right, "if these ever differ, the naive filter stopped losing "
                                      "`not` and this test's premise is stale")

    def test_a_polar_split_is_detected(self):
        left = antonyms.content("The right to buy the underlying at a set price.")
        right = antonyms.content("The right to sell the underlying at a set price.")
        self.assertEqual(antonyms.opposition(left, right)[0], "polar")

    def test_two_unrelated_definitions_are_not_an_opposition(self):
        left = antonyms.content("A vehicle that pools investor capital under one policy.")
        right = antonyms.content("The tendency to accept evidence favouring a prior belief.")
        self.assertIsNone(antonyms.opposition(left, right))


class TheDerivedPairsAreActionableByTheGuard(unittest.TestCase):
    def test_every_derived_pair_reduces_to_single_tokens(self):
        """`_rewrite_is_safe` tests token membership, so a multi-word pair is a dead line."""
        for row in antonyms.derive(records(), 0.5):
            for left, right in row["surface_pairs"]:
                self.assertNotIn(" ", left, f"{row['concepts']}: multi-word pair never matches")
                self.assertNotIn(" ", right, f"{row['concepts']}: multi-word pair never matches")

    def test_coverage_is_measured_with_the_guards_own_rule(self):
        shipped = (("comprada", "vendida"),)
        self.assertTrue(antonyms.already_blocked("posição comprada", "posição vendida", shipped))
        self.assertFalse(antonyms.already_blocked("posição comprada", "posição longa", shipped))

    def test_the_shipped_guard_covers_everything_the_registry_implies(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "derive_antonyms.py"), "--check"],
            capture_output=True, text=True, cwd=ROOT,
            env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
        self.assertEqual(result.returncode, 0, result.stderr[:2000])


class RedundancyIsDecidedBySubstitutionNotBySimilarity(unittest.TestCase):
    def test_an_antonym_pair_never_reaches_the_judgement_queue(self):
        """The safety argument: oppositions are removed BEFORE similarity proposes anything."""
        opposed = {frozenset(row["concepts"]) for row in antonyms.derive(records(), 0.4)}
        self.assertIn(frozenset({"technical.call_option", "technical.put_option"}), opposed)
        self.assertIn(frozenset({"state.enabled", "state.disabled"}), opposed)

    def test_the_substitution_test_runs_the_engines_own_guard(self):
        ok, _ = redundancy.substitution_is_safe("opção de compra", "opção de venda")
        self.assertFalse(ok, "the engine refuses this; the redundancy test must inherit that")

    def test_word_boundaries_apply_to_occurrence_search(self):
        found = redundancy.occurrences("trair", ["É preciso extrair o dado.",
                                                 "Ele pode trair o acordo."], cap=5)
        self.assertEqual([s for s, _ in found], ["Ele pode trair o acordo."])


class HierarchyEdgesPreserveTheClassAndTheInverse(unittest.TestCase):
    def test_containment_is_token_level_not_substring(self):
        self.assertTrue(hierarchy.contains_tokens(
            hierarchy.tokens("ajuste de convexidade"), hierarchy.tokens("convexidade")))
        self.assertFalse(hierarchy.contains_tokens(
            hierarchy.tokens("arrendamento"), hierarchy.tokens("renda")))

    def test_an_is_a_edge_never_crosses_the_semantic_class(self):
        """`action.cancel_registration` is not a kind of `entity.cvm`."""
        pair = [
            {"concept_id": "action.x", "semantic_class": "action",
             "labels": {"pt-BR": {"pref": "cancelar registro"}, "en": {"pref": "cancel"}}},
            {"concept_id": "entity.y", "semantic_class": "entity",
             "labels": {"pt-BR": {"pref": "registro"}, "en": {"pref": "registration"}}},
        ]
        self.assertEqual(hierarchy.containment_edges(pair), [])

    def test_same_class_containment_is_proposed(self):
        pair = [
            {"concept_id": "technical.macaulay", "semantic_class": "technical_term",
             "labels": {"pt-BR": {"pref": "duration de macaulay"}, "en": {"pref": "x"}}},
            {"concept_id": "technical.duration", "semantic_class": "technical_term",
             "labels": {"pt-BR": {"pref": "duration"}, "en": {"pref": "y"}}},
        ]
        edges = hierarchy.containment_edges(pair)
        self.assertEqual([(e["narrower"], e["broader"]) for e in edges],
                         [("technical.macaulay", "technical.duration")])

    def test_the_shipped_registry_declares_both_sides_of_every_edge(self):
        by_id = {r["concept_id"]: r for r in records()}
        edges = [(cid, target) for cid, record in by_id.items()
                 for target in record["relations"]["broader"]]
        self.assertTrue(edges, "the registry has no hierarchy at all")
        for narrower, broader in edges:
            self.assertIn(narrower, by_id[broader]["relations"]["narrower"],
                          f"{narrower} -> {broader} is declared one way only")


class AConceptNamingAPairIsNotACrossing(unittest.TestCase):
    """Widening the antonym list must not refuse a concept whose NAME holds both poles.

    Found by adversarial review, and the miss is the interesting part: this exemption already
    existed in `test_registry_governance.py`, reasoned out when the wider list surfaced
    `technical.long_and_short`, `technical.asset_liability_management` and
    `technical.premium_or_discount`. It was applied to the modelling check and never carried
    across to `_rewrite_is_safe`, which is the one that runs. Cost: 11 legitimate rewrites in
    the CGA corpus, silently refused.

    My own measurement had reported the cost as ZERO, because it counted `canonical_mappings` —
    emitted per match whether or not the text changes — instead of counting mappings where
    `original != canonical`. The proxy sat still at 13.466 while the quantity it stood for
    dropped by 11. These tests assert the BEHAVIOUR, so a proxy cannot hide it again.
    """

    def safe(self, alias: str, replacement: str) -> bool:
        from semantic_normalizer.normalizer import _rewrite_is_safe
        return _rewrite_is_safe(alias, replacement)

    def test_a_compound_naming_the_pair_normalises(self):
        self.assertTrue(self.safe("LONG & SHORT", "long and short"))
        self.assertTrue(self.safe("Long and Short", "long and short"))

    def test_edge_punctuation_does_not_hide_a_pole(self):
        """`Asset- Liability Management` splits into `asset-`, which is not `asset`."""
        self.assertTrue(self.safe("Asset- Liability Management", "asset liability management"))

    def test_a_genuine_crossing_is_still_refused(self):
        """The exemption must not turn the guard off. One pole here, the other there."""
        self.assertFalse(self.safe("opção de compra", "opção de venda"))
        self.assertFalse(self.safe("posição comprada", "posição vendida"))
        self.assertFalse(self.safe("fundos abertos", "fundos fechados"))

    def test_the_shipped_corpus_concepts_rewrite(self):
        """End to end through the real matcher, not through the guard alone."""
        from semantic_normalizer.normalizer import normalize_text
        from semantic_normalizer.registry import load_registry
        lexicon = load_registry(contexts=["core", "cga"])
        for text, expected in (("Estratégia LONG & SHORT na carteira.", "long and short"),
                               ("Asset- Liability Management (ALM) do fundo.",
                                "asset liability management")):
            rewrites = [m for record in normalize_text(text, "<test>", "text", lexicon)
                        for m in (record.get("canonical_mappings") or [])
                        if m["canonical"] == expected]
            self.assertTrue(rewrites, f"{text!r} no longer normalises to {expected!r}")


class AHierarchyWithACycleIsNotAHierarchy(unittest.TestCase):
    """The inverse checks are per EDGE and cannot see a loop.

    `A broader B`, `B broader C`, `C broader A` satisfies every inverse and still says each of
    the three is a kind of itself. The amendment op refuses the immediate inversion and is
    equally blind past one hop, so three separate, individually valid amendments compose into a
    cycle. Built over `technical.duration`, `technical.convexity` and `technical.irr` against
    the real loader, it used to load with no error at all.
    """

    @staticmethod
    def load_with(edges: list[tuple[str, str]]):
        from semantic_normalizer.registry import load_registry
        records = [json.loads(line)
                   for line in REGISTRY.read_text(encoding="utf-8").splitlines() if line.strip()]
        by_id = {record["concept_id"]: record for record in records}
        for narrower, broader in edges:
            by_id[narrower]["relations"]["broader"].append(broader)
            by_id[broader]["relations"]["narrower"].append(narrower)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.jsonl"
            path.write_text("\n".join(
                json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records) + "\n",
                encoding="utf-8")
            return load_registry(
                path, ROOT / "src" / "semantic_normalizer" / "data" / "registry.schema.json")

    def test_a_three_node_cycle_is_refused(self):
        from semantic_normalizer.registry import ContractError
        with self.assertRaises(ContractError) as caught:
            self.load_with([("technical.duration", "technical.convexity"),
                            ("technical.convexity", "technical.irr"),
                            ("technical.irr", "technical.duration")])
        self.assertIn("cycle", str(caught.exception))

    def test_the_message_names_the_path(self):
        """A cycle error that does not say which concepts are in it is unactionable."""
        from semantic_normalizer.registry import ContractError
        with self.assertRaises(ContractError) as caught:
            self.load_with([("technical.duration", "technical.convexity"),
                            ("technical.convexity", "technical.duration")])
        message = str(caught.exception)
        self.assertIn("technical.duration", message)
        self.assertIn("technical.convexity", message)
        self.assertIn("->", message)

    def test_the_shipped_registry_is_acyclic(self):
        from semantic_normalizer.registry import load_registry
        load_registry()  # raises if not

    def test_a_diamond_is_not_a_cycle(self):
        """`technical.ytm` already has two genera. Poly-hierarchy is legal; a loop is not."""
        view = self.load_with([])
        by_id = {r["concept_id"]: r for r in view["canonical_records"]}
        self.assertEqual(sorted(by_id["technical.ytm"]["relations"]["broader"]),
                         ["technical.discount_rate", "technical.irr"])


class TheWordnetGeneratorFiltersBeforeProposing(unittest.TestCase):
    """P4 shipped with no test at all — flagged by adversarial review, and it was right.

    Nothing would have broken if the prose-attestation filter or the already-owned filter
    regressed, and those two filters are the entire reason the generator is safe to run: they
    are what keep an invented spelling and a spelling another concept already claims out of the
    queue. The `wn` dependency is build-time, so the pieces are exercised without it.
    """

    def setUp(self):
        import propose_surfaces_from_wordnet as generator
        self.generator = generator
        self.lines = [
            "O pressuposto sustenta o argumento sem ser declarado.",
            "A premissa oculta escapa ao exame explícito.",
            "Um extrair mal feito destrói a evidência.",
        ]
        self.folded = [generator.fold(line) for line in self.lines]

    def test_a_surface_present_in_prose_is_cited(self):
        found = self.generator.attested("pressuposto", self.folded)
        self.assertEqual(len(found), 1)
        self.assertIn("pressuposto", found[0])

    def test_a_surface_absent_from_prose_gets_no_citation(self):
        """No citation means the proposal is never emitted — the invented-spelling guard."""
        self.assertEqual(self.generator.attested("pressuposição", self.folded), [])

    def test_a_substring_hit_is_not_attestation(self):
        """`trair` must not be attested by `extrair`, the defect the pipeline already paid for."""
        self.assertEqual(self.generator.attested("trair", self.folded), [])

    def test_the_citation_cap_is_respected(self):
        folded = [self.generator.fold("O pressuposto aparece.")] * 10
        self.assertEqual(len(self.generator.attested("pressuposto", folded)), 3)

    def test_prose_excludes_headings_and_captions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "c.md").write_text(
                "# Um título com pressuposto\n"
                "![legenda com pressuposto](a.png)\n"
                "Uma frase de prosa com pressuposto.\n", encoding="utf-8")
            lines = self.generator.prose(path)
        self.assertEqual(lines, ["Uma frase de prosa com pressuposto."])

    def test_the_one_admitted_proposal_is_in_the_registry(self):
        """`pressuposto` was the single survivor of the ten, admitted as a review form."""
        record = next(json.loads(line) for line in
                      REGISTRY.read_text(encoding="utf-8").splitlines()
                      if line.strip() and '"reasoning.premise"' in line)
        entry = [f for f in record["lexical_forms"]["pt-BR"] if f["form"] == "pressuposto"]
        self.assertEqual([f["policy"] for f in entry], ["review"])


class TheHierarchyEdgesAgreeWithTheDefinitions(unittest.TestCase):
    """A broader edge whose definitions contradict it is worse than no edge.

    `entity.bond` was linked under `entity.private_security` while its own English definition
    said "issued by a company or government" and the genus said "rather than by a government".
    The amendment's `reason` acknowledged the tension and left the field alone, so the artefact
    still carried the contradiction — which is where it matters.
    """

    def test_no_narrower_definition_contradicts_its_genus_on_the_issuer(self):
        by_id = {r["concept_id"]: r for r in
                 (json.loads(line) for line in
                  REGISTRY.read_text(encoding="utf-8").splitlines() if line.strip())}
        for cid, record in sorted(by_id.items()):
            for genus in record["relations"]["broader"]:
                narrow = record["definition"].casefold()
                broad = by_id[genus]["definition"].casefold()
                if "rather than by a government" in broad:
                    self.assertNotIn(
                        "or government", narrow,
                        f"{cid} is narrower than {genus}, which excludes governments, but its "
                        "own definition includes them")


class TheAmenderDemoterIsContextAware(unittest.TestCase):
    """An amendment about one thing must not destroy the packs' independence.

    Applying the hierarchy amendment with the old global demoter knocked BOTH `entity.premise`
    and `reasoning.premise` down to review — two concepts that never share a matcher table,
    because a scoped load builds from one context. The amendment was about six edges and it
    silently cost two automatic forms.
    """

    @staticmethod
    def record(cid: str, contexts: list[str], form: str) -> dict:
        return {"concept_id": cid, "contexts": contexts,
                "lexical_forms": {"pt-BR": [{"form": form, "features": {}, "policy": "auto"}],
                                  "en": []}}

    def test_disjoint_packs_sharing_a_surface_survive_an_amendment(self):
        current = [self.record("entity.premise", ["finance", "cga"], "premissa"),
                   self.record("reasoning.premise", ["reasoning"], "premissa")]
        self.assertEqual(demote_ambiguous(current), [])
        self.assertTrue(all(r["lexical_forms"]["pt-BR"][0]["policy"] == "auto" for r in current))

    def test_the_amender_imports_the_shared_demoter(self):
        source = (SCRIPTS / "amend_registry.py").read_text(encoding="utf-8")
        self.assertIn("from import_cga_batch import demote_ambiguous", source)
        self.assertNotIn("ambiguous = {key for key, ids in owners.items() if len(ids) > 1}",
                         source, "the old global demoter is still in place")


class LinkBroaderRefusesWhatWouldCorruptTheGraph(unittest.TestCase):
    def run_amendment(self, operations: list[dict]) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "amendment.json"
            path.write_text(json.dumps({
                "id": "test-canary", "corpus_sha256": "x", "authority": "test",
                "method": "test", "reason": "test", "operations": operations}),
                encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "amend_registry.py"), "--amendment", str(path),
                 "--registry-version", "2.41.0", "--dry-run"],
                capture_output=True, text=True, cwd=ROOT,
                env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
            return json.loads((result.stdout + result.stderr).strip().splitlines()[0])

    def test_an_inverse_edge_is_refused(self):
        outcome = self.run_amendment([{"op": "link_broader",
                                       "concept": "technical.duration",
                                       "broader": "technical.macaulay_duration"}])
        self.assertEqual(outcome["edges_linked"], 0)
        self.assertTrue(any("invert" in reason for reason in outcome["refused"]), outcome)

    def test_a_self_edge_is_refused(self):
        outcome = self.run_amendment([{"op": "link_broader",
                                       "concept": "technical.duration",
                                       "broader": "technical.duration"}])
        self.assertEqual(outcome["edges_linked"], 0)

    def test_an_unknown_target_is_refused(self):
        outcome = self.run_amendment([{"op": "link_broader",
                                       "concept": "technical.duration",
                                       "broader": "technical.does_not_exist"}])
        self.assertEqual(outcome["edges_linked"], 0)

    def test_an_edge_that_already_exists_is_refused(self):
        outcome = self.run_amendment([{"op": "link_broader",
                                       "concept": "technical.macaulay_duration",
                                       "broader": "technical.duration"}])
        self.assertEqual(outcome["edges_linked"], 0)
        self.assertTrue(any("already linked" in reason for reason in outcome["refused"]))


if __name__ == "__main__":
    unittest.main()
