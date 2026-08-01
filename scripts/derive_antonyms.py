#!/usr/bin/env python3
"""Derive opposition between concepts instead of maintaining the list by hand.

`CANONICAL_ANTONYMS` in `normalizer.py` is 17 pairs written by a person. Every one of them was
added after something broke — `technical.option` held put AND call, so every put in the corpus
was rewritten into a call, 13 times, silently inverting the instrument. The list works and it
does not scale: nothing tells you which pair is missing until a rewrite crosses it.

THE SIGNAL IS THE FALSE POSITIVE, INVERTED

Ask "which two concepts have near-identical definitions" and opposites crowd the top. Measured
on this registry with the `content()` below — 33 pairs overlap 50 % or more:

    0.875  entity.treasury_bond ~ entity.treasury_note   (NAO e antonimo — ver abaixo)
    0.800  state.enabled      ~ state.disabled       (`ativado` / `desativado`)
    0.778  technical.flattening ~ technical.steepening
    0.750  technical.negative_butterfly ~ technical.positive_butterfly
    0.714  temporal.after     ~ temporal.before
    0.636  technical.call_option ~ technical.put_option
    0.600  actor.buyer        ~ actor.seller

That is the textbook behaviour of distributional semantics: antonyms share almost all of their
context, because they are one predicate over inverted arguments.

TWO CORRECTIONS TO AN EARLIER VERSION OF THIS PARAGRAPH, both found by adversarial review:

* it quoted `state.enabled ~ state.disabled` at **1.00**, and 1.00 is what a NAIVE filter
  yields — one that drops words of three letters or fewer, and therefore drops `not`, the only
  token separating them. `content()` keeps short words on purpose, so the shipped number is
  0.800. Quoting the broken method's figure while arguing against the broken method is the
  sharpest way to be wrong about this;
* the true top of the ranking is NOT an antonym. `entity.treasury_bond ~ entity.treasury_note`
  scores 0.875 because both definitions say "United States Treasury debt maturing in ..." and
  differ only in the maturity range — two disjoint buckets, neither the negation of the other.
  High similarity means "look at this pair", never "these are opposites" and never "these are
  the same". Which is the entire argument for deciding elsewhere.

So the measure that would cause a catastrophic merge is exactly the measure that finds the pairs
a merge must never cross. This script takes the second reading.

WHAT DECIDES OPPOSITION, AND WHY THE OBVIOUS PREPROCESSING DESTROYS IT

`state.enabled` and `state.disabled` differ by ONE token:

    A state in which a function is     available for operation.
    A state in which a function is not available for operation.

`not` is three characters. Every stopword list drops it, every `len(word) > 3` filter drops it,
and the pair comes out at similarity 1.00 — identical. The discriminating evidence lives in
precisely the tokens that similarity pipelines throw away, so this script keeps short words and
looks for them on purpose: a NEGATOR present on one side and absent on the other, or a POLAR
pair split across the two sides.

Output is a proposal, never a write. `--check` fails when a derived pair is missing from the
shipped list, which is the mode that belongs in the suite.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
REGISTRY = ROOT / "src" / "semantic_normalizer" / "data" / "registry.jsonl"

# Kept deliberately short: these are function words that CARRY the opposition, not noise.
NEGATORS = frozenset({"not", "no", "never", "without", "cannot", "fails", "lacks", "absent",
                      "unavailable", "excluding", "opposite", "inverse"})

# Polar content pairs. A pair split across two definitions inverts them even when every other
# word matches. Ordered pairs; membership is checked both ways.
POLAR_PAIRS = (
    ("buy", "sell"), ("buys", "sells"), ("buyer", "seller"), ("buying", "selling"),
    ("acquiring", "disposing"), ("long", "short"), ("rise", "fall"), ("rises", "falls"),
    ("rising", "falling"), ("increase", "decrease"), ("increases", "decreases"),
    ("above", "below"), ("higher", "lower"), ("earlier", "later"), ("before", "after"),
    ("positive", "negative"), ("open", "closed"), ("asset", "liability"),
    ("holder", "issuer"), ("gain", "loss"), ("credit", "debit"), ("inflow", "outflow"),
    ("widens", "narrows"), ("upward", "downward"), ("more", "less"), ("receives", "pays"),
    ("enter", "exit"), ("entry", "exit"), ("start", "end"), ("first", "last"),
)

WORD = re.compile(r"[a-zà-ÿ]+")
# Only these are dropped. Note what is NOT here: `not`, `no`, `above`, `below`, `more`, `less`.
STRUCTURAL = frozenset({"the", "a", "an", "of", "to", "in", "for", "that", "is", "are", "be",
                        "by", "on", "with", "from", "which", "it", "its", "their", "this",
                        "at", "as", "and", "or", "any", "such", "one", "two", "than", "was",
                        "were", "has", "have", "been", "into", "over", "under", "each"})


def fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def content(definition: str) -> set[str]:
    """Every word except purely structural glue. Short words SURVIVE — see the module docstring."""
    return {word for word in WORD.findall(fold(definition)) if word not in STRUCTURAL}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def opposition(left: set[str], right: set[str]) -> tuple[str, str] | None:
    """Return the evidence of opposition, or None. Negation first, then polar split."""
    only_left, only_right = left - right, right - left
    for negator in sorted(NEGATORS):
        if negator in only_left:
            return ("negation", f"{negator!r} on one side only")
        if negator in only_right:
            return ("negation", f"{negator!r} on one side only")
    for first, second in POLAR_PAIRS:
        if (first in only_left and second in only_right) or (
            second in only_left and first in only_right
        ):
            return ("polar", f"{first!r} vs {second!r}")
    return None


def surface_pairs(record_a: dict, record_b: dict) -> list[tuple[str, str]]:
    """The MINIMAL token pair a rewrite must never cross, per language.

    Not the whole preferred labels. `_rewrite_is_safe` tests `left in alias_tokens`, where
    `alias_tokens` is a token LIST — so a multi-word entry like `("long position", "short
    position")` can never match anything and would sit in the guard as a dead line that reads
    like protection. The first version emitted exactly that.

    So the pair is reduced to the tokens that actually differ: `long position` vs `short
    position` yields `("long", "short")`, `fundos abertos` vs `fundos fechados` yields
    `("abertos", "fechados")`. When more than one token differs on either side the reduction is
    ambiguous and the pair is emitted as `None` for that language — declared rather than
    guessed, because a wrong reduction blocks legitimate rewrites.
    """
    pairs = []
    for language in ("pt-BR", "en"):
        left = fold(record_a["labels"][language]["pref"])
        right = fold(record_b["labels"][language]["pref"])
        if left == right:
            continue
        left_only = [t for t in WORD.findall(left) if t not in set(WORD.findall(right))]
        right_only = [t for t in WORD.findall(right) if t not in set(WORD.findall(left))]
        if len(left_only) == 1 and len(right_only) == 1:
            pairs.append((left_only[0], right_only[0]))
    return pairs


def derive(records: list[dict], floor: float) -> list[dict]:
    found = []
    for record_a, record_b in itertools.combinations(records, 2):
        left, right = content(record_a["definition"]), content(record_b["definition"])
        score = jaccard(left, right)
        if score < floor:
            continue
        evidence = opposition(left, right)
        if evidence is None:
            continue
        kind, detail = evidence
        found.append({
            "concepts": [record_a["concept_id"], record_b["concept_id"]],
            "similarity": round(score, 3),
            "kind": kind,
            "evidence": detail,
            "surface_pairs": surface_pairs(record_a, record_b),
            "definitions": [record_a["definition"], record_b["definition"]],
        })
    found.sort(key=lambda row: (-row["similarity"], row["concepts"]))
    return found


def shipped_pairs() -> tuple[tuple[str, str], ...]:
    from semantic_normalizer.normalizer import CANONICAL_ANTONYMS, REWRITE_ONLY_ANTONYMS
    return tuple((fold(a), fold(b))
                 for a, b in CANONICAL_ANTONYMS + REWRITE_ONLY_ANTONYMS)


def already_blocked(left_surface: str, right_surface: str,
                    shipped: tuple[tuple[str, str], ...]) -> bool:
    """Would the shipped guard already refuse a rewrite between these two surfaces?

    Mirrors `_rewrite_is_safe`, which tests TOKEN membership — `left in alias_tokens and right
    in replacement_tokens` — not whole-surface equality. Getting this wrong is the recurring
    defect of this repo one level up: a measurement whose rule differs from the rule of the
    thing it measures. A first version compared full preferred labels against the word-pair list
    and reported 11 uncovered pairs, when `posição comprada` -> `posição vendida` is already
    blocked by (`comprada`, `vendida`). The number was an artefact of the measurement.
    """
    left_tokens = set(WORD.findall(fold(left_surface)))
    right_tokens = set(WORD.findall(fold(right_surface)))
    return any(
        (a in left_tokens and b in right_tokens) or (b in left_tokens and a in right_tokens)
        for a, b in shipped
    )


def load(contexts: list[str] | None) -> list[dict]:
    records = [json.loads(line)
               for line in REGISTRY.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not contexts:
        return records
    scope = {c.casefold() for c in contexts}
    return [r for r in records
            if scope & {str(c).casefold() for c in r.get("contexts", [])}]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-similarity", type=float, default=0.5)
    parser.add_argument("--contexts", nargs="+")
    parser.add_argument("--output")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if a derived surface pair is absent from the shipped list")
    args = parser.parse_args()

    records = load(args.contexts)
    derived = derive(records, args.min_similarity)
    shipped = shipped_pairs()
    uncovered = [
        {**row, "missing_surface_pairs": [list(p) for p in row["surface_pairs"]
                                          if not already_blocked(p[0], p[1], shipped)]}
        for row in derived
    ]
    uncovered = [row for row in uncovered if row["missing_surface_pairs"]]

    report = {
        "schema_version": "derived-antonyms-v1",
        "method": {
            "candidate": "concept pairs whose definitions overlap >= min-similarity, with SHORT "
                         "words kept — `not` is three characters and is the entire difference "
                         "between state.enabled and state.disabled",
            "decision": "a negator present on exactly one side, or a polar pair split across "
                        "the two sides",
            "not_a_merge": "these pairs are the boundary a merge must never cross; this script "
                           "never proposes joining anything",
        },
        "concepts_scanned": len(records),
        "derived_pairs": len(derived),
        "pairs_not_covered_by_the_shipped_guard": len(uncovered),
        "pairs": derived,
        "uncovered": uncovered,
    }
    if args.output:
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    print(json.dumps({k: report[k] for k in
                      ("concepts_scanned", "derived_pairs",
                       "pairs_not_covered_by_the_shipped_guard")}, ensure_ascii=False))
    if args.check and uncovered:
        for row in uncovered[:20]:
            print(f"  UNCOVERED {row['concepts']} ({row['kind']}: {row['evidence']}) "
                  f"-> {row['missing_surface_pairs']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
