#!/usr/bin/env python3
"""Measure how much of the frozen corpus the registry actually resolves.

Step 2 of `docs/plan-cga-domain-lexicon-0.4.0.md`. Written and committed BEFORE any concept is
authored, so the target cannot be tuned to a result that already exists.

Two outputs, and the plan needs both:

* `target`  — the single acceptance number for 0.4.0, derived here rather than asserted in
              prose. Round 1 of the planning review caught the earlier version restating its
              own arithmetic as a goal.
* `curve[]` — cumulative coverage per N authored concepts. The abort trigger compares batch 1
              against this; without it the trigger has no prediction to check.

The abort trigger treats a missing number as a failure, never as "undetermined".
"""

from __future__ import annotations

import argparse
import collections
import glob
import json

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUEUE = ROOT / "reports" / "oov-queue.json"
DEFAULT_OUTPUT = ROOT / "reports" / "coverage-baseline.json"
DEFAULT_CORPUS = ROOT.parent / "cga-2026-markdown"
CURVE_POINTS = (25, 50, 100, 150, 200, 300)
SAMPLE_SIZE = 300
SAMPLE_SEED = 7


def sentence_sample(corpus: Path, strip_math, html_comment, sentences_of) -> list[str]:
    """The same 300-sentence sample the 0.3.0 baseline was measured on."""
    import random

    sentences: list[str] = []
    for path in sorted(glob.glob(str(corpus / "*.md"))):
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        sentences.extend(sentences_of(strip_math(html_comment.sub(" ", raw))))
    random.seed(SAMPLE_SEED)
    return random.sample(sentences, min(SAMPLE_SIZE, len(sentences)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE))
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--label", default="baseline", help="tag recorded in the report")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_oov_queue", ROOT / "scripts" / "build_oov_queue.py")
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    from semantic_normalizer.normalizer import normalize_text
    from semantic_normalizer.registry import load_lexicon, load_registry

    queue = json.loads(Path(args.queue).read_text(encoding="utf-8"))
    lexicon = load_lexicon()
    registry = load_registry()

    unknown = queue["strata"]["unknown"]
    unknown_total = queue["totals"]["unknown"]["occurrences"]

    # Cumulative coverage if the top N unknown terms were authored. This is the prediction the
    # abort trigger checks a batch against — derived from the frozen queue, never from a
    # result already observed.
    curve = []
    running = 0
    for index, item in enumerate(unknown, start=1):
        running += item["occurrences"]
        if index in CURVE_POINTS:
            curve.append({
                "concepts": index,
                "occurrences": running,
                "share_of_unknown": round(running / unknown_total, 4),
            })

    # Observed state of the sample, right now.
    sample = sentence_sample(
        Path(args.corpus), builder.strip_math, builder.HTML_COMMENT, builder.sentences_of
    )
    status = collections.Counter()
    with_concept = 0
    concepts = 0
    unresolved = 0
    for index, sentence in enumerate(sample):
        for record in normalize_text(sentence, source=f"s{index}", kind="text", lexicon=lexicon):
            found = len(record.get("concept_ids") or [])
            concepts += found
            with_concept += 1 if found else 0
            unresolved += len(record.get("unresolved") or [])
            status[record.get("canonical_status")] += 1

    target = next(point for point in curve if point["concepts"] == 200)

    # How much of the ORIGINAL unknown population this registry now resolves. The baseline is
    # frozen on disk; the live queue is not comparable across runs because every concept added
    # removes its own occurrences from `unknown`.
    frozen = None
    baseline_path = DEFAULT_OUTPUT
    if baseline_path.is_file() and args.label != "baseline":
        base = json.loads(baseline_path.read_text(encoding="utf-8"))
        base_total = base["queue"]["unknown_occurrences"]
        resolved = base_total - unknown_total
        frozen = {
            "baseline_unknown_occurrences": base_total,
            "current_unknown_occurrences": unknown_total,
            "resolved_occurrences": resolved,
            "resolved_share": round(resolved / max(1, base_total), 4),
            "baseline_target_share": base["target"]["resolved_share_of_unknown"],
            "meets_target": resolved / max(1, base_total) >= base["target"]["resolved_share_of_unknown"],
        }
    report = {
        "schema_version": "coverage-v1",
        "label": args.label,
        "corpus_sha256": queue["corpus"]["sha256"],
        "registry": {"version": registry["version"], "sha256": registry["hash"],
                     "concepts": len(registry["records"])},
        "sample": {
            "size": len(sample),
            "seed": SAMPLE_SEED,
            "status": dict(status),
            "with_concept": with_concept,
            "with_concept_share": round(with_concept / max(1, len(sample)), 4),
            "concepts_per_sentence": round(concepts / max(1, len(sample)), 4),
            "unresolved_items": unresolved,
        },
        "queue": {
            "unknown_terms": len(unknown),
            "unknown_occurrences": unknown_total,
            "queue_registry_version": queue["registry"]["version"],
        },
        # Gate-table line 1, confronted rather than restated. `unknown` shrinks every time the
        # registry grows, so measuring against the live queue compares a moving population to
        # itself and always looks fine. The frozen baseline is the only stable denominator.
        "against_frozen_baseline": frozen,
        "curve": curve,
        "target": {
            "concepts": target["concepts"],
            "resolved_share_of_unknown": target["share_of_unknown"],
            "derivation": "cumulative occurrences of the top 200 unknown terms / unknown total",
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "target": report["target"],
                      "sample": report["sample"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
