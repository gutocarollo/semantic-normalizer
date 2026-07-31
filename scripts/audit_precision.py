#!/usr/bin/env python3
"""Sweep every match in the corpus, not a sample of them.

Three review rounds established that sampling cannot find what is wrong here. Random samples
of 25-60 occurrences reported 3-8 % false positives; an exhaustive sweep of one batch reported
16 %. The reason is mechanical: an error concentrated in a rare syntactic sub-pattern of a form
that is mostly right is invisible to a sample dominated by the correct uses. `valores` matched
its compounds correctly about twenty times and was wrong ten times out of ten outside them.

So this script does not sample. It emits every match event in the corpus, grouped so the
clusters are visible: by concept, by surface form, by form length, and by how much of a
concept's volume rides on a single bare token. Those groupings are what turn "find the wrong
ones" into "find the wrong *patterns*", which is the only version of the problem that scales.

    PYTHONPATH=src python scripts/audit_precision.py --output reports/precision-audit.json
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
DEFAULT_OUTPUT = ROOT / "reports" / "precision-audit.json"
CONTEXT_WINDOW = 60


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--examples", type=int, default=6, help="contexts kept per (concept, form)")
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

    sentences: list[tuple[str, str]] = []
    for path in sorted(glob.glob(str(Path(args.corpus) / "*.md"))):
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        clean = builder.strip_math(builder.HTML_COMMENT.sub(" ", raw))
        for sentence in builder.sentences_of(clean):
            sentences.append((Path(path).name, sentence))

    # (concept_id, alias) -> every occurrence, with enough context to judge it
    pairs: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    per_concept: collections.Counter[str] = collections.Counter()
    for index, (source, sentence) in enumerate(sentences):
        for record in normalize_text(sentence, source=f"{source}:{index}", kind="text", lexicon=lexicon):
            for event in record["match_events"]:
                concept = event["concept_id"]
                alias = event["alias"]
                start, end = event["start"], event["end"]
                per_concept[concept] += 1
                pairs[(concept, alias)].append({
                    "source": source,
                    "surface": sentence[start:end],
                    "context": sentence[max(0, start - CONTEXT_WINDOW):end + CONTEXT_WINDOW],
                })

    groups = []
    for (concept, alias), hits in sorted(pairs.items(), key=lambda kv: -len(kv[1])):
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
        groups.append({
            "concept_id": concept,
            "alias": alias,
            "tokens": len(alias.split()),
            "policy": policy,
            "occurrences": len(hits),
            # A concept whose volume rides almost entirely on one bare token is the shape
            # that produced every false positive found so far.
            "share_of_concept": round(len(hits) / max(1, per_concept[concept]), 3),
            "domains": record.get("domains", []),
            "examples": hits[: args.examples],
        })

    bare = [g for g in groups if g["tokens"] == 1]
    phrase = [g for g in groups if g["tokens"] > 1]
    counts = [g["occurrences"] for g in groups]
    report = {
        "schema_version": "precision-audit-v1",
        "registry": {"version": registry["version"], "concepts": len(registry["records"])},
        "corpus": {"files": len({s for s, _ in sentences}), "sentences": len(sentences)},
        "totals": {
            "match_events": sum(counts),
            "distinct_pairs": len(groups),
            "concepts_with_matches": len(per_concept),
        },
        "statistics": {
            "occurrences_per_pair": {
                "mean": round(statistics.mean(counts), 2),
                "median": statistics.median(counts),
                "stdev": round(statistics.pstdev(counts), 2),
                "max": max(counts),
                "p90": sorted(counts)[int(0.9 * len(counts))],
            },
            "bare_token_pairs": len(bare),
            "phrase_pairs": len(phrase),
            "events_from_bare_tokens": sum(g["occurrences"] for g in bare),
            "events_from_phrases": sum(g["occurrences"] for g in phrase),
        },
        "groups": groups,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("totals", "statistics")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
