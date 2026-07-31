#!/usr/bin/env python3
"""Propose bilingual candidates for the unknown queue from OpenWordnet-PT.

Step 3 of `docs/plan-cga-domain-lexicon-0.4.0.md`.

This script never chooses a sense. Taking `synsets(term)[0]` is measurably catastrophic for
finance: `carteira` -> `bag`, `fundo` -> `deep` (an adjective), `retorno` -> `homecoming`,
`ativo` -> `active` (an adjective), `risco` -> `danger`, `título` -> `claim`. Every sense is
emitted with its ILI and the English lemmas that ILI reaches, alongside the corpus contexts,
so the decision is made by adjudication against real usage.

`wn` is a build-time dependency. The runtime stays dependency-free, as
`docs/data-governance.md` requires.

    pip install wn && python -c "import wn; wn.download('own-pt:1.0.0'); wn.download('own-en:1.0.0')"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUEUE = ROOT / "reports" / "oov-queue.json"
DEFAULT_OUTPUT = ROOT / "reports" / "wordnet-candidates.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--top", type=int, default=200, help="terms taken from the unknown stratum")
    args = parser.parse_args()

    try:
        import wn
    except ImportError:
        raise SystemExit("wn is not installed; see the module docstring") from None

    portuguese = wn.Wordnet("own-pt:1.0.0")
    english = wn.Wordnet("own-en:1.0.0")
    english_by_ili: dict[str, list[str]] = {}
    english_gloss: dict[str, str] = {}
    for synset in english.synsets():
        if synset.ili:
            key = str(synset.ili)
            english_by_ili.setdefault(key, synset.lemmas())
            english_gloss.setdefault(key, synset.definition() or "")

    queue = json.loads(Path(args.queue).read_text(encoding="utf-8"))
    terms = queue["strata"]["unknown"][: args.top]

    proposals = []
    counts = {"monosemous": 0, "polysemous": 0, "absent": 0, "without_ili": 0}
    for item in terms:
        term = item["term"]
        synsets = portuguese.synsets(term)
        senses = []
        for synset in synsets:
            ili = str(synset.ili) if synset.ili else None
            senses.append({
                "synset_id": synset.id,
                "pos": synset.pos,
                "ili": ili,
                "definition_pt": synset.definition() or "",
                "lemmas_pt": synset.lemmas(),
                "lemmas_en": english_by_ili.get(ili, []) if ili else [],
                "definition_en": english_gloss.get(ili, "") if ili else "",
            })
            if not ili:
                counts["without_ili"] += 1
        if not senses:
            counts["absent"] += 1
            decision = "author_from_apostila"
        elif len(senses) == 1:
            counts["monosemous"] += 1
            decision = "adjudicate_single_candidate"
        else:
            counts["polysemous"] += 1
            decision = "adjudicate_between_senses"
        proposals.append({
            "term": term,
            "occurrences": item["occurrences"],
            "contexts": item["contexts"],
            "sense_count": len(senses),
            "senses": senses,
            # Never a chosen sense. The field names the work, not the answer.
            "required_decision": decision,
            "chosen_sense": None,
        })

    if any(p["chosen_sense"] is not None for p in proposals):
        raise SystemExit("a sense was pre-selected; this script must not decide")

    payload = {
        "schema_version": "wordnet-candidates-v1",
        "source": {"pt": "own-pt:1.0.0", "en": "own-en:1.0.0", "bridge": "CILI"},
        "corpus_sha256": queue["corpus"]["sha256"],
        "top": args.top,
        "counts": counts,
        "proposals": proposals,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    covered = counts["monosemous"] + counts["polysemous"]
    print(json.dumps({
        "output": str(output.relative_to(ROOT)),
        "counts": counts,
        "wordnet_coverage": round(covered / max(1, args.top), 4),
        "mean_senses": round(
            sum(p["sense_count"] for p in proposals if p["sense_count"] > 1)
            / max(1, counts["polysemous"]), 2),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
