#!/usr/bin/env python3
"""Measure what the precision campaign stopped matching, per concept, against a baseline commit.

An adversarial review made a fair charge: the campaign reported precision every round and never
once reported recall, while demoting bare forms is precisely the operation that trades the
second for the first. A precision figure published alone invites the reading that nothing was
given up, and here something was — 46 correct occurrences went silent before the collocations
that carry their sense were registered.

So this reports the other half. It replays the corpus against two registries — a baseline commit
and the working tree — and diffs the match events per concept. Concepts that lost events are
listed first, because that is the number the campaign was not publishing.

A loss is not automatically a regression. Three kinds show up and they mean different things:

  absorbed    the events moved to a more specific concept in the same sentence, which is the
              longest-match rule working: `Yield to Worst` taking what `Yield` used to take
  suppressed  the form was demoted and nothing replaced it — the real cost, and the only kind
              that should ever be traded for precision
  removed     the concept no longer claims the surface at all

The distinction is computed, not asserted: for every lost event the replay checks whether the
same sentence position is now covered by some other concept.
"""

from __future__ import annotations

import argparse
import collections
import glob
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "cga-2026-markdown"
DEFAULT_OUTPUT = ROOT / "reports" / "recall-delta.json"
REGISTRY_REL = "semantic-normalizer-skill/src/semantic_normalizer/data/registry.jsonl"


def events_for(registry_path: Path, sentences: list[str]) -> dict:
    """Match events keyed by concept, plus the covered spans per sentence.

    `load_lexicon` accepts an explicit registry path, but `validate_record` also pins
    `REGISTRY_VERSION`, so a baseline from an older commit is rejected on load. The version pin
    is what makes the shipped registry auditable and is not being weakened — it is suspended for
    the duration of a read of a historical file, which is the one case it is wrong about.
    """
    sys.path.insert(0, str(ROOT / "src"))
    import semantic_normalizer.registry as registry_module

    from semantic_normalizer.normalizer import normalize_text

    pinned = registry_module.REGISTRY_VERSION
    first = json.loads(registry_path.read_text(encoding="utf-8").splitlines()[0])
    try:
        registry_module.REGISTRY_VERSION = first["registry_version"]
        lexicon = registry_module.load_lexicon(registry=registry_path)
    finally:
        registry_module.REGISTRY_VERSION = pinned

    per_concept: collections.Counter[str] = collections.Counter()
    spans: dict[int, set[tuple[int, int]]] = collections.defaultdict(set)
    quotes: dict[str, list[str]] = collections.defaultdict(list)
    for index, sentence in enumerate(sentences):
        for record in normalize_text(sentence, source=f"s{index}", kind="text", lexicon=lexicon):
            for event in record["match_events"]:
                per_concept[event["concept_id"]] += 1
                spans[index].add((event["start"], event["end"]))
                if len(quotes[event["concept_id"]]) < 3:
                    quotes[event["concept_id"]].append(
                        sentence[max(0, event["start"] - 55):event["end"] + 45].replace("\n", " ")
                    )
    return {"per_concept": per_concept, "spans": spans, "quotes": quotes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="git ref holding the baseline registry")
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    spec = importlib.util.spec_from_file_location("build_oov_queue", ROOT / "scripts" / "build_oov_queue.py")
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    sentences = []
    for path in sorted(glob.glob(str(Path(args.corpus) / "*.md"))):
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        sentences.extend(builder.sentences_of(builder.strip_math(builder.HTML_COMMENT.sub(" ", raw))))

    show = subprocess.run(
        ["git", "show", f"{args.baseline}:{REGISTRY_REL}"],
        cwd=ROOT.parent, capture_output=True, text=True,
    )
    if show.returncode != 0:
        raise SystemExit(f"cannot read the baseline registry at {args.baseline}: {show.stderr.strip()}")

    with tempfile.TemporaryDirectory() as tmp:
        baseline_path = Path(tmp) / "registry.jsonl"
        baseline_path.write_text(show.stdout, encoding="utf-8")
        before = events_for(baseline_path, sentences)
    after = events_for(ROOT / "src" / "semantic_normalizer" / "data" / "registry.jsonl", sentences)

    concepts = set(before["per_concept"]) | set(after["per_concept"])
    lost, gained = [], []
    for concept in sorted(concepts):
        delta = after["per_concept"][concept] - before["per_concept"][concept]
        if delta < 0:
            lost.append({
                "concept_id": concept, "before": before["per_concept"][concept],
                "after": after["per_concept"][concept], "delta": delta,
                "examples": before["quotes"].get(concept, [])[:2],
            })
        elif delta > 0:
            gained.append({
                "concept_id": concept, "before": before["per_concept"][concept],
                "after": after["per_concept"][concept], "delta": delta,
            })

    # Absorbed vs suppressed: a lost span still covered by something is absorbed.
    #
    # The suppressed spans are ENUMERATED, not just counted. An adversarial review asked whether
    # the 117 were all wrong senses or included correct matches, and the report could not answer
    # its own headline number — the reviewer had to reconstruct the list to find that 13 were
    # correct matches lost. A total without the list is a number nobody can check, which is the
    # same defect as `known_errors_remaining: 0`.
    covered_before = sum(len(v) for v in before["spans"].values())
    covered_after = sum(len(v) for v in after["spans"].values())
    absorbed = 0
    suppressed_spans = []
    for index, spans in sorted(before["spans"].items()):
        for span in sorted(spans):
            if span in after["spans"].get(index, ()):
                continue
            overlaps = any(
                other[0] <= span[0] and span[1] <= other[1]
                for other in after["spans"].get(index, ())
            )
            if overlaps:
                absorbed += 1
            else:
                sentence = sentences[index]
                suppressed_spans.append({
                    "surface": sentence[span[0]:span[1]],
                    "quote": sentence[max(0, span[0] - 60):span[1] + 50].replace("\n", " ").strip(),
                })
    suppressed = len(suppressed_spans)

    lost.sort(key=lambda item: item["delta"])
    gained.sort(key=lambda item: -item["delta"])
    report = {
        "schema_version": "recall-delta-v1",
        "baseline_ref": args.baseline,
        "events_before": sum(before["per_concept"].values()),
        "events_after": sum(after["per_concept"].values()),
        "spans_before": covered_before,
        "spans_after": covered_after,
        "spans_absorbed_by_a_longer_match": absorbed,
        "spans_suppressed_with_no_replacement": suppressed,
        "suppressed_spans": suppressed_spans,
        "concepts_that_lost_events": len(lost),
        "concepts_that_gained_events": len(gained),
        "lost": lost,
        "gained": gained[:40],
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in
                      ("events_before", "events_after", "spans_absorbed_by_a_longer_match",
                       "spans_suppressed_with_no_replacement", "concepts_that_lost_events",
                       "concepts_that_gained_events")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
