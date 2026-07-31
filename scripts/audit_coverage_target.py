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

# Morphology is delegated, not hand-written. Two earlier versions of this script rolled their
# own — the first undid plurals, the second added regular conjugation — and both filed inflected
# verbs as DOMAIN vocabulary because a lemma index does not contain `vamos` or `negociadas`. Each
# round moved the error instead of removing it, which is what writing your own morphology for a
# language you are not modelling gets you.
#
# NLTK's RSLP (Removedor de Sufixos da Língua Portuguesa) is a published, validated Portuguese
# stemmer and nltk is already installed. A stem is not a lemma — RSLP gives `permit`, not
# `permitir` — so the stem is bridged back to a lemma by trying the endings Portuguese actually
# uses, and whichever the wordnet knows is the one that counts. Same build-time-only status as
# `wn`: the shipped package imports neither.
SUFFIXES = ("ar", "er", "ir", "o", "a", "os", "as", "e", "ão", "al", "or", "ade", "ez",
            "ismo", "mento", "ção", "")

_STEMMER = None


def _stemmer():
    global _STEMMER
    if _STEMMER is None:
        import nltk
        from nltk.stem import RSLPStemmer
        try:
            _STEMMER = RSLPStemmer()
        except LookupError:
            nltk.download("rslp", quiet=True)
            _STEMMER = RSLPStemmer()
    return _STEMMER


def lemmas(form: str) -> list[str]:
    """Every lemma the term could belong to, via its RSLP stem plus Portuguese endings.

    Over-generating is safe — the wordnet only answers for candidates it knows — and it is
    exactly what under-generating cost the two previous versions.
    """
    lowered = unicodedata.normalize("NFC", form).casefold()
    stem = _stemmer().stem(lowered)
    seen: dict[str, None] = {lowered: None}
    for suffix in SUFFIXES:
        candidate = stem + suffix
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

    # The backtest that licenses the signal, run every time rather than trusted from a comment.
    known_domain = ["debênture", "cotista", "benchmark", "duration", "convexidade", "cupom",
                    "derivativo", "volatilidade", "liquidez", "emissor", "custodiante",
                    "imunização", "arbitragem", "alíquota", "patrocinador", "contraparte",
                    "desenquadramento", "barbell", "steepening"]
    known_general = ["relação", "longo", "ano", "ter", "dias", "total", "mesma", "faz",
                     "conforme", "duas", "nome", "conta", "meio", "final", "número", "fazer",
                     "tempo", "vamos", "quais", "têm", "recebe", "envolve", "desses", "demais",
                     "nada", "vale", "assume", "permitem", "negociadas", "emitidos"]

    def senses_of(term: str) -> int:
        best = 0
        for candidate in lemmas(term):
            try:
                best = max(best, len(wn.synsets(candidate, lexicon="own-pt:1.0.0")))
            except Exception:
                pass
        return best

    domain_scores = [senses_of(term) for term in known_domain]
    general_scores = [senses_of(term) for term in known_general]
    best_cut, best_sum = 3, -1.0
    for cut in range(0, 16):
        recall = sum(1 for value in domain_scores if value < cut) / len(domain_scores)
        specificity = sum(1 for value in general_scores if value >= cut) / len(general_scores)
        if recall + specificity > best_sum:
            best_cut, best_sum = cut, recall + specificity
    youden = round(best_sum - 1, 3)
    backtest = {
        "known_domain_mean": round(sum(domain_scores) / len(domain_scores), 2),
        "known_general_mean": round(sum(general_scores) / len(general_scores), 2),
        "threshold": best_cut,
        "youden_j": youden,
        "usable": youden >= 0.5,
    }
    # Sensitivity and specificity separately, because they license different uses. Youden's J
    # alone hid the thing that matters here: the signal is good enough to RANK one term against
    # another, and not good enough to ESTIMATE the mass of a population where the class it
    # misses is the majority.
    backtest["sensitivity"] = round(
        sum(1 for value in domain_scores if value < best_cut) / len(domain_scores), 3)
    backtest["specificity"] = round(
        sum(1 for value in general_scores if value >= best_cut) / len(general_scores), 3)
    if not backtest["usable"]:
        raise SystemExit(
            f"the signal does not separate known-domain from known-general terms "
            f"(Youden's J {youden}); it cannot carry a verdict about the target"
        )

    queue = json.loads(Path(args.queue).read_text(encoding="utf-8"))
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    unknown = sorted(queue["strata"]["unknown"], key=lambda item: -item["occurrences"])
    top = unknown[: args.top] if args.top else unknown

    rows = []
    for item in top:
        best = senses_of(item["term"])
        rows.append({
            "term": item["term"], "occurrences": item["occurrences"],
            "general_senses": best,
            "kind": "general_language" if best >= best_cut else "domain_specific",
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

    domain_mass = mass("domain_specific")
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
        "backtest": backtest,
        "composition": {
            "general_language": {"terms": sum(1 for r in rows if r["kind"] == "general_language"),
                                 "occurrences": mass("general_language")},
            "domain_specific": {"terms": sum(1 for r in rows if r["kind"] == "domain_specific"),
                                "occurrences": mass("domain_specific")},
        },
        "domain_mass_upper_bound": domain_mass,
        "domain_mass_is_an_upper_bound_because": (
            f"specificity is {backtest['specificity']} against a population that is "
            f"{round(mass('general_language') / max(1, top_mass), 2)} general by mass, so the "
            "false positives land in the domain bucket and dominate its top by frequency — "
            "`conforme`, `duas`, `mil`, `vamos`, `rafael` are all in it. The backtest licenses "
            "this signal to RANK one term against another; it does not license an estimate of a "
            "population's mass, and using it for that is the same over-reach as the two earlier "
            "attempts, in a different place."
        ),
        "reachable_share_with_domain_terms_only": round(domain_mass / max(1, unknown_total), 4),
        "verdict": (
            "UNSETTLED. The instrument ranks terms well enough to find harvest candidates and "
            "not well enough to say whether the target is reachable: the domain-mass estimate is "
            "an upper bound contaminated by false positives from the majority class. Three "
            "attempts have now failed to settle this — plural-only morphology, hand-written "
            "conjugation, and a validated stemmer used outside the regime its backtest covers. "
            "The target stands as a declared pendency, and the honest use of this report is its "
            "ranked term list, not its totals."
            if True else
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
                       "backtest", "composition", "reachable_share_with_domain_terms_only",
                       "verdict")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
