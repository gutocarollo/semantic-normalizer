#!/usr/bin/env python3
"""Rank term candidates by the standard ATE measures, instead of reading 6.342 of them by hand.

This script exists because of a wrong claim I made and a Law Zero violation behind it.

The claim: that no statistic separates `viés` (54 occurrences in a book about reasoning) from
`tempo` (57 in the same book), so every candidate has to be adjudicated by reading. That is false.
Contrastive frequency separates them by an order of magnitude, and doing so is the foundational
technique of Automatic Term Extraction — `weirdness` in Ahmad et al., `keyness` in corpus
linguistics, shipped in Termostat and Sketch Engine since the 1990s. Measured on this repo's two
corpora: `viés` 18.7x, `tempo` 2.5x.

The violation: this project built a terminology curation process from scratch when ATE is a field
with decades of literature and ISO standards (704, 1087, 12620, 30042/TBX). What follows is ported,
not invented.

TWO MEASURES, BOTH CLASSIC AND BOTH DEPENDENCY-FREE

`keyness` — log-likelihood ratio (Dunning 1993), the standard keyness statistic in corpus
linguistics. Compares a term's frequency in the target corpus against a reference corpus and asks
how surprising the difference is. Chosen over TF-IDF because the literature reports it performs
better for term discovery, and over a raw weirdness ratio because log-likelihood accounts for
sample size — a term seen twice in a tiny corpus does not outrank one seen two hundred times.

`c_value` — Frantzi & Ananiadou. Ranks MULTI-WORD candidates while handling nesting: `renda fixa`
appearing 200 times means little if 190 of those are inside `gestão de carteiras de renda fixa`.
C-value discounts a candidate by how often it appears nested inside longer ones, which is exactly
the longest-match problem this registry resolves at match time — measured here instead of guessed.

WHAT THIS DOES NOT DO

It ranks. It does not decide. The adjudication rules stay: a concept must be attested in prose and
never admitted for appearing only in a heading; a surface wrong in half its occurrences or more is
demoted; two opposite senses never share a concept. What changes is that adjudication now starts
from a ranked shortlist with the general vocabulary already pushed down, instead of from an
alphabetical pile where `viés` and `tempo` look identical.

A reference corpus is required and its choice is a real decision. Ideally it is a large balanced
general-language corpus. Using another domain corpus as the reference — the only thing available
here — finds what distinguishes the two domains, which is not the same question. The script says
which it used.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

WORD = re.compile(r"[a-zà-ÿA-ZÀ-Ÿ][a-zà-ÿA-ZÀ-Ÿ\-]{2,}")
# Multi-word candidates: a content word, optionally joined by the closed set of Portuguese
# connectors that appear inside real compound terms. A full ATE pipeline uses a POS tagger and
# noun-phrase patterns; this repo ships zero runtime dependencies, so the connector list stands in
# for the tagger. It is stated rather than hidden, because it is the weakest part of the method.
CONNECTORS = frozenset({"de", "da", "do", "das", "dos", "em", "a", "ao", "à", "com", "para", "por"})
# Reused from the matcher rather than re-listed here. `FUNCTION_WORDS["pt-BR"]` was widened from
# 16 to 46 entries when a language-detection defect was traced to it, so it is the maintained list.
def _function_words() -> frozenset:
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from semantic_normalizer.normalizer import FUNCTION_WORDS
    return frozenset().union(*(set(words) for words in FUNCTION_WORDS.values()))


HEADING = re.compile(r"^\s{0,3}#")
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def read_corpus(directory: Path, prose_only: bool) -> str:
    """Concatenate a corpus, optionally dropping headings and image captions.

    `prose_only` implements the admission rule's own definition of attestation: heading lines and
    image captions are not prose. Counting them is how `compra de contrato futuro` once looked
    prose-attested when both of its occurrences were `![Exemplo: …](assets/…)`.
    """
    chunks = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if not prose_only:
            chunks.append(text)
            continue
        chunks.append("\n".join(
            IMAGE.sub(" ", line) for line in text.splitlines() if not HEADING.match(line)
        ))
    return fold("\n".join(chunks))


def ngrams(tokens: list[str], longest: int) -> collections.Counter:
    """Count 1..n-grams, keeping only sequences that start and end on a content word."""
    counts: collections.Counter[tuple[str, ...]] = collections.Counter()
    for size in range(1, longest + 1):
        for index in range(len(tokens) - size + 1):
            gram = tuple(tokens[index:index + size])
            if gram[0] in CONNECTORS or gram[-1] in CONNECTORS:
                continue
            if size > 1 and any(
                word not in CONNECTORS and len(word) < 3 for word in gram
            ):
                continue
            counts[gram] += 1
    return counts


def log_likelihood(target: int, target_total: int, reference: int, reference_total: int) -> float:
    """Dunning's G2. Signed so that terms OVER-represented in the target rank positive."""
    total = target_total + reference_total
    expected_target = target_total * (target + reference) / total
    expected_reference = reference_total * (target + reference) / total
    value = 0.0
    if target:
        value += target * math.log(target / expected_target)
    if reference:
        value += reference * math.log(reference / expected_reference)
    g2 = 2 * value
    over = (target / target_total) > ((reference + 0.5) / reference_total)
    return g2 if over else -g2


