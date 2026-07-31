#!/usr/bin/env python3
"""Ask whether the coverage target can be met by concepts worth having.

The 0.3.0 plan set a target of resolving 41.51 % of the unknown population, derived
mechanically: cumulative occurrences of the top 200 unknown terms, over the unknown total. The
derivation never asked what those 200 terms ARE. The registry now resolves 26.81 % and the
obvious reading is that 14.7 points of work remain.

That reading is worth checking before doing the work, because the queue's `unknown` stratum was
never a list of domain terms — it is every content word with no registry entry, and a stopword
list is the only thing separating the two. If what remains is ordinary Portuguese, the target is
not a backlog. It is a number that can only be reached by registering `ano`, `longo` and
`relação` as concepts, which would grow the dictionary while destroying the thing it exists for.

So this classifies each remaining unknown term with the instrument the precision campaign
already validated: how many senses ordinary language gives it, read from OpenWordnet-PT. A CGA
term like `cotista` or `debênture` is absent from a general wordnet or has exactly one sense. A
word like `relação` or `conta` has many. The backtest for that signal is in
`scripts/score_form_polysemy.py`, where it separated proven-wrong from proven-right forms with a
Youden's J of 0.599 — it is not being introduced here on faith.

The output is the share of the remaining unknown MASS that is domain vocabulary, which is the
honest ceiling on this target, versus the share that is general language, which is what a
stopword list should have removed and what no dictionary should absorb.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUEUE = ROOT / "reports" / "oov-queue.json"
DEFAULT_BASELINE = ROOT / "reports" / "coverage-baseline.json"
DEFAULT_OUTPUT = ROOT / "reports" / "coverage-target-audit.json"

DEPLURAL = (
    (re.compile(r"ções$"), "ção"), (re.compile(r"ães$"), "ão"), (re.compile(r"ões$"), "ão"),
    (re.compile(r"([aeiou])is$"), r"\1l"), (re.compile(r"ns$"), "m"),
    (re.compile(r"(z|r|s)es$"), r"\1"), (re.compile(r"s$"), ""),
)
# Portuguese verb endings, enough to recover the infinitive a general wordnet indexes. The first
# version of this script only undid plurals, so `vamos`, `permitem`, `devemos`, `negociadas` and
# `emitidos` returned zero senses and were filed as DOMAIN vocabulary — a morphological failure
# read as a semantic finding. An adversarial review measured the damage: 26 % of the zero-sense
# bucket is inflected verbs, and the conclusion drawn from that bucket was the whole argument.
CONJUGATION = (
    (re.compile(r"(a|e|i)(mos|ram|rem|sse|ssem|ndo|das|dos|da|do|va|vam|ria|riam|rão|rá)$"), r"\1r"),
    (re.compile(r"(a|e|i)(m|s|u)$"), r"\1r"),
    (re.compile(r"[aeiou]$"), "ar"),
)
ADVERB = re.compile(r"mente$")


def lemmas(form: str) -> list[str]:
    """Every plausible lemma: the surface, its singular, its infinitive, its adjective base.

    Tried as candidates rather than as a decision — whichever the wordnet knows is the one that
    counts. Over-generating here is safe; under-generating is what produced the wrong verdict.
    """
    lowered = unicodedata.normalize("NFC", form).casefold()
    out = [lowered]
    for pattern, replacement in DEPLURAL:
        if pattern.search(lowered):
            out.append(pattern.sub(replacement, lowered))
            break
    if ADVERB.search(lowered):
        stem = ADVERB.sub("", lowered)
        out.extend([stem, stem + "o", stem[:-1] + "o" if stem.endswith("a") else stem])
    for base in list(out):
        for pattern, replacement in CONJUGATION:
            if pattern.search(base):
                out.append(pattern.sub(replacement, base))
                break
        if base.endswith("a"):
            out.append(base[:-1] + "o")
    seen: dict[str, None] = {}
    for candidate in out:
        if len(candidate) > 2:
            seen[candidate] = None
    return list(seen)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE))
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--top", type=int, default=0,
                        help="classify only the top N terms; 0 means the WHOLE unknown stratum, "
                             "which is the only scope in which the verdict means anything")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    import wn

    queue = json.loads(Path(args.queue).read_text(encoding="utf-8"))
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    unknown = sorted(queue["strata"]["unknown"], key=lambda item: -item["occurrences"])
    top = unknown[: args.top] if args.top else unknown

    rows = []
    for item in top:
        best = 0
        for candidate in lemmas(item["term"]):
            try:
                found = len(wn.synsets(candidate, lexicon="own-pt:1.0.0"))
            except Exception:
                found = 0
            best = max(best, found)
        # Threshold 3 is the cut score_form_polysemy.py fitted against forms already proven
        # wrong and proven right; it is reused rather than re-picked.
        rows.append({
            "term": item["term"], "occurrences": item["occurrences"],
            "general_senses": best,
            "kind": "general_language" if best >= 3 else ("narrow" if best else "domain_specific"),
        })

    def mass(kind: str) -> int:
        return sum(row["occurrences"] for row in rows if row["kind"] == kind)

    top_mass = sum(row["occurrences"] for row in rows)
    # The denominator is the FROZEN baseline, not the current queue. `target_as_written` and
    # `resolved_share` are both fractions of the 86-concept baseline, and the first version of
    # this script divided by the CURRENT unknown total and compared the two directly — different
    # populations either side of the same inequality.
    unknown_total = baseline["queue"]["unknown_occurrences"]
    current_unknown = queue["totals"]["unknown"]["occurrences"]
    target = baseline["target"]["resolved_share_of_unknown"]
    already_resolved = (unknown_total - current_unknown) / max(1, unknown_total)

    domain_mass = mass("domain_specific") + mass("narrow")
    report = {
        "schema_version": "coverage-target-audit-v1",
        "target_as_written": target,
        "target_derivation": baseline["target"]["derivation"],
        "top_terms_examined": len(rows),
        "denominator": "frozen baseline unknown_occurrences",
        "baseline_unknown_occurrences": unknown_total,
        "current_unknown_occurrences": current_unknown,
        "already_resolved_share": round(already_resolved, 4),
        "gap_to_target_share": round(max(0.0, target - already_resolved), 4),
        "gap_to_target_occurrences": round(max(0.0, target - already_resolved) * unknown_total),
        "top_terms_mass": top_mass,
        "top_terms_share_of_unknown": round(top_mass / max(1, unknown_total), 4),
        "composition_of_the_top_terms": {
            "general_language": {"terms": sum(1 for r in rows if r["kind"] == "general_language"),
                                 "occurrences": mass("general_language")},
            "narrow": {"terms": sum(1 for r in rows if r["kind"] == "narrow"),
                       "occurrences": mass("narrow")},
            "domain_specific": {"terms": sum(1 for r in rows if r["kind"] == "domain_specific"),
                                "occurrences": mass("domain_specific")},
        },
        "reachable_share_with_domain_terms_only": round(domain_mass / max(1, unknown_total), 4),
        "verdict": (
            "The remaining domain vocabulary is larger than the gap: the target IS reachable by "
            "registering concepts worth having, and the earlier claim that it was not was drawn "
            "from a 200-term slice with a lemmatiser that could not undo verb inflection."
            if domain_mass >= max(0.0, target - already_resolved) * unknown_total else
            "The target is NOT reachable by registering domain vocabulary. Most of the remaining "
            "unknown mass is ordinary Portuguese that a stopword list should have removed. "
            "Meeting the number as written would require registering general words as concepts, "
            "which grows the dictionary while destroying what it is for."
        ),
        "terms": rows,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in
                      ("target_as_written", "already_resolved_share", "gap_to_target_occurrences",
                       "composition_of_the_top_terms", "reachable_share_with_domain_terms_only",
                       "verdict")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
