#!/usr/bin/env python3
"""Find concepts that say the same thing — by SUBSTITUTION in the corpus, not by similar text.

The question this answers: two concepts with different spellings but one meaning are two
dictionary entries doing one job, and nothing in this repo looked for them. The collision demoter
only sees concepts that share a SURFACE; `entity.bond` and `entity.private_security` never
collide and may still be the same referent.

WHY THE OBVIOUS METHOD IS THE WRONG ONE, WITH THE MEASUREMENT

Rank the 598 shipped concepts by how similar their definitions are and opposites crowd the top:

    0.875  entity.treasury_bond ~ entity.treasury_note   (NAO e antonimo — ver abaixo)
    0.800  state.enabled      ~ state.disabled       (`ativado` / `desativado`)
    0.778  technical.flattening ~ technical.steepening
    0.750  technical.negative_butterfly ~ technical.positive_butterfly
    0.714  temporal.after     ~ temporal.before
    0.636  technical.call_option ~ technical.put_option
    0.600  actor.buyer        ~ actor.seller

The top entry is the warning about the method itself: `treasury_bond` and `treasury_note` are
neither opposites nor the same thing — two disjoint maturity buckets whose definitions differ
only in a numeral. High similarity means "look at this pair" and nothing more.

Antonyms share nearly all of their context, because they are one predicate over inverted
arguments. Merging on textual similarity would have joined put with call — the exact defect this
repo already paid for once, when `technical.option` held both and 13 puts in the corpus were
rewritten into calls.

So similarity is used here ONLY to propose, never to decide, and `derive_antonyms.py` runs first:
any pair whose opposition is derivable is removed from the candidate list before anything else
looks at it.

THE SUBSTITUTION TEST: NECESSARY, AND MEASURED NOT SUFFICIENT

If A and B are one concept, then replacing A's surface with B's preferred label in the sentences
where A actually occurs must leave a sentence that still means what it meant. The check runs the
engine's own `_rewrite_is_safe` — deliberately the same rule the matcher applies, so what the
engine refuses to substitute at match time cannot be merged at modelling time — over real corpus
occurrences, in BOTH directions.

Its measured power is small and saying otherwise would make this a guard that only ever passes.
On the CGA pack: 51 pairs reached it, it separated exactly **1**. Zero pairs came out one-way.
`_rewrite_is_safe` is a STRUCTURAL guard — it refuses antonym crossings, truncated compounds and
stripped copulas — and structural safety is not identity of meaning. `technical.sharpe_ratio` and
`technical.treynor` substitute cleanly and are different ratios.

So the honest contract of this script is: it removes the pairs that are DANGEROUS to merge, and
hands everything else to judgement. The bucket is named `needs_judgement`, not `merge_candidate`,
because nothing here established that any pair should merge.

A HYPOTHESIS THAT WAS TESTED AND FAILED

Before settling for that, a cheaper discriminator was tried: true synonyms should rarely share a
sentence (nobody writes "the Sharpe ratio and the Sharpe ratio"), so co-occurrence would mark a
pair distinct. Measured over the CGA corpus it does not separate: `technical.bacen` ~
`technical.selic` co-occur ZERO times and are plainly different things, while `technical.duration`
~ `technical.macaulay_duration` co-occur 9 times and are the clearest hierarchy pair in the set.
The signal is recorded per pair as `co_occurrences` for a reader, and no threshold acts on it.

The output is a QUEUE. Nothing here writes to the registry. Survivors go to the same adversarial
node that already refuses 39 % of what reaches it, with the question inverted: prove these two are
NOT the same concept.
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

WORD = re.compile(r"[a-zà-ÿ]+")
HEADING = re.compile(r"^\s{0,3}#")


def fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def prose_sentences(corpus: Path) -> list[str]:
    """Sentences that count as attestation: no headings, no image captions."""
    out = []
    for path in sorted(corpus.glob("*.md")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if HEADING.match(line) or "![" in line or not line.strip():
                continue
            for piece in re.split(r"(?<=[.!?])\s+", line.strip()):
                if 20 <= len(piece) <= 400:
                    out.append(piece)
    return out


def surfaces_of(record: dict, language: str) -> list[str]:
    return [form["form"] for form in record["lexical_forms"][language]]


def occurrences(surface: str, sentences: list[str], cap: int) -> list[tuple[str, str]]:
    """(sentence, matched_text) for word-boundary hits. Hyphen is not a boundary."""
    needle = re.escape(fold(surface))
    pattern = re.compile(rf"(?<![a-zà-ÿ0-9\-]){needle}(?![a-zà-ÿ0-9\-])")
    found = []
    for sentence in sentences:
        if pattern.search(fold(sentence)):
            found.append((sentence, surface))
            if len(found) >= cap:
                break
    return found


def substitution_is_safe(alias: str, replacement: str) -> tuple[bool, str]:
    """Run the ENGINE's own rewrite guard, not a second opinion about rewriting.

    Reusing `_rewrite_is_safe` is the point: whatever the engine refuses to substitute at match
    time is exactly what must not be merged at modelling time. A separate implementation here
    would be a second rule that could drift from the one that ships.
    """
    from semantic_normalizer.normalizer import _rewrite_is_safe
    ok = _rewrite_is_safe(alias, replacement)
    return ok, "" if ok else "the engine's rewrite guard refuses this substitution"


def sentence_hits(record: dict, folded: list[str]) -> set[int]:
    """Indices of prose sentences carrying any surface of this concept."""
    patterns = []
    for language in ("pt-BR", "en"):
        for form in record["lexical_forms"][language]:
            needle = re.escape(fold(form["form"]))
            patterns.append(re.compile(rf"(?<![a-zà-ÿ0-9\-]){needle}(?![a-zà-ÿ0-9\-])"))
    return {index for index, sentence in enumerate(folded)
            if any(pattern.search(sentence) for pattern in patterns)}


def test_pair(record_a: dict, record_b: dict, sentences: list[str],
              cap: int, folded: list[str] | None = None) -> dict:
    """Substitute each concept's surfaces with the other's preferred label, both directions."""
    result = {
        "concepts": [record_a["concept_id"], record_b["concept_id"]],
        "prefs": {},
        "directions": {},
        "verdict": "no-evidence",
    }
    for language in ("pt-BR", "en"):
        result["prefs"][language] = [record_a["labels"][language]["pref"],
                                     record_b["labels"][language]["pref"]]
    directions = []
    for first, second, tag in ((record_a, record_b, "a->b"), (record_b, record_a, "b->a")):
        target = second["labels"]["pt-BR"]["pref"]
        tested, unsafe, examples = 0, 0, []
        for language in ("pt-BR",):
            for surface in surfaces_of(first, language):
                for sentence, matched in occurrences(surface, sentences, cap):
                    tested += 1
                    ok, why = substitution_is_safe(matched, target)
                    if not ok:
                        unsafe += 1
                        if len(examples) < 3:
                            examples.append({"sentence": sentence[:160],
                                             "from": matched, "to": target, "why": why})
        directions.append({"direction": tag, "tested": tested, "unsafe": unsafe,
                           "examples": examples})
    result["directions"] = {d["direction"]: d for d in directions}
    if folded is not None:
        # Reported, never thresholded — see the module docstring for why this signal failed.
        result["co_occurrences"] = len(
            sentence_hits(record_a, folded) & sentence_hits(record_b, folded))
    a_to_b, b_to_a = directions[0], directions[1]
    if a_to_b["tested"] == 0 or b_to_a["tested"] == 0:
        result["verdict"] = "no-evidence"          # one side never occurs in this corpus
    elif a_to_b["unsafe"] == 0 and b_to_a["unsafe"] == 0:
        result["verdict"] = "needs-judgement"      # not dangerous; identity NOT established
    elif a_to_b["unsafe"] == 0 or b_to_a["unsafe"] == 0:
        result["verdict"] = "one-way"              # asymmetric: a hierarchy shape, never a merge
    else:
        result["verdict"] = "structurally-unsafe"  # both directions refused: do not merge
    return result


def content(definition: str) -> set[str]:
    from derive_antonyms import content as shared
    return shared(definition)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--contexts", nargs="+")
    parser.add_argument("--min-similarity", type=float, default=0.34,
                        help="proposal floor only; the decision is the substitution test")
    parser.add_argument("--occurrences", type=int, default=8,
                        help="occurrences sampled per surface per direction")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    from derive_antonyms import derive, jaccard, load
    records = load(args.contexts)
    corpus = Path(args.corpus)
    if not corpus.is_dir():
        raise SystemExit(f"fail-closed: corpus is not a directory: {corpus}")
    sentences = prose_sentences(corpus)
    if not sentences:
        raise SystemExit("fail-closed: the corpus produced no prose sentences")

    # Oppositions are removed BEFORE anything else looks at the pair. This is the whole safety
    # argument: the similarity measure that proposes candidates is the same measure that ranks
    # antonyms highest, so the antonym derivation has to run first, not as a later filter.
    antonym_pairs = {frozenset(row["concepts"]) for row in derive(records, 0.4)}

    proposed, skipped_antonym = [], 0
    for record_a, record_b in itertools.combinations(records, 2):
        if frozenset((record_a["concept_id"], record_b["concept_id"])) in antonym_pairs:
            skipped_antonym += 1
            continue
        score = jaccard(content(record_a["definition"]), content(record_b["definition"]))
        if score >= args.min_similarity:
            proposed.append((score, record_a, record_b))
    proposed.sort(key=lambda row: -row[0])

    folded = [fold(sentence) for sentence in sentences]
    tested = []
    for score, record_a, record_b in proposed:
        outcome = test_pair(record_a, record_b, sentences, args.occurrences, folded)
        outcome["similarity"] = round(score, 3)
        tested.append(outcome)

    buckets: dict[str, list] = {}
    for row in tested:
        buckets.setdefault(row["verdict"], []).append(row)

    report = {
        "schema_version": "redundant-concepts-v1",
        "method": {
            "proposal": "definition overlap >= min-similarity, AFTER removing every pair whose "
                        "opposition derive_antonyms.py can derive",
            "decision": "substitute each concept's surfaces with the other's preferred label in "
                        "REAL corpus sentences and run the engine's own _rewrite_is_safe; a "
                        "merge candidate must be safe in BOTH directions",
            "why_not_similarity": "ranking these concepts by definition similarity puts "
                                  "ANTONYMS on top — state.enabled ~ state.disabled scores 1.00 "
                                  "because the only differing token is `not`",
            "measured_power": "on the CGA pack the substitution test separated 1 pair of 51; "
                              "it removes what is DANGEROUS to merge and does not establish "
                              "that anything SHOULD merge",
            "refuted_hypothesis": "co-occurrence in a sentence as evidence of distinctness: "
                                  "bacen~selic co-occur 0 times and are distinct, "
                                  "duration~macaulay_duration co-occur 9 times and are a "
                                  "hierarchy pair. Reported, never thresholded.",
            "output_is_a_queue": "nothing here writes to the registry; survivors go to the "
                                 "adversarial node with the question inverted",
        },
        "corpus": str(corpus),
        "prose_sentences": len(sentences),
        "concepts_scanned": len(records),
        "pairs_skipped_as_antonyms": skipped_antonym,
        "pairs_tested": len(tested),
        "counts": {verdict: len(rows) for verdict, rows in sorted(buckets.items())},
        "needs_judgement": buckets.get("needs-judgement", []),
        "one_way": buckets.get("one-way", []),
        "structurally_unsafe": buckets.get("structurally-unsafe", []),
        "no_evidence": buckets.get("no-evidence", []),
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(json.dumps({k: report[k] for k in
                      ("prose_sentences", "concepts_scanned", "pairs_skipped_as_antonyms",
                       "pairs_tested", "counts")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
