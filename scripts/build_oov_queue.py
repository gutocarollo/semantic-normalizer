#!/usr/bin/env python3
"""Freeze the CGA corpus and build the out-of-vocabulary work queue.

Step 1 of `docs/plan-cga-domain-lexicon-0.4.0.md`. Everything the planning review found
undocumented lives here, in the script, not in a person's head: the corpus glob, the sentence
rule, the length filter, the stopword list and the mathematical-markup cleanup.

The queue is emitted in three strata (plan D4), because they are different kinds of work:

* `unknown`   — no form in the registry. Authoring or import.
* `ambiguous` — already has candidate concepts; lands in `unresolved` because the match is
                ambiguous, not because a concept is missing. Adjudication, not authoring.
* `function`  — grammatical word with no dictionary value.

Determinism: same corpus bytes produce a byte-identical `oov-queue.json`.
"""

from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "cga-2026-markdown"
DEFAULT_OUTPUT = ROOT / "reports" / "oov-queue.json"

MIN_SENTENCE, MAX_SENTENCE = 40, 300
WORD = re.compile(r"[a-zà-ÿA-ZÀ-Ÿ][a-zà-ÿA-ZÀ-Ÿ\-]{2,}")

# --------------------------------------------------------------------------------------
# Mathematical markup (plan D7)
# --------------------------------------------------------------------------------------
# `R$` and `US$` use the same character that delimits inline LaTeX. The corpus has 256 of
# them. A rule that just strips `$...$` deletes real money:
#
#   "avaliada em R$ 12.000,00 no início do mês. … de R$ 18.250,00."
#     -> "avaliada em R 18.250,00."      75 characters of financial content, gone
#
# So a `$` preceded by a letter is never a delimiter, and a delimited span is only removed
# when it actually contains TeX. Both directions are asserted by `verify_cleanup`.
#
# The same trap exists for display math, and it bites harder. The corpus writes `($$)` in a
# table header to mean "in currency units"; the naive `\$\$.*?\$\$` pairs that with a distant
# `$$` and swallows an entire table — 7 `R$` values in one match, measured. So a delimited
# span is removed only when it actually contains TeX, block and inline alike.
BLOCK_MATH = re.compile(r"(?<![A-Za-z])\$\$(.*?)\$\$", re.S)
INLINE_MATH = re.compile(r"(?<![A-Za-z])\$([^\$\n]{1,120}?)\$")
# Display math is one contiguous formula. It never crosses a markdown table row or a blank
# line, and a span that does is the `($$)` header pairing with a distant delimiter — measured
# swallowing two tables and 17 currency values.
NOT_FORMULA = re.compile(r"\n\s*\n|\|")
TEXISH = re.compile(r"\\[a-zA-Z]|\\[ \\\\]|_\{|\^\{|_[0-9]|\^[0-9]")
TEX_COMMAND = re.compile(r"\\[a-zA-Z]+")
# The other LaTeX delimiters. `TEX_COMMAND` requires a letter after the backslash, so `\[` and
# `\(` survive it and so does everything between them: a residual sample found `VaR` matching
# inside `\[ {VaR}(X+Y)=25.11 \]`, which is a formula, not a sentence. There are 22 bracket
# blocks and 10 paren blocks in the corpus. The `TEXISH` guard is kept for the same reason it
# exists on `$`: remove a delimited span only when it really contains TeX.
LATEX_BRACKET = re.compile(r"\\\[(.*?)\\\]", re.S)
LATEX_PAREN = re.compile(r"\\\((.*?)\\\)", re.S)
CURRENCY = re.compile(r"(?:R|US)\$")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
# Markdown image destinations are filesystem paths, not prose. The corpus has 49 of them and
# they leaked `assets`, `figures` and `long` (from `p234-01.png` neighbours) into the queue as
# high-frequency pseudo-terms. The alt text IS prose and is kept.
IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)", re.S)

