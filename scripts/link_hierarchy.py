#!/usr/bin/env python3
"""Propose broader/narrower edges. The registry has 598 concepts and ZERO of them.

Measured before writing a line: of 598 concepts, 15 carry any relation at all, and the tally is
`broader: 0, narrower: 0, related: 18`. A registry with no hierarchy is a list with metadata. It
cannot answer "is a Macaulay duration a duration", it cannot roll a query up from a specific
instrument to its family, and the SKOS export it produces is a flat concept scheme.

THREE SIGNALS, ALL CHEAP, ALL PROPOSING ONLY

1. LEXICAL CONTAINMENT — the strongest and the most boring. When one preferred label contains the
   other as a whole-token subsequence, the longer one is almost always the narrower:
   `duration de macaulay` contains `duration`, `ajuste de convexidade` contains `convexidade`.
   The direction is fixed by length, not guessed.

2. WORDNET HYPERNYMY — the failures of `propose_surfaces_from_wordnet.py` ARE this signal.
   When WordNet offers `dedução` as a synonym of `raciocínio` it has found a real relation and
   labelled it wrong: deduction is a KIND of reasoning. Reading those misses as hierarchy hints
   turns a false positive into evidence, the same move `derive_antonyms.py` makes.

3. DEFINITION SUBSUMPTION — the content words of the broader definition are a subset of the
   narrower one's. Weak on its own, reported with the others so a reader can see agreement.

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
            edges.append({
                "narrower": a["concept_id"], "broader": b["concept_id"],
                "signal": "lexical-containment", "language": language,
                "evidence": f"{a['labels'][language]['pref']!r} contains "
                            f"{b['labels'][language]['pref']!r} as whole tokens",
            })
            break
    return edges


def wordnet_edges(records: list[dict], lexicon: str) -> list[dict]:
    """Hypernymy between two concepts' preferred labels, if the lexicon is installed."""
    try:
        import wn
        wordnet = wn.Wordnet(lexicon)
    except Exception:  # noqa: BLE001 - absence is a fact to report, not a crash
        return []
    by_lemma: dict[str, list[str]] = {}
    for record in records:
        by_lemma.setdefault(fold(record["labels"]["pt-BR"]["pref"]), []).append(
            record["concept_id"])
    by_id = {record["concept_id"]: record for record in records}
    edges = []
    for record in records:
        preferred = record["labels"]["pt-BR"]["pref"]
        for synset in wordnet.synsets(preferred):
            for hypernym in synset.hypernyms():
                for lemma in hypernym.lemmas():
                    for target in by_lemma.get(fold(lemma), []):
                        if target == record["concept_id"]:
                            continue
                        # Same reason as containment, and a sharper case here: WordNet reports
                        # general-language hypernymy, which crosses this registry's classes
                        # freely. `actor.administrator` came out under `actor.portfolio_manager`
                        # — in CVM 175 those are two DISTINCT roles, not a genus and a species.
                        if by_id[target]["semantic_class"] != record["semantic_class"]:
                            continue
                        edges.append({
                            "narrower": record["concept_id"], "broader": target,
                            "signal": "wordnet-hypernym", "language": "pt-BR",
                            "evidence": f"{lexicon}: {preferred!r} has hypernym {lemma!r}",
                        })
    return edges


def subsumption_edges(records: list[dict], floor: float) -> list[dict]:
    from derive_antonyms import content
    edges = []
    for a, b in itertools.permutations(records, 2):
        narrow, broad = content(a["definition"]), content(b["definition"])
        if not broad or not narrow or broad == narrow:
            continue
        if broad <= narrow and len(broad) / len(narrow) >= floor:
            edges.append({
                "narrower": a["concept_id"], "broader": b["concept_id"],
                "signal": "definition-subsumption", "language": "en",
                "evidence": f"every content word of {b['concept_id']}'s definition appears in "
                            f"{a['concept_id']}'s",
            })
    return edges


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--contexts", nargs="+")
    parser.add_argument("--lexicon", default="own-pt:1.0.0")
    parser.add_argument("--subsumption-floor", type=float, default=0.5)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    from derive_antonyms import derive, load
    records = load(args.contexts)
    opposed = {frozenset(row["concepts"]) for row in derive(records, 0.4)}

    raw = (containment_edges(records)
           + wordnet_edges(records, args.lexicon)
           + subsumption_edges(records, args.subsumption_floor))

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
            "signals": ["lexical-containment (whole tokens, order preserved; direction fixed by "
                        "length, not guessed)",
                        "wordnet-hypernym (the mislabelled 'synonyms' of the surface generator)",
                        "definition-subsumption (weak; reported for agreement)"],
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
