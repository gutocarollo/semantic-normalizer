#!/usr/bin/env python3
"""Score every bare automatic form by how many senses general language already gives it.

Three hypotheses failed before this one, and each failure narrowed the question.

*Hypothesis 1 — generic words keep poor company.* Refuted by backtest: in a finance corpus a
wrong match sits among right ones, so co-occurrence cannot separate them. `valores` ranked #73.

*Hypothesis 2 — alternative labels are riskier than preferred ones.* Directionally right, far
too blunt: most bare alternatives are harmless morphological variants.

*Hypothesis 3 — a label drifting from its own preferred label is suspect.* Real, but it only
sees labels that share a concept. It cannot see a form whose competing sense belongs to no
concept in the registry at all, which is where the residual errors live.

*This one.* The failure is not domain-versus-domain, it is **domain-versus-ordinary-language**.
`ação` is a share and it is also a deed, a lawsuit, a plot; `título` is a security and also a
right, a headline, a degree. That fact is not a guess: OpenWordnet-PT records those senses, and
this project already depends on it. So the risk of a bare form is measurable before any
occurrence is read — count the senses general language assigns to the token.

Sense counts are a signal, never a verdict. `banco` scores 7 and is perfectly safe as a label
for a bank, because in this corpus the seat sense never occurs. So the number orders the review
queue; the corpus decides. The backtest at the bottom is what keeps that honest: the score is
only worth using if it separates the forms already proven wrong from the ones already proven
right, and it is reported whether or not it does.

OWN-PT indexes lemmas, so `ações` and `futuros` return nothing while `ação` and `futuro` return
nine and several. Portuguese inflection is regular enough that a small set of rules recovers the
lemma, and every candidate is tried rather than one guess being trusted.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "reports" / "form-polysemy.json"

# Portuguese plural and participle rules, applied as candidates rather than as a decision:
# whichever candidate the wordnet knows is the one that counts.
DEPLURAL = (
    (re.compile(r"ções$"), "ção"),
    (re.compile(r"ães$"), "ão"),
    (re.compile(r"ões$"), "ão"),
    (re.compile(r"([aeiou])is$"), r"\1l"),
    (re.compile(r"ns$"), "m"),
    (re.compile(r"(z|r|s)es$"), r"\1"),
    (re.compile(r"s$"), ""),
)
FEMININE = (re.compile(r"a$"), "o")


def lemma_candidates(form: str) -> list[str]:
    """Every plausible lemma of a surface form, most specific first."""
    lowered = form.casefold()
    out = [lowered]
    for pattern, replacement in DEPLURAL:
        if pattern.search(lowered):
            out.append(pattern.sub(replacement, lowered))
            break
    for candidate in list(out):
        if FEMININE[0].search(candidate):
            out.append(FEMININE[0].sub(FEMININE[1], candidate))
    seen: dict[str, None] = {}
    for candidate in out:
        if len(candidate) > 2:
            seen[candidate] = None
    return list(seen)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    import wn  # build-time dependency, never imported by the runtime package

    from semantic_normalizer.registry import load_registry

    registry = load_registry()
    lexicon_of = {"pt-BR": "own-pt:1.0.0", "en": "own-en:1.0.0"}

    cache: dict[tuple[str, str], tuple[int, str, list[str]]] = {}

    def senses(token: str, language: str) -> tuple[int, str, list[str]]:
        key = (token.casefold(), language)
        if key in cache:
            return cache[key]
        best: tuple[int, str, list[str]] = (0, token.casefold(), [])
        for candidate in lemma_candidates(token):
            try:
                found = wn.synsets(candidate, lexicon=lexicon_of[language])
            except Exception:
                found = []
            if len(found) > best[0]:
                glosses = [
                    (synset.definition() or "").strip()[:70]
                    for synset in found[:6]
                    if (synset.definition() or "").strip()
                ]
                best = (len(found), candidate, glosses)
        cache[key] = best
        return best

    scored = []
    for record in registry["records"]:
        for language, entries in record["lexical_forms"].items():
            if language not in lexicon_of:
                continue
            for entry in entries:
                form = entry["form"]
                if len(form.split()) != 1:
                    continue  # a phrase carries its own disambiguation
                count, lemma, glosses = senses(form, language)
                scored.append({
                    "concept_id": record["concept_id"],
                    "language": language,
                    "form": form,
                    "policy": entry["policy"],
                    "lemma_used": lemma,
                    "general_senses": count,
                    "glosses": glosses,
                })

    scored.sort(key=lambda item: (-item["general_senses"], item["form"]))
    automatic = [item for item in scored if item["policy"] == "auto"]

    # The backtest. `known_wrong` are forms an exhaustive corpus sweep proved wrong as bare
    # automatic matches; `known_right` are bare forms the same sweeps left standing. If the
    # score does not separate these two sets it is not usable, and that is reported as such.
    known_wrong = {
        "valores", "opções", "futuros", "desconto", "rendimento", "classes", "proteção",
        "comprado", "ajustes", "ações", "títulos", "título", "performance", "crédito",
        "exposição", "prazo", "juros",
    }
    known_right = {
        "cvm", "anbima", "bacen", "ibovespa", "selic", "debênture", "debêntures", "cotista",
        "cotistas", "hedge", "benchmark", "cdb", "ltn", "ntn-b", "corretora", "custodiante",
        "emissor", "volatilidade", "liquidez", "derivativo", "derivativos",
    }
    wrong_scores = [i["general_senses"] for i in scored if i["form"].casefold() in known_wrong]
    right_scores = [i["general_senses"] for i in scored if i["form"].casefold() in known_right]

    def summarise(values: list[int]) -> dict:
        if not values:
            return {"n": 0}
        return {
            "n": len(values),
            "mean": round(statistics.mean(values), 2),
            "median": statistics.median(values),
            "stdev": round(statistics.pstdev(values), 2),
            "min": min(values),
            "max": max(values),
        }

    # The threshold that best separates the two proven sets, chosen by the data rather than
    # picked by hand: the cut maximising (recall of wrong) + (specificity on right).
    best_cut, best_score = None, -1.0
    for cut in range(0, 12):
        if not wrong_scores or not right_scores:
            break
        recall = sum(1 for v in wrong_scores if v >= cut) / len(wrong_scores)
        specificity = sum(1 for v in right_scores if v < cut) / len(right_scores)
        if recall + specificity > best_score:
            best_cut, best_score = cut, recall + specificity

    separation = {
        "known_wrong": summarise(wrong_scores),
        "known_right": summarise(right_scores),
        "best_threshold": best_cut,
        "youden_j": round(best_score - 1, 3) if best_score >= 0 else None,
        "recall_of_known_wrong": (
            round(sum(1 for v in wrong_scores if best_cut is not None and v >= best_cut)
                  / len(wrong_scores), 3) if wrong_scores else None
        ),
        "false_alarm_on_known_right": (
            round(sum(1 for v in right_scores if best_cut is not None and v >= best_cut)
                  / len(right_scores), 3) if right_scores else None
        ),
        "usable": bool(best_score >= 0 and (best_score - 1) >= 0.5),
    }

    counts = [item["general_senses"] for item in automatic] or [0]
    report = {
        "schema_version": "form-polysemy-v1",
        "registry": {"version": registry["version"], "concepts": len(registry["records"])},
        "bare_forms": len(scored),
        "bare_automatic_forms": len(automatic),
        "distribution_over_automatic": {
            "mean": round(statistics.mean(counts), 2),
            "median": statistics.median(counts),
            "stdev": round(statistics.pstdev(counts), 2),
            "p75": sorted(counts)[int(0.75 * len(counts))],
            "p90": sorted(counts)[int(0.90 * len(counts))],
            "max": max(counts),
            "zero_sense_forms": sum(1 for value in counts if value == 0),
        },
        "backtest": separation,
        "forms": scored,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in
                      ("bare_automatic_forms", "distribution_over_automatic", "backtest")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