# --------------------------------------------------------------------------------------
# Stopwords (plan D8)
# --------------------------------------------------------------------------------------
# Union of NLTK `stopwords.words("portuguese")` (207 words, vendored below so the build is
# deterministic and offline) and a local list for what this corpus surfaced. The review
# showed the local list alone let `para` (332 occurrences), `com` (275), `por` (244) and
# `uma` (238) through into the automatic-import bucket; NLTK alone misses `assim`, `apenas`,
# `cada`, `maior`, `menor`. Neither is a superset of the other, so the union is used.
NLTK_PT = """a ao aos aquela aquelas aquele aqueles aquilo as até com como da das de dela
delas dele deles depois do dos e ela elas ele eles em entre era eram essa essas esse esses
esta estamos estar estas estava estavam este esteja estejam estejamos estes esteve estive
estivemos estiver estivera estiveram estiverem estivermos estivesse estivessem estivéramos
estivéssemos estou está estávamos estão eu foi fomos for fora foram forem formos fosse
fossem fui fôramos fôssemos haja hajam hajamos havemos haver hei houve houvemos houver
houvera houveram houverei houverem houveremos houveria houveriam houvermos houverá houverão
houveríamos houvesse houvessem houvéramos houvéssemos há hão isso isto já lhe lhes mais mas
me mesmo meu meus minha minhas muito na nas nem no nos nossa nossas nosso nossos num numa
não nós o os ou para pela pelas pelo pelos por qual quando que quem se seja sejam sejamos
sem ser serei seremos seria seriam será serão seríamos seu seus somos sou sua suas são só
também te tem temos tenha tenham tenhamos tenho terei teremos teria teriam terá terão
teríamos teu teus teve tinha tinham tive tivemos tiver tivera tiveram tiverem tivermos
tivesse tivessem tivéramos tivéssemos tu tua tuas tém tínhamos um uma você vocês vos à às é
éramos""".split()

LOCAL_PT = """pode podem deve devem forma maior menor caso exemplo assim sejam cada porém
portanto ainda apenas então logo bem pouco todo toda todos todas outro outra outros outras
qualquer alguns algumas cujo cuja onde enquanto após antes durante desde através sobre
dentro fora acima abaixo tanto tão sempre nunca somente aquela geral gerais grande grandes
pequeno pequena melhor pior primeiro segundo terceiro nesse nessa neste nesta desta deste
dessa desse vez vezes parte partes ponto pontos lado lados modo modos tipo tipos nível
níveis quanto sendo possui possuem podemos além tais pois disso porque geralmente menos
possível contra dois importante diferentes diferente devido acordo respectivamente inclusive
exemplos abaixo acima seguir seguinte anterior próximo dado dada dados dadas feita feito
feitas feitos maiores menores altas baltas baixas baixos altos considerado considerada
utilizado utilizada utilizados utilizadas chamado chamada determinado determinada
representa representam significa apresenta apresentam ocorre ocorrem existe existem
""".split()

STOPWORDS = frozenset(NLTK_PT) | frozenset(LOCAL_PT)


def strip_math(text: str) -> str:
    """Remove markdown image paths and mathematics, never currency and never alt text."""
    def drop_block(match: re.Match[str]) -> str:
        content = match.group(1)
        if NOT_FORMULA.search(content) or not TEXISH.search(content):
            return match.group(0)
        return " "

    text = IMAGE.sub(lambda m: " " + m.group(1) + " ", text)
    text = LATEX_BRACKET.sub(drop_block, text)
    text = LATEX_PAREN.sub(drop_block, text)
    text = BLOCK_MATH.sub(drop_block, text)
    text = INLINE_MATH.sub(
        lambda m: " " if TEXISH.search(m.group(1)) else m.group(0), text
    )
    return TEX_COMMAND.sub(" ", text)


def verify_cleanup(before: str, after: str) -> dict:
    """The gate for step 1: no TeX survives, and not one currency marker is lost."""
    return {
        "currency_before": len(CURRENCY.findall(before)),
        "currency_after": len(CURRENCY.findall(after)),
        "tex_commands_before": len(TEX_COMMAND.findall(before)),
        "tex_commands_after": len(TEX_COMMAND.findall(after)),
    }


