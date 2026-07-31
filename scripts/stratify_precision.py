#!/usr/bin/env python3
"""Partition every match event into strata whose precision is knowable, and size each one.

Measuring precision over 6459 events by reading all of them is not feasible, and sampling them
is what this project already proved does not work: random samples reported 95-100 % on batches
an exhaustive sweep scored at 84 %, because the errors sit in rare sub-patterns of forms that
are usually right.

The way out is neither reading everything nor sampling: it is partitioning the events into
strata where precision is either **true by construction** or **cheap to sweep exhaustively**.

    A  phrase forms                  a multi-word form carries its own disambiguation
    B  bare form, zero general senses the token does not exist outside this domain
    C  bare form, 1-2 general senses  a narrow competing reading
    D  bare form, 3+ general senses   the risk surface, swept exhaustively

Strata A and B are not sampled and not guessed — they are argued. A phrase like `bolsa de
valores` has no second reading in Portuguese, and a token like `cotista` or `debênture` returns
nothing from OpenWordnet-PT because ordinary Portuguese has no other use for it. Stratum D is
the only place where reading occurrences is unavoidable, and the point of this script is to
show how small it is, so the sweep is finite and the overall number is honest.

Threshold 3 is not chosen for elegance. `score_form_polysemy.py` fits it against forms already
proven wrong and already proven right by exhaustive sweep, and reports Youden's J for the cut.
"""

from __future__ import annotations

import argparse
import collections
import glob
import importlib.util
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "cga-2026-markdown"
DEFAULT_POLYSEMY = ROOT / "reports" / "form-polysemy.json"
DEFAULT_OUTPUT = ROOT / "reports" / "precision-strata.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--polysemy", default=str(DEFAULT_POLYSEMY))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--threshold", type=int, default=3,
                        help="general senses at or above which a bare form enters the sweep")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    spec = importlib.util.spec_from_file_location("build_oov_queue", ROOT / "scripts" / "build_oov_queue.py")
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    from semantic_normalizer.normalizer import normalize_text
    from semantic_normalizer.registry import load_lexicon, load_registry

    polysemy_report = json.loads(Path(args.polysemy).read_text(encoding="utf-8"))
    senses_of = {
        (item["concept_id"], item["form"]): item["general_senses"]
        for item in polysemy_report["forms"]
    }
    glosses_of = {
        (item["concept_id"], item["form"]): item["glosses"]
        for item in polysemy_report["forms"]
    }

    lexicon = load_lexicon()
    registry = load_registry()

    sentences = []
    for path in sorted(glob.glob(str(Path(args.corpus) / "*.md"))):
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        sentences.extend(builder.sentences_of(builder.strip_math(builder.HTML_COMMENT.sub(" ", raw))))

    strata: collections.Counter[str] = collections.Counter()
    per_pair: dict[tuple[str, str], dict] = {}

    for index, sentence in enumerate(sentences):
        for record in normalize_text(sentence, source=f"s{index}", kind="text", lexicon=lexicon):
            for event in record["match_events"]:
                key = (event["concept_id"], event["alias"])
                senses = senses_of.get(key)
                if len(event["alias"].split()) > 1:
                    stratum = "A_phrase"
                elif senses is None or senses == 0:
                    stratum = "B_domain_exclusive"
                elif senses < args.threshold:
                    stratum = "C_narrow"
                else:
                    stratum = "D_risk_surface"
                strata[stratum] += 1
                bucket = per_pair.setdefault(key, {
                    "concept_id": event["concept_id"], "form": event["alias"],
                    "stratum": stratum, "general_senses": senses or 0,
                    "glosses": glosses_of.get(key, []), "occurrences": 0, "quotes": [],
                })
                bucket["occurrences"] += 1
                if len(bucket["quotes"]) < 40:
                    start, end = event["start"], event["end"]
                    bucket["quotes"].append(sentence[max(0, start - 70):end + 60])

    total = sum(strata.values())
    sweep = sorted(
        (pair for pair in per_pair.values() if pair["stratum"] == "D_risk_surface"),
        key=lambda pair: -pair["occurrences"],
    )
    sweep_events = sum(pair["occurrences"] for pair in sweep)
    by_pair = [pair["occurrences"] for pair in per_pair.values()]

    report = {
        "schema_version": "precision-strata-v1",
        "registry": {"version": registry["version"], "concepts": len(registry["records"])},
        "threshold": args.threshold,
        "events_total": total,
        "strata": {
            name: {"events": count, "share": round(count / total, 4)}
            for name, count in sorted(strata.items())
        },
        "sweep": {
            "pairs": len(sweep),
            "events": sweep_events,
            "share_of_all_events": round(sweep_events / total, 4),
            # A precision figure is only as strong as what the unswept part can hide. If every
            # unswept event were correct, this is the worst the total could be once the sweep
            # is done and its errors are fixed.
            "ceiling_if_sweep_perfect": round((total - sweep_events + sweep_events) / total, 4),
            "floor_if_sweep_all_wrong": round((total - sweep_events) / total, 4),
        },
        "occurrences_per_pair": {
            "mean": round(statistics.mean(by_pair), 2),
            "median": statistics.median(by_pair),
            "stdev": round(statistics.pstdev(by_pair), 2),
            "max": max(by_pair),
        },
        "sweep_queue": sweep,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in ("events_total", "strata", "sweep")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
