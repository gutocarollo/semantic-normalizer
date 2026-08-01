#!/usr/bin/env python3
"""Propose spellings an existing concept is missing, from OpenWordnet-PT. Generator, never oracle.

A concept earns its keep by matching the words a corpus actually uses. `entity.premise` shipped
with `premissa` and the CGA corpus also writes `pressuposto`; nothing in the pipeline looked for
that, because every surface has to be authored by whoever writes the batch. This proposes them.

MEASURED PRECISION, SO NOBODY MISTAKES THIS FOR A DECIDER

Run over the 14 concepts of the reasoning pack, WordNet offered 38 spellings the concepts do not
already carry. 26 never occur in the corpus prose and 2 are already claimed by another concept,
so 10 reach the queue. Reading the attested ones:

    premissa      -> pressuposto                        correct
    atributo      -> característica, qualidade          the general-language sense the concept's
                                                        own negative example exists to exclude
    conclusão     -> chegada, resultado, final          WRONG — the "end of something" sense
    matriz        -> programa, base                     WRONG — the "womb/origin" sense
    raciocínio    -> dedução, inferência, argumentação   NOT synonyms: those are KINDS of
                                                        reasoning, i.e. a hierarchy edge

Roughly one in twelve is a synonym of the sense the concept holds. WordNet has no idea which of a
word's senses this dictionary means, and it never will — that is what a domain registry is for.
The batch-01 adjudication note in `config/` reached the same conclusion in 2026 and wrote it
down: "`ili` is recorded only when an OpenWordnet-PT sense genuinely carries the financial
meaning; most do not (carteira offers bag/briefcase/purse and no portfolio)".

So the output is a QUEUE for the same adjudicate -> validate -> refute loop that already refuses
39 % of what reaches it, and every proposal carries a corpus citation so the adjudicator reads
the word in use rather than trusting the synset. Nothing here writes to the registry.

The failures are informative in their own right: when WordNet offers `dedução` for `raciocínio`
it has found a real relation and mislabelled it. Those are re-emitted under
`hierarchy_hints`, which is what `link_hierarchy.py` consumes.

`wn` is a BUILD-TIME dependency, like `propose_from_wordnet.py`. The runtime stays dependency
free. Install the data with:

    pip install wn && python -c "import wn; wn.download('own-pt:1.0.0')"
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "src" / "semantic_normalizer" / "data" / "registry.jsonl"
HEADING = re.compile(r"^\s{0,3}#")


def fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def prose(corpus: Path) -> list[str]:
    out = []
    for path in sorted(corpus.glob("*.md")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if HEADING.match(line) or "![" in line or not line.strip():
                continue
            out.append(line.strip())
    return out


def attested(surface: str, folded: list[str], cap: int = 3) -> list[str]:
    """Citations on word boundaries. A surface with none is not proposed at all."""
    needle = re.escape(fold(surface))
    pattern = re.compile(rf"(?<![a-zà-ÿ0-9\-]){needle}(?![a-zà-ÿ0-9\-])")
    found = [line for line in folded if pattern.search(line)]
    return found[:cap]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--contexts", nargs="+", required=True)
    parser.add_argument("--lexicon", default="own-pt:1.0.0")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    try:
        import wn
    except ImportError:
        raise SystemExit("fail-closed: `wn` is not installed. pip install wn && "
                         "python -c \"import wn; wn.download('own-pt:1.0.0')\"")
    try:
        wordnet = wn.Wordnet(args.lexicon)
    except Exception as exc:  # noqa: BLE001 - the library raises several types here
        raise SystemExit(f"fail-closed: lexicon {args.lexicon!r} unavailable: {exc}")

    corpus = Path(args.corpus)
    if not corpus.is_dir():
        raise SystemExit(f"fail-closed: corpus is not a directory: {corpus}")
    lines = prose(corpus)
    folded = [fold(line) for line in lines]

    scope = {c.casefold() for c in args.contexts}
    records = [json.loads(line)
               for line in REGISTRY.read_text(encoding="utf-8").splitlines() if line.strip()]
    owned = {fold(form["form"])
             for record in records
             for forms in record["lexical_forms"].values()
             for form in forms}
    in_scope = [r for r in records
                if scope & {str(c).casefold() for c in r.get("contexts", [])}]

    proposals, skipped_unattested, skipped_owned = [], 0, 0
    for record in sorted(in_scope, key=lambda r: r["concept_id"]):
        preferred = record["labels"]["pt-BR"]["pref"]
        mine = {fold(form["form"]) for form in record["lexical_forms"]["pt-BR"]}
        candidates: set[str] = set()
        for synset in wordnet.synsets(preferred):
            for lemma in synset.lemmas():
                candidates.add(fold(lemma))
        for candidate in sorted(candidates - mine):
            if len(candidate) < 4:
                continue
            if candidate in owned:
                # Another concept already claims it. Proposing it would be proposing a
                # collision, which the importer would demote and nobody would benefit from.
                skipped_owned += 1
                continue
            citations = attested(candidate, folded)
            if not citations:
                skipped_unattested += 1
                continue
            proposals.append({
                "concept_id": record["concept_id"],
                "concept_pref": preferred,
                "concept_definition": record["definition"],
                "proposed_surface": candidate,
                "citations": citations,
                "question": "does this spelling denote THE SAME SENSE this concept holds, in "
                            "these sentences? if it is a KIND of the concept rather than "
                            "another name for it, answer `hierarchy` instead of `surface`",
            })

    report = {
        "schema_version": "wordnet-surface-proposals-v1",
        "method": {
            "source": args.lexicon,
            "role": "GENERATOR — measured at roughly 1 correct in 12 on the reasoning pack; "
                    "WordNet cannot know which sense this registry holds",
            "filters": "the surface must occur in corpus PROSE on word boundaries, and must not "
                       "already be claimed by any concept in the registry",
            "decision": "none is taken here; every proposal carries citations and goes to the "
                        "adjudicate -> validate -> refute loop",
        },
        "corpus": str(corpus),
        "contexts": sorted(scope),
        "concepts_in_scope": len(in_scope),
        "proposals": len(proposals),
        "skipped_not_attested_in_prose": skipped_unattested,
        "skipped_already_owned": skipped_owned,
        "items": proposals,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(json.dumps({k: report[k] for k in
                      ("concepts_in_scope", "proposals", "skipped_not_attested_in_prose",
                       "skipped_already_owned")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