def sentences_of(text: str) -> list[str]:
    """Split into sentences, treating a markdown heading as a hard boundary.

    Splitting only on `.!?` glued a heading into the middle of 2.5 % of sentences: a line
    ending in a colon has no terminator, so `"...seria de: ## Taxas de juros A taxa Libor..."`
    came out as one unit and put heading words into the frequency queue as if they were prose
    from the preceding paragraph. A heading is a boundary regardless of punctuation.
    """
    found = []
    for block in re.split(r"^#{1,6}\s.*$", text, flags=re.M):
        for sentence in re.split(r"(?<=[.!?])\s+", block):
            sentence = " ".join(sentence.split())
            if MIN_SENTENCE < len(sentence) < MAX_SENTENCE and not sentence.startswith(("#", "|")):
                found.append(sentence)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--contexts", type=int, default=3, help="corpus quotes kept per term")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    from semantic_normalizer.normalizer import normalize_text
    from semantic_normalizer.registry import load_lexicon, load_registry

    lexicon = load_lexicon()
    registry = load_registry()

    paths = sorted(glob.glob(str(Path(args.corpus) / "*.md")))
    if not paths:
        raise SystemExit(f"no corpus files under {args.corpus}")

    corpus_hash = hashlib.sha256()
    raw_all, clean_all, sentences = [], [], []
    per_file = []
    for path in paths:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        corpus_hash.update(Path(path).read_bytes())
        clean = strip_math(HTML_COMMENT.sub(" ", raw))
        raw_all.append(raw)
        clean_all.append(clean)
        found = sentences_of(clean)
        sentences.extend(found)
        per_file.append({"path": Path(path).name, "sentences": len(found)})

    cleanup = verify_cleanup("\n".join(raw_all), "\n".join(clean_all))
    if cleanup["currency_before"] != cleanup["currency_after"]:
        raise SystemExit(
            f"cleanup destroyed currency: {cleanup['currency_before']} -> "
            f"{cleanup['currency_after']}. See plan D7."
        )
    if cleanup["tex_commands_after"]:
        raise SystemExit(f"{cleanup['tex_commands_after']} TeX commands survived cleanup")

    # Every surface the registry can already produce a candidate for, in any language.
    known_forms = {
        entry["form"].casefold()
        for record in registry["records"]
        for forms in record["lexical_forms"].values()
        for entry in forms
    }

    frequency: collections.Counter[str] = collections.Counter()
    contexts: dict[str, list[str]] = {}
    for index, sentence in enumerate(sentences):
        for record in normalize_text(sentence, source=f"cga:{index}", kind="text", lexicon=lexicon):
            for span in record.get("unresolved") or []:
                for word in WORD.findall(span["original"]):
                    word = word.casefold()
                    frequency[word] += 1
                    bucket = contexts.setdefault(word, [])
                    if len(bucket) < args.contexts and sentence not in bucket:
                        bucket.append(sentence)

    strata: dict[str, list] = {"unknown": [], "ambiguous": [], "function": []}
    for term, count in frequency.most_common():
        if term in STOPWORDS:
            stratum = "function"
        elif term in known_forms:
            stratum = "ambiguous"
        else:
            stratum = "unknown"
        strata[stratum].append(
            {"term": term, "occurrences": count, "contexts": contexts.get(term, [])}
        )

    if any(term["term"].startswith("\\") for group in strata.values() for term in group):
        raise SystemExit("a TeX token reached the queue; cleanup is incomplete")

    payload = {
        "schema_version": "oov-queue-v1",
        "corpus": {
            "path": str(Path(args.corpus).name),
            "files": len(paths),
            "sha256": corpus_hash.hexdigest(),
            "per_file": per_file,
            "sentences": len(sentences),
        },
        "cleanup": cleanup,
        "parameters": {
            "min_sentence": MIN_SENTENCE,
            "max_sentence": MAX_SENTENCE,
            "stopwords": len(STOPWORDS),
            "contexts_per_term": args.contexts,
        },
        "registry": {"version": registry["version"], "sha256": registry["hash"]},
        "totals": {
            name: {
                "terms": len(group),
                "occurrences": sum(item["occurrences"] for item in group),
            }
            for name, group in strata.items()
        },
        "strata": strata,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps({"output": str(output.relative_to(ROOT)), "corpus_sha256": payload["corpus"]["sha256"][:16],
                      "sentences": len(sentences), "cleanup": cleanup,
                      "totals": payload["totals"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