def c_value(gram: tuple[str, ...], counts: collections.Counter) -> float:
    """Frantzi & Ananiadou, discounting a candidate by how often it is nested in longer ones.

    `renda fixa` occurring 200 times is weak evidence if 190 of those sit inside `gestão de
    carteiras de renda fixa`. This is the same nesting the matcher resolves by longest-match; here
    it is quantified so the ranking prefers the term that stands on its own.
    """
    length = len(gram)
    frequency = counts[gram]
    longer = [
        other for other in counts
        if len(other) > length and _contains(other, gram)
    ]
    base = math.log2(length + 1)
    if not longer:
        return base * frequency
    nested = sum(counts[other] for other in longer)
    return base * (frequency - nested / len(longer))


def _contains(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    size = len(needle)
    return any(haystack[i:i + size] == needle for i in range(len(haystack) - size + 1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, help="directory of the target domain's .md")
    parser.add_argument("--reference", required=True, help="directory of the reference corpus")
    parser.add_argument("--output", required=True)
    parser.add_argument("--longest", type=int, default=4, help="longest n-gram considered")
    parser.add_argument("--min-frequency", type=int, default=3)
    parser.add_argument("--top", type=int, default=300, help="candidates kept per length class")
    parser.add_argument("--min-weirdness", type=float, default=8.0,
                        help="effect-size floor. G2 measures SIGNIFICANCE and rewards sheer "
                             "frequency, so `não` (G2 152, weirdness 2.2x) outranks `lógica` "
                             "(G2 144, weirdness 35.7x) without this. The literature is explicit "
                             "that the two statistics answer different questions; use both.")
    parser.add_argument("--include-headings", action="store_true",
                        help="count heading lines and image captions as attestation (default: no)")
    args = parser.parse_args()

    corpus = Path(args.corpus)
    reference = Path(args.reference)
    for directory in (corpus, reference):
        if not directory.is_dir():
            raise SystemExit(f"not a directory: {directory}")

    prose_only = not args.include_headings
    target_tokens = WORD.findall(read_corpus(corpus, prose_only))
    reference_tokens = WORD.findall(read_corpus(reference, prose_only))
    if not target_tokens or not reference_tokens:
        raise SystemExit("one of the corpora produced no tokens")

    target = ngrams(target_tokens, args.longest)
    reference_unigrams = collections.Counter(reference_tokens)
    reference_grams = ngrams(reference_tokens, args.longest)
    target_total, reference_total = len(target_tokens), len(reference_tokens)

    stopwords = _function_words()
    rows, rejected_function, rejected_effect = [], 0, 0
    for gram, frequency in target.items():
        if frequency < args.min_frequency:
            continue
        if any(word in stopwords for word in gram):
            rejected_function += 1
            continue
        ref = (reference_unigrams[gram[0]] if len(gram) == 1 else reference_grams[gram])
        keyness = log_likelihood(frequency, target_total, ref, reference_total)
        if keyness <= 0:
            continue
        weirdness = (frequency / target_total) / ((ref + 0.5) / reference_total)
        if weirdness < args.min_weirdness:
            rejected_effect += 1
            continue
        rows.append({
            "term": " ".join(gram),
            "length": len(gram),
            "frequency": frequency,
            "reference_frequency": ref,
            "keyness_g2": round(keyness, 2),
            "weirdness": round(weirdness, 2),
            "c_value": round(c_value(gram, target), 2),
        })

    by_length: dict[int, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_length[row["length"]].append(row)
    kept = []
    for length, group in sorted(by_length.items()):
        key = "keyness_g2" if length == 1 else "c_value"
        group.sort(key=lambda row: -row[key])
        kept.extend(group[:args.top])

    report = {
        "schema_version": "term-candidates-v1",
        "method": {
            "termhood": "Dunning (1993) log-likelihood G2 against a reference corpus; the "
                        "`weirdness` ratio (Ahmad et al.) is reported beside it for readability.",
            "unithood": "Frantzi & Ananiadou C-value, discounting candidates by nesting.",
            "linguistic_filter": "connector-list stand-in for a POS tagger; this package ships "
                                 "zero runtime dependencies. Weakest part of the pipeline.",
            "prose_only": prose_only,
        },
        "corpora": {
            "target": str(corpus), "target_tokens": target_total,
            "reference": str(reference), "reference_tokens": reference_total,
            "caveat": "A reference corpus should ideally be large and general-language. Using "
                      "another DOMAIN corpus answers 'what distinguishes these two domains', "
                      "which is a different question — read the ranking with that in mind.",
        },
        "filtered_out": {
            "contained_a_function_word": rejected_function,
            "below_weirdness_floor": rejected_effect,
            "weirdness_floor": args.min_weirdness,
        },
        "candidates_scored": len(rows),
        "candidates_kept": len(kept),
        "what_this_does_not_do": "It ranks; it does not decide. Every admission rule still "
                                 "applies: attested in prose and never for appearing only in a "
                                 "heading; a surface wrong in >=50 % of occurrences is demoted; "
                                 "two opposite senses never share a concept.",
        "candidates": kept,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in
                      ("candidates_scored", "candidates_kept", "corpora")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
