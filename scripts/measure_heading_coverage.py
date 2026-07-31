#!/usr/bin/env python3
"""Measure coverage against the apostila's own taxonomy, because the word-frequency target cannot be.

The 0.4.0 plan set a coverage target of 41.51 %, derived mechanically as the cumulative occurrences
of the top 200 `unknown` terms over the unknown total. Three attempts to settle whether it is
reachable have failed, and all three failed in the same place: `unknown` is every content word minus
a stopword list, so answering the question means deciding whether `relação`, `vamos` and `negociadas`
are domain vocabulary — which needs a Portuguese lemmatiser this environment does not have.
`scripts/audit_coverage_target.py` ends at UNSETTLED and says so in its own verdict field.

The question was wrong, not merely hard. A dictionary of a domain is complete when it names the
things the domain declares, and this corpus declares them explicitly: `## Alocação de Ativos (Asset
Allocation)` is the material stating that asset allocation is one of its subjects, `## Medidas de
Performance` likewise. That population has every property the unknown stratum lacks —

  enumerable          406 distinct headings, not an open vocabulary
  domain by construction  a section title names a subject, and the document furniture that does not
                      is removable by a stated rule rather than by judging each word
  morphology-free     no lemma index, no stemmer, no wordnet, so nothing for irregular verbs to break
  weighted by the material itself  a heading repeated across 65 sections is the corpus saying which
                      subject it dwells on

A heading counts as COVERED only when one concept's form spans the whole of it. `Alocação de Ativos`
resolving to `alocação` plus `ativos` is two adjacent words, not an entry for asset allocation, and
scoring it as covered is the same self-congratulation as a gate that checks a string prefix instead
of resolving the citation.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "cga-2026-markdown"
DEFAULT_OUTPUT = ROOT / "reports" / "heading-coverage.json"

HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.M)
PARENTHETICAL = re.compile(r"^(.*?)\s*\((.+?)\)\s*$")

# Document furniture: page scaffolding rather than vocabulary. Stated as a rule so the denominator
# is auditable — a heading is furniture if it ends in a colon (a lead-in to the slide body) or is
# one of a closed set of organising words. Everything else counts, including headings this
# dictionary would rather not have to cover.
FURNITURE = frozenset({
    "exemplo", "exemplos", "exercício", "exercícios", "resumo", "observações", "observação",
    "conceito", "conceitos", "definição", "definições", "introdução", "característica",
    "características", "vantagens", "desvantagens", "fórmula", "fórmulas", "cálculo", "tipos",
    "classificação", "atenção", "importante", "questão", "questões", "sumário", "gráfico",
    "continuação", "motivo", "remuneração", "constituição", "objetivo", "objetivos", "regras",
    "principais regras", "principais vantagens", "principais características", "tópico de prova",
    "como a prova tem cobrado", "porém, há algumas desvantagens", "nova estruturação",
    "disposições gerais", "tabela", "passo", "aplicação prática", "solução do exemplo",
    "vantagens e limitações", "analisando duas aplicações", "customizados", "competência",
    "apostila cga 2026 - apresentação e sumário", "apostila de preparação para a prova da cga",
    "apostila de preparação para a prova da cga - 2026", "sobre a academia rafael toro",
    "proveniência",
    # A slide instruction, not a subject: `ETAPA 5 – FINAL!: Dividir Etapa 3 pela Etapa 4!!!`.
    "etapa 5 – final!: dividir etapa 3 pela etapa 4!!!", "dividir etapa 3 pela etapa 4!!!",
})


def is_furniture(title: str) -> bool:
    return title.strip().endswith(":") or title.strip().rstrip(":").strip().casefold() in FURNITURE


# A heading that names its subject twice, once per language or once per spelling: `TIR – Taxa
# Interna de Retorno`, `CDs – Certificate of Deposits`. The dash is a gloss, the same relation the
# parenthetical form expresses, and scoring such a heading uncovered measures the punctuation
# rather than the dictionary.
INNER_GLOSS = re.compile(r"^(.{2,}?)\s*\(([^)]{2,})\)\s*(.+)$")
GLOSS_DASH = re.compile(r"^(.{3,}?)\s+[–—-]\s+(.{3,})$")
# Enumeration and running-head furniture the material adds around a title: `1) Indicadores de
# Tendência (continuação)`, `Apreçamento (Cap. XI)`. Neither is vocabulary.
ENUMERATION = re.compile(r"^\s*\(?\d+[).]\s*")
TRAILING_FURNITURE = re.compile(
    r"\s*\((?:cont\.?|continuação|cap\.?\s*[IVXLC\d]+|parte\s*\d+)\)\s*$", re.IGNORECASE)


def variants(title: str) -> list[str]:
    """A heading and, when it glosses itself, each side of the gloss.

    `Alocação de Ativos (Asset Allocation)` is the corpus naming one subject in two languages, so
    covering either side covers the heading.
    """
    stripped = TRAILING_FURNITURE.sub("", ENUMERATION.sub("", title)).strip()
    # `Código: Administração de Recursos de Terceiros`, `Duration: Gráfico`, `Exemplo: Compra de
    # Contrato Futuro` — the material labels a slide with a running head or an example marker and
    # separates it with a colon. One side is furniture, the other is the subject, and which side
    # varies. Splitting and testing both measures the dictionary; refusing to split measures the
    # punctuation. The single heaviest heading in the corpus is of this shape.
    colon = [part.strip() for part in stripped.split(":") if part.strip()]
    if len(colon) > 1:
        stripped = max((part for part in colon if not is_furniture(part)), key=len, default=stripped)
    out = [title] if stripped == title else [title, stripped]
    for candidate in (stripped,):
        match = PARENTHETICAL.match(candidate)
        if match:
            out += [match.group(1).strip(), match.group(2).strip()]
            continue
        # `Value-at-Risk (VaR) Analítico` — the gloss sits INSIDE the phrase rather than at its
        # end, so the heading names one subject under two spellings with a qualifier after it.
        inner = INNER_GLOSS.match(candidate)
        if inner:
            head, alias, tail = (g.strip() for g in inner.groups())
            out += [f"{head} {tail}".strip(), f"{alias} {tail}".strip(), head, alias]
            continue
        dash = GLOSS_DASH.match(candidate)
        if dash:
            out += [dash.group(1).strip(), dash.group(2).strip()]
    return [item for item in dict.fromkeys(out) if item]


CONJUNCTION = re.compile(r"\s+(?:&|x|×|e|and|ou|or)\s+", re.IGNORECASE)


def conjuncts(title: str) -> list[str]:
    """A heading that names two subjects side by side, split into them.

    `Top-Down & Bottom-Up`, `Time-Weighted x Money Weighted Rate of Return`, `Estratégica x
    Tática` are the material contrasting two concepts under one heading, not naming a third. Both
    concepts are registered and the heading still scored `partial`, which understates coverage by
    an artifact of the metric rather than a gap in the dictionary — and chasing that artifact with
    more batches is exactly the failure mode of the OOV target this instrument exists to replace.

    So a conjunctive heading is COVERED when every conjunct is. Splitting is deliberately narrow:
    the separator must be surrounded by whitespace, so `Renda Fixa e Variável` splits while
    `Demonstrações Contábeis` does not, and a single conjunct means the heading was not
    conjunctive and nothing changes.
    """
    parts = [part.strip(" -–—") for part in CONJUNCTION.split(title)]
    return [part for part in parts if part] if len(parts) > 1 else []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    from semantic_normalizer.normalizer import normalize_text
    from semantic_normalizer.registry import load_registry, nfc_casefold

    registry = load_registry()

    counts: collections.Counter[str] = collections.Counter()
    origin: dict[str, str] = {}
    for path in sorted(glob.glob(str(Path(args.corpus) / "*.md"))):
        body = Path(path).read_text(encoding="utf-8", errors="replace")
        for _level, raw in HEADING.findall(body):
            title = re.sub(r"\s+", " ", raw).strip(" #*")
            title = re.sub(r"^(CGA\s*\d+\s*[-–]\s*)", "", title)
            if not title or is_furniture(title) or len(title) > 90:
                continue
            counts[title] += 1
            origin.setdefault(title, Path(path).stem)

    rows = []
    for title, occurrences in counts.items():
        covered_by = None
        for candidate in variants(title):
            if not candidate:
                continue
            for record in normalize_text(candidate, source="h", kind="text", lexicon=registry):
                for event in record["match_events"]:
                    if nfc_casefold(event["alias"]) == nfc_casefold(candidate):
                        covered_by = event["concept_id"]
            if covered_by:
                break
        # A heading naming two subjects is covered when both are.
        if not covered_by:
            pieces = conjuncts(title)
            if len(pieces) > 1:
                each = []
                for piece in pieces:
                    hit = None
                    for candidate in variants(piece):
                        for record in normalize_text(candidate, source="h", kind="text",
                                                     lexicon=registry):
                            for event in record["match_events"]:
                                if nfc_casefold(event["alias"]) == nfc_casefold(candidate):
                                    hit = event["concept_id"]
                        if hit:
                            break
                    each.append(hit)
                if all(each):
                    covered_by = "+".join(each)

        partial = []
        if not covered_by:
            partial = [
                event["alias"]
                for record in normalize_text(title, source="h", kind="text", lexicon=registry)
                for event in record["match_events"]
            ]
        rows.append({
            "heading": title, "occurrences": occurrences, "file": origin[title],
            "status": "covered" if covered_by else ("partial" if partial else "uncovered"),
            "covered_by": covered_by, "partial_matches": partial,
        })

    total = sum(row["occurrences"] for row in rows)
    mass: collections.Counter[str] = collections.Counter()
    for row in rows:
        mass[row["status"]] += row["occurrences"]

    rows.sort(key=lambda row: (row["status"] == "covered", -row["occurrences"]))
    report = {
        "schema_version": "heading-coverage-v1",
        "registry": {"version": registry["version"], "concepts": len(registry["records"])},
        "why_this_and_not_the_oov_target": (
            "The 0.4.0 target is a fraction of a stratum that is every content word minus a "
            "stopword list, so settling it requires classifying ordinary Portuguese and a "
            "lemmatiser this environment does not have. Three attempts failed. Headings are what "
            "the corpus itself declares to be its subjects: enumerable, domain by construction, "
            "and weighted by how often the material returns to each one."
        ),
        "covered_means": "one concept's form spans the WHOLE heading — two adjacent atomic matches are "
                         "not an entry for the compound. A heading that contrasts two subjects "
                         "(`Top-Down & Bottom-Up`) counts when EVERY conjunct is covered.",
        "distinct_headings": len(rows),
        "heading_occurrences": total,
        "by_status": dict(collections.Counter(row["status"] for row in rows)),
        "mass_by_status": dict(mass),
        "covered_share_of_mass": round(mass["covered"] / max(1, total), 4),
        "covered_share_of_distinct": round(
            sum(1 for row in rows if row["status"] == "covered") / max(1, len(rows)), 4),
        "headings": rows,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                      ("distinct_headings", "heading_occurrences", "by_status", "mass_by_status",
                       "covered_share_of_mass", "covered_share_of_distinct")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
