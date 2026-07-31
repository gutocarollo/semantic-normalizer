#!/usr/bin/env python3
"""Surface the occurrences of each form that sit farthest from how that form is normally used.

Sweeping every one of the ~6500 match events by hand is not feasible, and sampling them is
what four review rounds proved does not work. This is the third option: cluster.

For each (concept, form), build a centroid from the context words of all its occurrences, then
score every occurrence by cosine distance to that centroid. A form used consistently has a
tight cloud and nothing far out. A form doing two jobs has a second lobe, and its members are
exactly the far ones. Every false positive confirmed so far lives in that lobe:

    `opções` in "uma das opções mais seguras"  sits far from "opções de compra embutidas"
    `ações` in "todas as ações necessárias"    sits far from "ações negociadas em bolsa"
    `futuros` in "resultados futuros"          sits far from "contratos futuros de Ibovespa"

Reading the five farthest occurrences of the fifty highest-volume forms is ~250 judgements
covering roughly half of all match events — a review a person can finish, aimed where the
errors actually are instead of spread evenly over text that is mostly right.
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
DEFAULT_OUTPUT = ROOT / "reports" / "outlier-matches.json"
WORD = re.compile(r"[a-zà-ÿA-ZÀ-Ÿ][a-zà-ÿA-ZÀ-Ÿ\-]{2,}")
WINDOW = 10


def cosine(a: dict[str, float], b: collections.Counter) -> float:
    shared = set(a) & set(b)
    if not shared:
        return 0.0
    dot = sum(a[w] * b[w] for w in shared)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--forms", type=int, default=50, help="highest-volume forms to cluster")
    parser.add_argument("--outliers", type=int, default=5, help="farthest occurrences kept per form")
    parser.add_argument("--min-occurrences", type=int, default=8)
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    spec = importlib.util.spec_from_file_location("build_oov_queue", ROOT / "scripts" / "build_oov_queue.py")
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    from semantic_normalizer.normalizer import normalize_text
    from semantic_normalizer.registry import load_lexicon, load_registry

    lexicon = load_lexicon()
    registry = load_registry()

    sentences = []
    for path in sorted(glob.glob(str(Path(args.corpus) / "*.md"))):
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        sentences.extend(builder.sentences_of(builder.strip_math(builder.HTML_COMMENT.sub(" ", raw))))

    occurrences: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for index, sentence in enumerate(sentences):
        for record in normalize_text(sentence, source=f"s{index}", kind="text", lexicon=lexicon):
            for event in record["match_events"]:
                start, end = event["start"], event["end"]
                context = collections.Counter(
                    word.casefold()
                    for word in WORD.findall(sentence[:start])[-WINDOW:] + WORD.findall(sentence[end:])[:WINDOW]
                    if word.casefold() not in builder.STOPWORDS
                )
                occurrences[(event["concept_id"], event["alias"])].append({
                    "context": context,
                    "quote": sentence[max(0, start - 62):end + 52],
                })

    ordered = sorted(occurrences.items(), key=lambda kv: -len(kv[1]))
    ordered = [kv for kv in ordered if len(kv[1]) >= args.min_occurrences][: args.forms]

    clusters = []
    covered = 0
    for (concept, alias), items in ordered:
        covered += len(items)
        centroid: collections.Counter = collections.Counter()
        for item in items:
            centroid.update(item["context"])
        weights = {w: n / len(items) for w, n in centroid.items()}
        scored = sorted(
            ({"distance": round(1 - cosine(weights, item["context"]), 4), "quote": item["quote"]}
             for item in items),
            key=lambda item: -item["distance"],
        )
        distances = [item["distance"] for item in scored]
        clusters.append({
            "concept_id": concept,
            "alias": alias,
            "occurrences": len(items),
            "distance_mean": round(statistics.mean(distances), 4),
            "distance_stdev": round(statistics.pstdev(distances), 4),
            # How far the worst occurrence sits beyond the form's own spread. A tight cloud
            # with one distant member is the two-jobs signature.
            "outlier_z": round(
                (max(distances) - statistics.mean(distances)) / (statistics.pstdev(distances) or 1), 2
            ),
            "outliers": scored[: args.outliers],
        })

    clusters.sort(key=lambda c: -c["outlier_z"])
    report = {
        "schema_version": "outlier-matches-v1",
        "registry": {"version": registry["version"], "concepts": len(registry["records"])},
        "forms_clustered": len(clusters),
        "events_covered": covered,
        "review_burden": sum(len(c["outliers"]) for c in clusters),
        "clusters": clusters,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in ("forms_clustered", "events_covered", "review_burden")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
