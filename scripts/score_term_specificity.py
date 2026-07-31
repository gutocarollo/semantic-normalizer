#!/usr/bin/env python3
"""Decide whether an unknown term is domain vocabulary by measuring the corpus, not the word.

Two attempts to settle the coverage question failed the same way. Both classified terms by
counting the senses OpenWordnet-PT gives them, and both were wrong about the same class: an
inflected verb is not in a lemma index, so `vamos`, `permitem`, `devemos` and `negociadas`
returned zero senses and were filed as DOMAIN vocabulary. The first version undid only plurals;
the second added regular conjugation and still missed `faz`, `têm`, `vamos`. Each round of hand-
written morphology moves the error rather than removing it, because the instrument is asking a
question about the LANGUAGE when the question is about this CORPUS.

There is no Portuguese lemmatiser in this environment and adding one for a single measurement is
not proportionate. But the measurement does not need one. A domain term and a general word
differ in something the corpus states directly:

  a domain term appears in a narrow set of surroundings — `debênture` sits near `emissor`,
  `vencimento`, `cupom`, and near almost nothing else
  a general word appears anywhere — `faz`, `duas`, `longo` take whatever context is passing

So the signal is the CONCENTRATION of a term's context distribution, and it is computed from the
corpus alone: no wordnet, no lemma index, no morphology, hence nothing for irregular verbs to
break. Normalised entropy over the content words surrounding each occurrence — 0 means the term
always appears among the same words, 1 means its neighbours are as varied as the corpus allows.

Like every signal this campaign adopted, it is backtested before it is used, against terms
already known to be domain and already known to be general. If it does not separate them it is
reported as unusable and the coverage question stays open — which is a legitimate answer and
better than a third round of regex morphology.
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
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "cga-2026-markdown"
DEFAULT_QUEUE = ROOT / "reports" / "oov-queue.json"
DEFAULT_BASELINE = ROOT / "reports" / "coverage-baseline.json"
DEFAULT_OUTPUT = ROOT / "reports" / "term-specificity.json"
WORD = re.compile(r"[a-zà-ÿA-ZÀ-Ÿ][a-zà-ÿA-ZÀ-Ÿ\-]{2,}")
WINDOW = 6

# Terms whose status is not in doubt, used to fit and to judge the cut. Domain terms are CGA
# vocabulary already in the registry; general terms are ordinary Portuguese the queue surfaced.
KNOWN_DOMAIN = {
    "debênture", "cotista", "cotistas", "benchmark", "duration", "convexidade", "cupom",
    "derivativo", "derivativos", "volatilidade", "liquidez", "emissor", "custodiante",
    "imunização", "arbitragem", "hedge", "swap", "debêntures", "corretora", "alíquota",
    "patrocinador", "contraparte", "desenquadramento", "barbell", "ladder", "steepening",
}
KNOWN_GENERAL = {
    "relação", "longo", "ano", "ter", "dias", "total", "mesma", "faz", "conforme", "duas",
    "nome", "conta", "anos", "meio", "final", "dia", "número", "fazer", "tempo", "vamos",
    "quais", "têm", "recebe", "envolve", "desses", "demais", "nada", "vale", "sob", "assume",
}


def fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def normalised_entropy(counter: collections.Counter) -> float:
    total = sum(counter.values())
    if total <= 1:
        return 0.0
    raw = -sum((n / total) * math.log2(n / total) for n in counter.values())
    ceiling = math.log2(total)
    return raw / ceiling if ceiling else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE))
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--min-occurrences", type=int, default=4,
                        help="below this an entropy estimate is noise")
    args = parser.parse_args()

    spec = importlib.util.spec_from_file_location("build_oov_queue", ROOT / "scripts" / "build_oov_queue.py")
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    queue = json.loads(Path(args.queue).read_text(encoding="utf-8"))
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    unknown = {fold(item["term"]): item["occurrences"] for item in queue["strata"]["unknown"]}

    sentences = []
    for path in sorted(glob.glob(str(Path(args.corpus) / "*.md"))):
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        sentences.extend(builder.sentences_of(builder.strip_math(builder.HTML_COMMENT.sub(" ", raw))))

    wanted = set(unknown) | {fold(term) for term in KNOWN_DOMAIN | KNOWN_GENERAL}
    contexts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    counts: collections.Counter[str] = collections.Counter()
    for sentence in sentences:
        tokens = [fold(token) for token in WORD.findall(sentence)]
        for position, token in enumerate(tokens):
            if token not in wanted:
                continue
            counts[token] += 1
            neighbours = tokens[max(0, position - WINDOW):position] + tokens[position + 1:position + 1 + WINDOW]
            for neighbour in neighbours:
                if neighbour not in builder.STOPWORDS and neighbour != token:
                    contexts[token][neighbour] += 1

    scored = {
        term: {"occurrences": counts[term],
               "distinct_neighbours": len(contexts[term]),
               "context_entropy": round(normalised_entropy(contexts[term]), 4)}
        for term in contexts
        if counts[term] >= args.min_occurrences
    }

    domain = [scored[t]["context_entropy"] for t in map(fold, KNOWN_DOMAIN) if t in scored]
    general = [scored[t]["context_entropy"] for t in map(fold, KNOWN_GENERAL) if t in scored]

    best_cut, best_score = None, -1.0
    if domain and general:
        for step in range(0, 101):
            cut = step / 100
            recall = sum(1 for value in domain if value <= cut) / len(domain)
            specificity = sum(1 for value in general if value > cut) / len(general)
            if recall + specificity > best_score:
                best_cut, best_score = cut, recall + specificity
    youden = round(best_score - 1, 3) if best_score >= 0 else None
    usable = bool(youden is not None and youden >= 0.5)

    verdict_terms = []
    if usable:
        for term, occurrences in unknown.items():
            row = scored.get(term)
            if not row:
                continue
            verdict_terms.append({
                "term": term, "occurrences": occurrences,
                "context_entropy": row["context_entropy"],
                "kind": "domain" if row["context_entropy"] <= best_cut else "general",
            })
    domain_mass = sum(r["occurrences"] for r in verdict_terms if r["kind"] == "domain")
    unscored_mass = sum(
        occurrences for term, occurrences in unknown.items() if term not in scored
    )
    baseline_total = baseline["queue"]["unknown_occurrences"]
    current_total = queue["totals"]["unknown"]["occurrences"]
    already = (baseline_total - current_total) / max(1, baseline_total)
    gap = max(0.0, baseline["target"]["resolved_share_of_unknown"] - already)

    report = {
        "schema_version": "term-specificity-v1",
        "signal": "normalised entropy of the content words surrounding each occurrence",
        "why_not_wordnet": "Two wordnet-based attempts misclassified inflected verbs as domain "
                           "vocabulary because a lemma index does not contain them, and each "
                           "round of hand-written morphology moved the error instead of removing "
                           "it. This signal reads the corpus and has no morphology to get wrong.",
        "backtest": {
            "known_domain": {"n": len(domain),
                             "mean": round(statistics.mean(domain), 4) if domain else None},
            "known_general": {"n": len(general),
                              "mean": round(statistics.mean(general), 4) if general else None},
            "best_threshold": best_cut,
            "youden_j": youden,
            "usable": usable,
        },
        "terms_scored": len(scored),
        "terms_below_min_occurrences": len(unknown) - len([t for t in unknown if t in scored]),
        "mass_not_scorable": unscored_mass,
        "gap_to_target_occurrences": round(gap * baseline_total),
        "domain_mass_by_this_signal": domain_mass if usable else None,
        "verdict": (
            f"Domain vocabulary reachable: {domain_mass} occurrences against a gap of "
            f"{round(gap * baseline_total)}. The target IS reachable."
            if usable and domain_mass >= gap * baseline_total else
            f"Domain vocabulary reachable: {domain_mass} occurrences against a gap of "
            f"{round(gap * baseline_total)}. The target is NOT reachable."
            if usable else
            "The signal does not separate known-domain from known-general terms well enough to "
            "carry a verdict. The coverage question stays open, which is the honest answer — a "
            "third round of hand-written morphology would move the error, not remove it."
        ),
        "terms": sorted(verdict_terms, key=lambda row: -row["occurrences"]),
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in
                      ("backtest", "terms_scored", "mass_not_scorable",
                       "gap_to_target_occurrences", "domain_mass_by_this_signal", "verdict")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
