#!/usr/bin/env python3
"""Find bare English labels that only ever match inside an English compound term.

A uniform draw from the occurrences the risk queue had dismissed found four errors, and all four
were one thing: the Portuguese material quotes English terms verbatim — `Market Neutral`,
`Hedge Funds`, `High Yield Bonds`, `Growth at a Reasonable Price` — and a bare English label
matches a fragment of them. `Hedge Funds` is not a hedge, and `High Yield Bonds` is a credit
category rather than a yield.

Ranking by distance from a form's context centroid cannot find this class, and that is the
useful part of the finding rather than a footnote. Those occurrences sit in ordinary finance
prose surrounded by ordinary finance vocabulary, so their context vectors are unremarkable and
they never reach the tail that gets read. The signal is not in the context at all — it is in the
surface immediately around the match, which is a different measurement entirely.

So this measures that instead. For every bare English automatic form, look at what follows and
precedes each occurrence, and report the capitalised multi-word terms it turns out to be part
of, with the share of occurrences each accounts for. A form whose occurrences are mostly inside
one compound is a fragment matcher, and the repair is to register the compound so longest-match
takes it, or to forbid the fragment where the compound is not a concept worth having.
"""

from __future__ import annotations

import argparse
import collections
import glob
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "cga-2026-markdown"
DEFAULT_OUTPUT = ROOT / "reports" / "compound-fragments.json"
# A capitalised or all-caps run of words is how this material writes a quoted English term.
NEIGHBOUR = re.compile(r"[A-Z][A-Za-z\-]+(?:\s+(?:[A-Z][A-Za-z\-]+|a|at|of|the|and|to))*")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--min-occurrences", type=int, default=2)
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
    english_forms = {
        entry["form"].casefold()
        for record in registry["records"]
        for entry in record["lexical_forms"]["en"]
        if entry["policy"] == "auto" and len(entry["form"].split()) == 1
    }

    sentences = []
    for path in sorted(glob.glob(str(Path(args.corpus) / "*.md"))):
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        sentences.extend(builder.sentences_of(builder.strip_math(builder.HTML_COMMENT.sub(" ", raw))))

    inside: dict[tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    totals: collections.Counter[tuple[str, str]] = collections.Counter()
    quotes: dict[tuple[str, str], list[str]] = collections.defaultdict(list)

    for index, sentence in enumerate(sentences):
        for record in normalize_text(sentence, source=f"s{index}", kind="text", lexicon=lexicon):
            for event in record["match_events"]:
                surface = event["alias"]
                if surface.casefold() not in english_forms or len(surface.split()) != 1:
                    continue
                key = (event["concept_id"], surface)
                totals[key] += 1
                start, end = event["start"], event["end"]
                # The compound the match sits in, if any: the longest capitalised run that
                # covers the matched span.
                window_start = max(0, start - 46)
                compound = ""
                for candidate in NEIGHBOUR.finditer(sentence[window_start:end + 46]):
                    span_start = window_start + candidate.start()
                    span_end = window_start + candidate.end()
                    if span_start <= start and end <= span_end and len(candidate.group().split()) > 1:
                        if len(candidate.group()) > len(compound):
                            compound = candidate.group()
                if compound:
                    inside[key][compound] += 1
                    if len(quotes[key]) < 4:
                        quotes[key].append(sentence[max(0, start - 60):end + 50].replace("\n", " "))

    findings = []
    for key, count in totals.items():
        if count < args.min_occurrences:
            continue
        compounds = inside.get(key, collections.Counter())
        swallowed = sum(compounds.values())
        if not swallowed:
            continue
        findings.append({
            "concept_id": key[0], "form": key[1],
            "occurrences": count,
            "inside_a_compound": swallowed,
            "fragment_share": round(swallowed / count, 3),
            "compounds": compounds.most_common(6),
            "examples": quotes[key],
        })
    findings.sort(key=lambda item: (-item["fragment_share"], -item["inside_a_compound"]))

    report = {
        "schema_version": "compound-fragments-v1",
        "registry": {"version": registry["version"], "concepts": len(registry["records"])},
        "bare_english_automatic_forms": len(english_forms),
        "forms_with_fragment_matches": len(findings),
        "total_fragment_events": sum(item["inside_a_compound"] for item in findings),
        "findings": findings,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in
                      ("bare_english_automatic_forms", "forms_with_fragment_matches",
                       "total_fragment_events")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
