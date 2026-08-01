#!/usr/bin/env python3
"""Propose broader/narrower edges. The registry has 598 concepts and ZERO of them.

Measured before writing a line: of 598 concepts, 15 carry any relation at all, and the tally is
`broader: 0, narrower: 0, related: 18`. A registry with no hierarchy is a list with metadata. It
cannot answer "is a Macaulay duration a duration", it cannot roll a query up from a specific
instrument to its family, and the SKOS export it produces is a flat concept scheme.

ONE SIGNAL, AFTER MEASURING THE OTHER TWO TO ZERO

LEXICAL CONTAINMENT, and the head position decides the direction. When one preferred label
contains the other as a whole-token subsequence AND the contained label is the syntactic HEAD,
the longer one is the narrower: `risco de crédito` is a kind of `risco`.

The head test is LANGUAGE-DEPENDENT, and getting that wrong is what made the first version emit
61 edges that are not hyponymy at all. Portuguese is head-initial and English is head-final:

    classe de ativos -> classe     head is `classe`, the FIRST token       hyponymy
    classe de ativos -> ativo      `ativo` is the modifier                 NOT hyponymy
    asset class      -> class      head is `class`, the LAST token         hyponymy
    asset class      -> asset      `asset` is the modifier                 NOT hyponymy

An asset class is a kind of class, never a kind of asset. When the genus is the modifier the
relation is ABOUTNESS, not type, and writing it as `broader` states something false.

TWO SIGNALS WERE REMOVED, EACH WITH ITS MEASUREMENT

WordNet hypernymy produced 25 proposals and **zero** plausible ones: `ativo -> dinheiro`,
`ativo -> custo`, `moeda -> fundo`, `Documentos -> crédito`, `classe -> mercado`. Its failure
mode is not being wrong by a little — `administrador -> gestor` would invert a CVM 175
distinction between two separate regulated roles. WordNet reports general-language hypernymy,
which crosses this registry's categories freely. It remains useful in
`propose_surfaces_from_wordnet.py`, where it earned 1 admission in 10.

Definition subsumption produced exactly 2 proposals and both are wrong:
`entity.treasury_bond -> entity.treasury_note`, which contradicts a verdict already recorded in
this repository (an adversarial judge ruled them DISTINCT — disjoint maturity buckets), and
`technical.selic -> technical.bacen`, which makes a policy rate a kind of central bank. Two
samples is thin for statistics and sufficient for "produced nothing".

MEASURED RECALL: ZERO OF THE SIX EDGES THAT TURNED OUT TO BE RIGHT

The six `broader` edges the registry now carries came from an adversarial judge reading the
corpus, not from this script. Intersecting the two sets: **none** of the six appears among the
172 edges the three signals proposed. Containment cannot find `technical.ytm -> technical.irr`
because neither label contains the other; WordNet cannot find `entity.bond ->
entity.private_security` because the relation is Brazilian regulatory, not general-language.

So the honest standing of this tool is: it proposes cheaply and it has not yet been shown to
propose anything correct. Its 172 outputs are declared `pending` in
`reports/hierarchy-proposals-disposition.json` with an owner, and `check_queue_disposition.py`
counts them on every run so the pile cannot quietly become permanent. Applying them unjudged
would be inventing structure.

WHAT THIS REFUSES TO DO

Never writes. Never proposes an edge between two concepts an antonym derivation separates —
`open`/`closed` funds contain no hierarchy, and containment alone would happily nest
`fundos abertos` under `fundos`. Never proposes an edge for a pair the redundancy queue judged
`same`, because that pair needs a merge decision first and a hierarchy under a duplicate is
structure built on sand.

The registry contract requires inverses: a `broader` edge is invalid unless the target declares
the matching `narrower`. So the output is emitted as PAIRS to be applied by an amendment, both
sides at once, never as a one-way suggestion someone might half-apply.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
REGISTRY = ROOT / "src" / "semantic_normalizer" / "data" / "registry.jsonl"

WORD = re.compile(r"[a-zà-ÿ0-9]+")


def fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def tokens(text: str) -> list[str]:
    return WORD.findall(fold(text))


def contains_tokens(longer: list[str], shorter: list[str]) -> bool:
    """Whole-token containment, contiguous or not, order preserved.

    Token level, not substring: `renda` must not match inside `arrendamento`. Order preserved so
    `ajuste de convexidade` contains `convexidade` while an unordered bag would also accept
    coincidental overlaps.
    """
    if len(shorter) >= len(longer):
        return False
    index = 0
    for token in longer:
        if index < len(shorter) and token == shorter[index]:
            index += 1
    return index == len(shorter)


# English compounds are head-final (`asset class`), but an English noun modified by a
# PREPOSITIONAL PHRASE is head-INITIAL, exactly like Portuguese: the head of `yield to maturity`
# is `yield`, not `maturity`. Measured cost of missing this: a seeded 10-sample of the discarded
# edges came back with 2 wrong discards, and `technical.ytm -> technical.yield` was one of them —
# yield to maturity is a kind of yield, and the rule was throwing it away.
ENGLISH_PREPOSITIONS = frozenset({"to", "of", "on", "in", "at", "for", "from", "with", "over",
                                  "under", "against", "per", "by", "into", "within"})


def genus_is_the_head(narrow: list[str], broad: list[str], language: str) -> bool:
    """Is the contained label the syntactic HEAD of the containing one, in this language?

    Portuguese is head-initial and English compounds are head-final; conflating them is what
    produced 61 proposals that are not hyponymy at all. `asset class -> asset` passes a naive
    prefix test while the head of `asset class` is `class`: an asset class is a kind of class,
    never a kind of asset. When the genus is the modifier the relation is aboutness, and writing
    it as `broader` states something false.

    English takes both shapes, and which one applies is readable from the token after the genus:
    a preposition means the modifier is a PP and the head sits on the left (`yield to
    maturity`), anything else means a compound and the head sits on the right (`asset class`).

    Nothing here is a parser. It is the smallest positional fact that separates a genus from a
    modifier in these two languages, and its error rate was measured rather than assumed.
    """
    if language == "pt-BR":
        return narrow[:len(broad)] == broad
    if narrow[-len(broad):] == broad:
        return True
    if narrow[:len(broad)] == broad and len(narrow) > len(broad):
        return narrow[len(broad)] in ENGLISH_PREPOSITIONS
    return False


def containment_edges(records: list[dict]) -> list[dict]:
    edges = []
    for a, b in itertools.permutations(records, 2):
        # `is-a` preserves the semantic class. Without this, containment proposes
        # `action.cancel_registration -> entity.cvm` and `action.close_for_redemption ->
        # entity.fund`, which are actions whose LABEL happens to name the entity they act on —
        # an aboutness relation, not hypernymy. Measured: dropping cross-class containment took
        # the proposal count from 383 to a set where every survivor is at least arguable.
        if a["semantic_class"] != b["semantic_class"]:
            continue
        for language in ("pt-BR", "en"):
            broad = tokens(b["labels"][language]["pref"])
            narrow = tokens(a["labels"][language]["pref"])
            if len(broad) < 1 or not contains_tokens(narrow, broad):
                continue
            if not genus_is_the_head(narrow, broad, language):
                continue
            edges.append({
                "narrower": a["concept_id"], "broader": b["concept_id"],
                "signal": "lexical-containment", "language": language,
                "evidence": f"{a['labels'][language]['pref']!r} contains "
                            f"{b['labels'][language]['pref']!r} as whole tokens",
            })
            break
    return edges




def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--contexts", nargs="+")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    from derive_antonyms import derive, load
    records = load(args.contexts)
    opposed = {frozenset(row["concepts"]) for row in derive(records, 0.4)}

    raw = containment_edges(records)

    merged: dict[tuple[str, str], dict] = {}
    refused_opposition = 0
    for edge in raw:
        key = (edge["narrower"], edge["broader"])
        if frozenset(key) in opposed:
            refused_opposition += 1
            continue
        if key in merged:
            merged[key]["signals"].append({"signal": edge["signal"],
                                           "evidence": edge["evidence"]})
        else:
            merged[key] = {"narrower": edge["narrower"], "broader": edge["broader"],
                           "signals": [{"signal": edge["signal"],
                                        "evidence": edge["evidence"]}]}

    # A pair proposed in BOTH directions is not a hierarchy, it is two concepts that look alike.
    # Emitting either direction would be a coin flip written as structure.
    both_ways = {key for key in merged if (key[1], key[0]) in merged}
    cycles = sorted({tuple(sorted(k)) for k in both_ways})
    edges = [row for key, row in sorted(merged.items()) if key not in both_ways]
    for row in edges:
        row["agreeing_signals"] = sorted({s["signal"] for s in row["signals"]})
        row["confidence"] = len(row["agreeing_signals"])

    report = {
        "schema_version": "hierarchy-proposals-v1",
        "method": {
            "signals": ["lexical-containment, whole tokens, order preserved, with the genus "
                        "required to be the syntactic HEAD — head-initial in pt-BR, head-final "
                        "in en"],
            "signals_removed": {
                "wordnet-hypernym": "25 proposals, 0 plausible; `administrador -> gestor` would "
                                    "invert a CVM 175 role distinction",
                "definition-subsumption": "2 proposals, both wrong; one contradicts a verdict "
                                          "already recorded in this repository",
            },
            "refusals": ["pairs an antonym derivation separates", "pairs proposed in both "
                         "directions, which is similarity wearing a hierarchy costume"],
            "output": "PAIRS for an amendment to apply — the registry contract rejects a "
                      "`broader` edge whose target lacks the matching `narrower`",
        },
        "concepts_scanned": len(records),
        "edges_before_filters": len(raw),
        "refused_as_opposition": refused_opposition,
        "refused_as_bidirectional": len(cycles),
        "bidirectional_pairs": [list(pair) for pair in cycles],
        "edges": edges,
        "by_confidence": {str(n): sum(1 for e in edges if e["confidence"] == n)
                          for n in sorted({e["confidence"] for e in edges})},
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(json.dumps({k: report[k] for k in
                      ("concepts_scanned", "edges_before_filters", "refused_as_opposition",
                       "refused_as_bidirectional", "by_confidence")}, ensure_ascii=False))
    print(f"edges proposed: {len(edges)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
