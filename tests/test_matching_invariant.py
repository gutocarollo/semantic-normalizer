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

    def test_a_longer_form_wins_wherever_a_shorter_one_could_preempt_it(self):
        """Every pair where a shorter form could eat the head of a longer one.

        The pre-emption condition, stated exactly: a proper suffix of form A is a prefix of form
        B. Under a leftmost scan A is reached first, consumes the tokens B needs to start on, and
        B is never tried. The probe puts A's head immediately before B so the two compete.
        """
        pairs = []
        for _concept_a, _form_a, tokens_a in self.forms:
            if len(tokens_a) < 2:
                continue
            for cut in range(1, len(tokens_a)):
                tail = tokens_a[cut:]
                for _concept_b, form_b, tokens_b in self.forms:
                    if len(tokens_b) <= len(tokens_a) or tokens_b[:len(tail)] != tail:
                        continue
                    pairs.append((form_b, tokens_a[:cut]))
        self.assertTrue(pairs, "no pre-emption pairs found; the invariant would be vacuous")

        for long_form, prefix in pairs[:400]:
            sentence = f"O {' '.join(prefix)} {long_form} é relevante."
            with self.subTest(long_form=long_form[:44]):
                found = normalize_text(sentence, source="t", kind="text", lexicon=self.lexicon)[0]
                spans = {(event["alias"].casefold()) for event in found["match_events"]}
                self.assertIn(
                    long_form.casefold(), spans,
                    f"`{long_form}` lost to a shorter form starting earlier. The matcher must "
                    f"resolve overlaps by length, not by which form begins first.",
                )


if __name__ == "__main__":
    unittest.main()
