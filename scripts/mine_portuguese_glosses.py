#!/usr/bin/env python3
"""Take the Portuguese name of each registered English term from the corpus, not from a translator.

The compound-term batches fixed the English side of a defect and left the Portuguese side open,
because the Portuguese labels were authored by translating the English rather than by reading
what the material writes. Twenty-seven of those labels occur zero times in the corpus, while the
names the corpus does use — `Mercado Neutro` for Market Neutral, `Crescimento a um Preço
Razoável` for GARP, `Títulos com Retornos Elevado` for High Yield Bonds — were never registered.
So the fragment errors the English repair removed came straight back in Portuguese: `Mercado`
matching inside `Mercado Neutro` is the same defect as `Market` inside `Market Neutral`.

This material glosses its own terminology, and consistently: `Long Only`, `ou somente comprado`,
`Market Neutral (Mercado Neutro)`, `Risk Budgeting, ou Orçamento de Risco`. That is a bilingual
dictionary the corpus is already offering, and mining it is both more accurate than translating
and closer to what the objective asks for — a controlled vocabulary anchored in the source.

Every candidate is printed with the sentence it came from, because a gloss pattern will also
catch appositions that are not glosses at all, and that judgement is not the script's to make.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "cga-2026-markdown"
DEFAULT_OUTPUT = ROOT / "reports" / "portuguese-glosses.json"
REGISTRY = ROOT / "src" / "semantic_normalizer" / "data" / "registry.jsonl"

# How this material introduces a translation, in the order it prefers them.
GLOSS = (
    r"\(\s*(?:ou\s+)?(?P<g1>[^)]{4,60}?)\s*\)",          # Market Neutral (Mercado Neutro)
    r",\s*ou\s+(?P<g2>[^,.;:]{4,60}?)\s*[,.;:]",          # Risk Budgeting, ou Orçamento de Risco,
    r"\s+ou\s+(?P<g3>[^,.;:]{4,60}?)\s*[,.;:]",           # Long Only ou somente comprado.
    r"[,:]\s*(?:com\s+)?tradu[çc][ãa]o\s+(?:literal\s+)?(?:ficaria\s+)?(?:como\s+)?"
    r"[“\"']?(?P<g4>[^”\"'.;]{4,60}?)[”\"'.;]",           # ..., com tradução literal de “...”
    r"[,:]\s*(?:poder[íi]amos\s+)?traduzir\s+para\s+o\s+portugu[êe]s\s+como\s+"
    r"[“\"']?(?P<g5>[^”\"'.;]{4,60}?)[”\"'.;]",
)
NOISE = re.compile(r"^(?:e|o|a|os|as|um|uma|de|do|da|em|no|na|por|para|ver|isto é|ou seja)\b", re.I)


def fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    records = [
        json.loads(line)
        for line in REGISTRY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    known = {
        fold(entry["form"])
        for record in records
        for language in ("en", "pt-BR")
        for entry in record["lexical_forms"][language]
    }
    # Multi-word English labels are the ones that get glossed; a bare token is not introduced.
    targets = [
        (record["concept_id"], entry["form"])
        for record in records
        for entry in record["lexical_forms"]["en"]
        if len(entry["form"].split()) > 1 and entry["policy"] == "auto"
    ]

    text = " ".join(
        Path(path).read_text(encoding="utf-8", errors="replace")
        for path in sorted(glob.glob(str(Path(args.corpus) / "*.md")))
    )
    folded = fold(text)

    findings = []
    for concept_id, label in targets:
        needle = fold(label)
        occurrences = [m.start() for m in re.finditer(re.escape(needle), folded)]
        if not occurrences:
            continue
        glosses: collections.Counter[str] = collections.Counter()
        quotes: dict[str, str] = {}
        for start in occurrences:
            tail = text[start + len(label): start + len(label) + 90]
            for pattern in GLOSS:
                match = re.match(r"\s*" + pattern, tail)
                if not match:
                    continue
                candidate = next(v for v in match.groupdict().values() if v)
                candidate = candidate.strip(" “”\"'")
                if (len(candidate.split()) < 2 or NOISE.match(candidate)
                        or fold(candidate) in known or fold(candidate) == needle):
                    continue
                glosses[candidate] += 1
                quotes.setdefault(candidate, text[max(0, start - 40): start + 130].replace("\n", " "))
                break
        if glosses:
            findings.append({
                "concept_id": concept_id,
                "english": label,
                "occurrences": len(occurrences),
                "glosses": [
                    {"portuguese": gloss, "times": times, "quote": quotes[gloss]}
                    for gloss, times in glosses.most_common()
                ],
            })

    # The other half of the same defect: a registered label the corpus never uses.
    unattested = []
    for record in records:
        for entry in record["lexical_forms"]["pt-BR"]:
            if entry["policy"] != "auto" or len(entry["form"].split()) < 2:
                continue
            if fold(entry["form"]) not in folded:
                unattested.append({"concept_id": record["concept_id"], "form": entry["form"]})

    report = {
        "schema_version": "portuguese-glosses-v1",
        "english_labels_examined": len(targets),
        "labels_with_a_corpus_gloss": len(findings),
        "unattested_automatic_pt_labels": len(unattested),
        "findings": findings,
        "unattested": unattested,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in
                      ("english_labels_examined", "labels_with_a_corpus_gloss",
                       "unattested_automatic_pt_labels")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
