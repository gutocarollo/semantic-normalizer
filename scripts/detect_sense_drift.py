#!/usr/bin/env python3
"""Find alternative labels that are doing a different job than their preferred label.

Two earlier hypotheses failed, and the way they failed is what produced this one.

*Hypothesis 1 — generic words keep poor company.* Refuted by backtest: in a finance corpus
every sentence is dense with finance concepts, so a wrong match sits among right ones.
`valores` scored 2.9 on co-occurrence, higher than many correct pairs. The errors are not
domain-versus-general; they are **sense confusion inside the domain**.

*Hypothesis 2 — alternatives are riskier than preferred labels.* Directionally right (six of
eight batch false positives were alternatives) but far too blunt: 133 bare automatic
alternatives exist and most are harmless morphological variants — `banco`/`bancos`,
`empresa`/`empresas`, `investidor`/`investidores`.

*Hypothesis 3 — this one.* If two labels name the same concept, the corpus should use them in
similar surroundings. `banco` and `bancos` do. `risco` and `exposição` do not, because
`exposição` was never a synonym for risk — it is a quantity, and it later became its own
concept. So: build a context profile for every label of a concept and measure how far each
alternative sits from the preferred one. Cosine distance over context-word frequency, which
needs no annotation and no external resource.

The output is a ranked drift list. High drift on a bare automatic alternative is the signature
of a label that will produce false positives.
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
DEFAULT_OUTPUT = ROOT / "reports" / "sense-drift.json"
WORD = re.compile(r"[a-zà-ÿA-ZÀ-Ÿ][a-zà-ÿA-ZÀ-Ÿ\-]{2,}")
WINDOW = 10


def cosine(a: collections.Counter, b: collections.Counter) -> float:
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
    parser.add_argument("--min-occurrences", type=int, default=4)
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

    profiles: dict[tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    counts: collections.Counter[tuple[str, str]] = collections.Counter()
    samples: dict[tuple[str, str], list[str]] = collections.defaultdict(list)

    for index, sentence in enumerate(sentences):
        for record in normalize_text(sentence, source=f"s{index}", kind="text", lexicon=lexicon):
            for event in record["match_events"]:
                key = (event["concept_id"], event["alias"])
                counts[key] += 1
                start, end = event["start"], event["end"]
                context = WORD.findall(sentence[:start])[-WINDOW:] + WORD.findall(sentence[end:])[:WINDOW]
                for word in context:
                    word = word.casefold()
                    if word not in builder.STOPWORDS:
                        profiles[key][word] += 1
                if len(samples[key]) < 4:
                    samples[key].append(sentence[max(0, start - 55):end + 55])

    drifts = []
    for concept_id, record in by_id.items():
        for language in ("en", "pt-BR"):
            preferred = record["labels"][language]["pref"]
            pref_profile = profiles.get((concept_id, preferred))
            if not pref_profile or counts[(concept_id, preferred)] < args.min_occurrences:
                continue
            for entry in record["lexical_forms"][language]:
                alias = entry["form"]
                if alias == preferred or counts[(concept_id, alias)] < args.min_occurrences:
                    continue
                similarity = cosine(pref_profile, profiles[(concept_id, alias)])
                # A morphological variant shares its lemma's first letters; a different word
                # does not. Used only to explain the score, never to decide it.
                shared_stem = len(
                    [1 for a, b in zip(alias.casefold(), preferred.casefold()) if a == b]
                )
                drifts.append({
                    "concept_id": concept_id,
                    "language": language,
                    "preferred": preferred,
                    "alias": alias,
                    "policy": entry["policy"],
                    "tokens": len(alias.split()),
                    "occurrences": counts[(concept_id, alias)],
                    "preferred_occurrences": counts[(concept_id, preferred)],
                    "context_similarity": round(similarity, 4),
                    "drift": round(1 - similarity, 4),
                    "shared_prefix": shared_stem,
                    "likely_variant": shared_stem >= 4,
                    "examples": samples[(concept_id, alias)],
                })

    # The risk surface: bare, automatic, and drifting away from its own preferred label.
    suspects = [
        d for d in drifts
        if d["tokens"] == 1 and d["policy"] == "auto" and not d["likely_variant"]
    ]
    suspects.sort(key=lambda d: -d["drift"])
    drifts.sort(key=lambda d: -d["drift"])

    values = [d["drift"] for d in drifts] or [0.0]
    report = {
        "schema_version": "sense-drift-v1",
        "registry": {"version": registry["version"], "concepts": len(registry["records"])},
        "pairs_compared": len(drifts),
        "drift_distribution": {
            "mean": round(statistics.mean(values), 4),
            "median": round(statistics.median(values), 4),
            "stdev": round(statistics.pstdev(values), 4),
            "p75": round(sorted(values)[int(0.75 * len(values))], 4),
            "p90": round(sorted(values)[int(0.90 * len(values))], 4),
        },
        "suspects": suspects,
        "all": drifts,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "pairs_compared": len(drifts),
        "suspects": len(suspects),
        "drift_distribution": report["drift_distribution"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
