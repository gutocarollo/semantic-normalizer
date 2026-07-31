#!/usr/bin/env python3
"""Rank (concept, form) pairs by how likely they are to be systematically wrong.

Judging 6520 match events by hand is not possible, and sampling them is exactly what three
review rounds proved does not work. So this ranks instead: it computes, per pair, signals that
every confirmed false positive so far shared, and puts the pairs most likely to be wrong at the
top of a queue a human can actually finish.

Every false positive found in this project had the same shape — a bare, common word whose
financial reading is a minority of its corpus uses:

    valores    ESG scores, notional amounts, statistical values, accounting sums
    IR         Imposto de Renda vs Information Ratio
    classes    asset class vs fund share class
    proteção   regulatory investor protection vs a market hedge
    comprado   a purchased certificate of deposit vs a long position
    ajustes    daily futures margin vs a configuration

The signals, all computed from the corpus rather than assumed:

* `company_score` — mean number of OTHER concepts matched in the same sentence. A genuine
  domain term travels with other domain terms; a generic word turns up anywhere. This is the
  strongest single signal and it needs no annotation.
* `context_entropy` — Shannon entropy of the content words surrounding the match. A term with
  one technical sense has low entropy; a word doing many jobs has high entropy.
* `isolation_rate` — share of occurrences where nothing else matched at all.
* `dominance` — share of the concept's volume riding on this one form. A concept held up
  entirely by one bare token has no second anchor if that token is wrong.

The output is ordered, so the sweep can start where the expected yield is highest and stop
when it stops paying.
"""

from __future__ import annotations

import argparse
import collections
import glob
import importlib.util
import json
import math
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "cga-2026-markdown"
DEFAULT_OUTPUT = ROOT / "reports" / "precision-risk.json"
WORD = re.compile(r"[a-zà-ÿA-ZÀ-Ÿ][a-zà-ÿA-ZÀ-Ÿ\-]{2,}")
WINDOW = 8  # content words on each side


def entropy(counter: collections.Counter) -> float:
    total = sum(counter.values())
    if total <= 1:
        return 0.0
    return round(-sum((n / total) * math.log2(n / total) for n in counter.values()), 3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--min-occurrences", type=int, default=3)
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    spec = importlib.util.spec_from_file_location(
        "build_oov_queue", ROOT / "scripts" / "build_oov_queue.py"
    )
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    from semantic_normalizer.normalizer import normalize_text
    from semantic_normalizer.registry import load_lexicon, load_registry

    lexicon = load_lexicon()
    registry = load_registry()
    by_id = {record["concept_id"]: record for record in registry["records"]}

    sentences = []
    for path in sorted(glob.glob(str(Path(args.corpus) / "*.md"))):
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        sentences.extend(builder.sentences_of(builder.strip_math(builder.HTML_COMMENT.sub(" ", raw))))

    pairs: dict[tuple[str, str], dict] = collections.defaultdict(
        lambda: {"n": 0, "company": [], "isolated": 0, "context": collections.Counter(), "examples": []}
    )
    per_concept: collections.Counter[str] = collections.Counter()

    for index, sentence in enumerate(sentences):
        for record in normalize_text(sentence, source=f"s{index}", kind="text", lexicon=lexicon):
            events = record["match_events"]
            concepts_here = {e["concept_id"] for e in events}
            for event in events:
                key = (event["concept_id"], event["alias"])
                bucket = pairs[key]
                bucket["n"] += 1
                per_concept[event["concept_id"]] += 1
                bucket["company"].append(len(concepts_here) - 1)
                if len(concepts_here) == 1:
                    bucket["isolated"] += 1
                start, end = event["start"], event["end"]
                left = WORD.findall(sentence[:start])[-WINDOW:]
                right = WORD.findall(sentence[end:])[:WINDOW]
                for word in left + right:
                    word = word.casefold()
                    if word not in builder.STOPWORDS:
                        bucket["context"][word] += 1
                if len(bucket["examples"]) < 8:
                    bucket["examples"].append(sentence[max(0, start - 55):end + 55])

    ranked = []
    for (concept, alias), bucket in pairs.items():
        if bucket["n"] < args.min_occurrences:
            continue
        record = by_id.get(concept, {})
        policy = next(
            (
                entry["policy"]
                for entries in record.get("lexical_forms", {}).values()
                for entry in entries
                if entry["form"] == alias
            ),
            "unknown",
        )
        company = round(statistics.mean(bucket["company"]), 2)
        isolation = round(bucket["isolated"] / bucket["n"], 3)
        ctx_entropy = entropy(bucket["context"])
        dominance = round(bucket["n"] / max(1, per_concept[concept]), 3)
        tokens = len(alias.split())
        # Bare tokens carrying volume, keeping poor company, in scattered contexts, and
        # holding up their whole concept. Phrases are structurally safe and score near zero.
        risk = round(
            (1.0 if tokens == 1 else 0.0)
            * (1.0 if policy == "auto" else 0.3)
            * math.log2(1 + bucket["n"])
            * (1 + isolation)
            * (1 + ctx_entropy / 6)
            / (1 + company),
            3,
        )
        ranked.append({
            "concept_id": concept, "alias": alias, "tokens": tokens, "policy": policy,
            "occurrences": bucket["n"], "company_score": company, "isolation_rate": isolation,
            "context_entropy": ctx_entropy, "dominance": dominance, "risk": risk,
            "top_context": [w for w, _ in bucket["context"].most_common(8)],
            "examples": bucket["examples"][:5],
        })
    ranked.sort(key=lambda item: -item["risk"])

    risks = [item["risk"] for item in ranked]
    report = {
        "schema_version": "precision-risk-v1",
        "registry": {"version": registry["version"], "concepts": len(registry["records"])},
        "pairs_ranked": len(ranked),
        "risk_distribution": {
            "mean": round(statistics.mean(risks), 3),
            "median": round(statistics.median(risks), 3),
            "stdev": round(statistics.pstdev(risks), 3),
            "p90": round(sorted(risks)[int(0.9 * len(risks))], 3),
            "max": max(risks),
        },
        "ranked": ranked,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"pairs_ranked": len(ranked), "risk_distribution": report["risk_distribution"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
