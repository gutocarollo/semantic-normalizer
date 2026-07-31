"""The matcher resolves overlaps by length, not by which form starts first.

An adversarial review found the scan was leftmost-then-longest: at each position it took the
longest form starting THERE and jumped past it, so a longer form beginning one token later never
got tried. Two sentences over the same phrase disagreed —

    "Growth at a Reasonable Price (GARP) é um estilo."   -> technical.garp
    "O estilo Growth at a Reasonable Price combina..."   -> `estilo Growth` won, GARP was lost,
                                                            and `Price` fired bare

— a recall loss and a false positive in one span.

Two sentences are not what makes this worth a module. The claim "register the compound and
longest-match retires the fragment" was the stated justification for dozens of repairs across
five review rounds, and under a leftmost scan it held only when the competing forms happened to
start at the same token. The registry contains 397 pairs where a proper suffix of one automatic
form is the head of another — `alocação de capital` × `capital regulatório`, `active risk` ×
`risk management`, `achatamento da curva` × `curva de juros` — every one of them a place where
the guarantee could fail silently.

So this asserts the invariant over the whole registry rather than over the two sentences that
exposed it: for every such pair, a probe sentence containing the long form must match the long
form. A regression in the resolution order fails here in bulk, not by luck of which example
someone remembered to write down.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from semantic_normalizer.normalizer import normalize_text  # noqa: E402
from semantic_normalizer.registry import load_lexicon, load_registry  # noqa: E402


def automatic_forms(records: list[dict]) -> list[tuple[str, str, tuple[str, ...]]]:
    out = []
    for record in records:
        for entries in record["lexical_forms"].values():
            for entry in entries:
                if entry["policy"] != "auto":
                    continue
                tokens = tuple(entry["form"].casefold().split())
                if tokens:
                    out.append((record["concept_id"], entry["form"], tokens))
    return out


class MatchingInvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lexicon = load_lexicon()
        cls.forms = automatic_forms(load_registry()["records"])

    def test_two_sentences_that_exposed_the_defect(self):
        """The literal probe from the review, both directions."""
        for sentence in (
            "Growth at a Reasonable Price (GARP) é um estilo.",
            "O estilo Growth at a Reasonable Price combina Growth e Value.",
        ):
            with self.subTest(sentence=sentence[:40]):
                found = normalize_text(sentence, source="t", kind="text", lexicon=self.lexicon)[0]
                self.assertIn("technical.garp", found["concept_ids"])
                self.assertNotIn("quantity.price", found["concept_ids"])

    def test_the_longer_form_wins_wherever_a_shorter_one_could_preempt_it(self):
        """Every pair where one form could eat the head of another, resolved by the real rule.

        The pre-emption condition, stated exactly: a proper suffix of form A is a prefix of form B,
        and B reaches PAST where A ends. The second half is what the first version of this test got
        wrong — it required B to be longer than the whole of A, which is a stricter condition and a
        different one. An adversarial review computed both against the registry as it then stood:
        123 pairs the narrow filter tested, 529 that qualified. Among the 406 it silently dropped
        was `classificação de risco` × `risco de crédito`, the second of the two examples that
        motivated the engine fix in the first place — so the guard read green while its own
        flagship case went unexercised.

        Those two counts are of a registry that has since grown; at 2.28.0 the corrected condition
        enumerates 767. The number is deliberately not hardcoded — the floor below only asserts the
        enumeration did not collapse — because a count pinned in a docstring goes stale the next
        time a batch lands, which is exactly what happened to the 529.

        Widening the net is only half the repair, and the half that is wrong on its own: once B may
        be SHORTER than A overall, asserting "B wins" asserts a falsehood, because the rule is
        length and A is longer. So the assertion is the invariant itself rather than a proxy for it —
        the longer form wins, and an exact tie goes to the leftmost, which is A by construction here.
        A guard that tests a proxy passes for reasons unrelated to what it claims.
        """
        pairs = []
        for _concept_a, form_a, tokens_a in self.forms:
            if len(tokens_a) < 2:
                continue
            for cut in range(1, len(tokens_a)):
                tail = tokens_a[cut:]
                for _concept_b, form_b, tokens_b in self.forms:
                    if len(tokens_b) <= len(tail) or tokens_b[:len(tail)] != tail:
                        continue
                    pairs.append((form_a, form_b, tokens_a, tokens_b, tokens_a[:cut]))
        self.assertGreater(
            len(pairs), 400,
            "the pre-emption enumeration collapsed; it covered 767 pairs at registry 2.28.0, and "
            "a drop to a few hundred means the filter narrowed rather than the registry shrinking",
        )

        for form_a, form_b, tokens_a, tokens_b, prefix in pairs:
            sentence = f"O {' '.join(prefix)} {form_b} é relevante."
            # Longer wins; an exact tie goes to whichever starts first, and that is A.
            expected = form_b if len(tokens_b) > len(tokens_a) else form_a
            wanted = len(expected.split())
            with self.subTest(long_form=form_b[:36], against=form_a[:26]):
                found = normalize_text(sentence, source="t", kind="text", lexicon=self.lexicon)[0]
                spans = {event["alias"].casefold() for event in found["match_events"]}
                # A third, still longer form may cover the whole probe — `classificação de risco`
                # against `risco de crédito` builds a sentence that contains the five-token
                # `classificação de risco de crédito`, and that form winning is the invariant
                # holding, not failing. So the claim is the one that cannot be satisfied by
                # anything shorter: nothing below the expected length may take this span.
                self.assertTrue(
                    expected.casefold() in spans
                    or any(len(alias.split()) > wanted for alias in spans),
                    f"`{form_a}` and `{form_b}` overlap in {sentence!r}. `{expected}` should have "
                    f"won, or a longer form should have taken the whole span; instead the matcher "
                    f"settled for {sorted(spans)}. Overlaps resolve by LENGTH, and by leftmost "
                    f"only on an exact tie.",
                )


if __name__ == "__main__":
    unittest.main()
