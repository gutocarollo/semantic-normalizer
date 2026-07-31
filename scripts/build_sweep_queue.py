#!/usr/bin/env python3
"""Order the risk surface by prior × evidence, and emit only the occurrences worth reading.

`score_form_polysemy.py` gives a prior: how many senses ordinary language assigns a token. It
backtests well, but on its own it condemns `risco` (7 senses) alongside `ações` (9), and `risco`
is never wrong here — a CGA textbook has no use for the other senses of the word. The prior says
an alternative reading *exists*; it cannot say the corpus *uses* it.

The corpus can say that, and it does not need annotation to. If a form carries one sense, its
occurrences share vocabulary and sit in one cloud. If it carries two, the cloud has a second
lobe, and the second lobe is exactly where the false positives are:

    `ações` in "todas as ações necessárias"  far from  "ações negociadas em bolsa"
    `título` in "a qualquer título"          far from  "títulos públicos federais"
    `valores` in "valores entre 0 e 100"     far from  "bolsa de valores"

So the queue is ordered by prior × spread, and the number of occurrences read per form scales
with that product instead of being a flat top-k. A form with a high prior and a wide cloud earns
a deep read; a form with a high prior and a tight cloud earns a shallow one, because there is
nothing out there to find. Nothing is sampled: within each form the occurrences read are the
farthest ones, chosen by distance, and the rest of that form's occurrences are the ones the
distance ordering says are indistinguishable from its core use.

Output is a flat reading list, so judgement happens against real sentences rather than against
scores.
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
DEFAULT_STRATA = ROOT / "reports" / "precision-strata.json"
DEFAULT_OUTPUT = ROOT / "reports" / "sweep-queue.json"
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
    parser.add_argument("--strata", default=str(DEFAULT_STRATA))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--budget", type=int, default=420,
                        help="total occurrences to read, distributed by risk")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    spec = importlib.util.spec_from_file_location("build_oov_queue", ROOT / "scripts" / "build_oov_queue.py")
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    from semantic_normalizer.normalizer import normalize_text
    from semantic_normalizer.registry import load_lexicon, load_registry

    strata = json.loads(Path(args.strata).read_text(encoding="utf-8"))
    risk_pairs = {
        (pair["concept_id"], pair["form"]): pair
        for pair in strata["sweep_queue"]
    }

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
                key = (event["concept_id"], event["alias"])
                if key not in risk_pairs:
                    continue
                start, end = event["start"], event["end"]
                context = collections.Counter(
                    word.casefold()
                    for word in WORD.findall(sentence[:start])[-WINDOW:] + WORD.findall(sentence[end:])[:WINDOW]
                    if word.casefold() not in builder.STOPWORDS
                )
                occurrences[key].append({
                    "context": context,
                    "quote": sentence[max(0, start - 78):end + 62].replace("\n", " "),
                })

    forms = []
    for key, items in occurrences.items():
        pair = risk_pairs[key]
        centroid: collections.Counter = collections.Counter()
        for item in items:
            centroid.update(item["context"])
        weights = {word: n / len(items) for word, n in centroid.items()}
        scored = sorted(
            ({"distance": round(1 - cosine(weights, item["context"]), 4), "quote": item["quote"]}
             for item in items),
            key=lambda item: -item["distance"],
        )
        distances = [item["distance"] for item in scored]
        spread = statistics.pstdev(distances)
        # Prior says a second sense could exist; spread says the corpus behaves as if one does.
        # Volume enters as a logarithm: a form seen 200 times deserves more reading than one seen
        # 8 times, but not 25 times more.
        priority = round(
            math.log2(1 + pair["general_senses"]) * (0.15 + spread) * math.log2(1 + len(items)), 4
        )
        forms.append({
            "concept_id": pair["concept_id"], "form": pair["form"],
            "general_senses": pair["general_senses"], "glosses": pair["glosses"][:3],
            "occurrences": len(items),
            "distance_mean": round(statistics.mean(distances), 4),
            "distance_spread": round(spread, 4),
            "distance_max": round(max(distances), 4),
            "priority": priority,
            "ranked": scored,
        })

    forms.sort(key=lambda form: -form["priority"])
    weight_total = sum(form["priority"] for form in forms) or 1.0
    reading: list[dict] = []
    for form in forms:
        share = form["priority"] / weight_total
        depth = max(2, min(form["occurrences"], round(args.budget * share)))
        form["read_depth"] = depth
        for item in form["ranked"][:depth]:
            reading.append({
                "concept_id": form["concept_id"], "form": form["form"],
                "general_senses": form["general_senses"],
                "distance": item["distance"], "quote": item["quote"],
            })

    covered = sum(form["read_depth"] for form in forms)
    report = {
        "schema_version": "sweep-queue-v1",
        "registry": {"version": registry["version"], "concepts": len(registry["records"])},
        "risk_surface_events": strata["strata"]["D_risk_surface"]["events"],
        "forms_in_queue": len(forms),
        "occurrences_to_read": covered,
        "share_of_risk_surface_read": round(covered / max(1, strata["strata"]["D_risk_surface"]["events"]), 4),
        "priority_distribution": {
            "mean": round(statistics.mean([f["priority"] for f in forms]), 4),
            "median": round(statistics.median([f["priority"] for f in forms]), 4),
            "stdev": round(statistics.pstdev([f["priority"] for f in forms]), 4),
            "max": max(f["priority"] for f in forms),
        },
        "forms": [{k: v for k, v in form.items() if k != "ranked"} for form in forms],
        "reading_list": reading,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in
                      ("forms_in_queue", "occurrences_to_read", "share_of_risk_surface_read",
                       "priority_distribution")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
