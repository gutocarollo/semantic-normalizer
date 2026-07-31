#!/usr/bin/env python3
"""Draw a uniform sample from the occurrences the risk queue never reached.

The sweep reads the occurrences a prior×spread ranking says are most likely to be wrong, and
every form where an error turned up then had all of its occurrences adjudicated one by one. That
makes the ranked part exact and says nothing at all about the rest, so a total precision figure
built only from it would be the same mistake this project already made once: reporting 95-100 %
from evidence that structurally could not see the errors.

This is the complement, and it is the one place where sampling is the right tool rather than the
wrong one. The question here is not "where are the errors" — ranking answers that better than any
sample could. It is "how often is the ranking wrong about an occurrence it dismissed", and that
is a rate over a defined population, which is exactly what a uniform draw estimates. A Wilson
interval turns the count into a bound, so the reported total carries its own uncertainty instead
of implying more precision than the sample size supports.

Every drawn occurrence is written out with its sentence, so adjudication happens against text.
The seed is fixed, so the same corpus and registry always produce the same draw.
"""

from __future__ import annotations

import argparse
import collections
import glob
import importlib.util
import json
import math
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "cga-2026-markdown"
DEFAULT_OUTPUT = ROOT / "reports" / "unread-residual-sample.json"
WORD = re.compile(r"[a-zà-ÿA-ZÀ-Ÿ][a-zà-ÿA-ZÀ-Ÿ\-]{2,}")


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — the usual normal approximation collapses at 0 errors."""
    if total == 0:
        return (0.0, 1.0)
    phat = successes / total
    denominator = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--queues", nargs="+", required=True,
                        help="sweep-queue reports whose reading lists count as already read")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--sample", type=int, default=160)
    parser.add_argument("--seed", type=int, default=20260731)
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    spec = importlib.util.spec_from_file_location("build_oov_queue", ROOT / "scripts" / "build_oov_queue.py")
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    from semantic_normalizer.normalizer import normalize_text
    from semantic_normalizer.registry import load_lexicon, load_registry

    # A quote is the unit of "already read": the reading lists store the exact rendered window,
    # so matching on it is exact rather than a heuristic over positions that shift between runs.
    read_quotes: set[str] = set()
    read_forms: set[tuple[str, str]] = set()
    for path in args.queues:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        for item in report["reading_list"]:
            read_quotes.add(item["quote"].strip())
            read_forms.add((item["concept_id"], item["form"]))

    lexicon = load_lexicon()
    registry = load_registry()

    sentences = []
    for path in sorted(glob.glob(str(Path(args.corpus) / "*.md"))):
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        sentences.extend(builder.sentences_of(builder.strip_math(builder.HTML_COMMENT.sub(" ", raw))))

    population = []
    total_events = 0
    for index, sentence in enumerate(sentences):
        for record in normalize_text(sentence, source=f"s{index}", kind="text", lexicon=lexicon):
            for event in record["match_events"]:
                total_events += 1
                start, end = event["start"], event["end"]
                quote = sentence[max(0, start - 78):end + 62].replace("\n", " ").strip()
                if quote in read_quotes:
                    continue
                population.append({
                    "concept_id": event["concept_id"], "form": event["alias"],
                    "form_already_swept": (event["concept_id"], event["alias"]) in read_forms,
                    "quote": quote,
                })

    rng = random.Random(args.seed)
    drawn = rng.sample(population, min(args.sample, len(population)))
    drawn.sort(key=lambda item: (item["concept_id"], item["form"]))

    by_form = collections.Counter(item["form"] for item in drawn)
    report = {
        "schema_version": "unread-residual-v1",
        "registry": {"version": registry["version"], "concepts": len(registry["records"])},
        "events_total": total_events,
        "events_read_by_sweep": total_events - len(population),
        "events_unread": len(population),
        "unread_share": round(len(population) / max(1, total_events), 4),
        "sample_size": len(drawn),
        "seed": args.seed,
        "distinct_forms_in_sample": len(by_form),
        "sample": drawn,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in
                      ("events_total", "events_read_by_sweep", "events_unread", "unread_share",
                       "sample_size", "distinct_forms_in_sample")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
