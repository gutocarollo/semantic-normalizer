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
