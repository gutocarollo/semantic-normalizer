# semantic-normalizer

A governed bilingual (pt-BR / en) **concept registry** and a deterministic **text normalizer** built
on it. You give it prose; it tells you which concepts the prose refers to, where each one starts and
ends, and what the sentence looks like once every recognised surface is rewritten to its canonical
form.

It is not an embedding model and not an LLM. It is a dictionary with a matcher, and every answer is
reproducible from the registry hash.

The shipped registry covers the **CGA/ANBIMA** Brazilian financial-certification domain — 584
concepts, measured against a 12-file course corpus.

---

## Where the ontology lives

```
src/semantic_normalizer/data/
  registry.jsonl            ← THE ONTOLOGY. one JSON object per concept, 584 lines
  registry.schema.json      ← the closed schema every record must satisfy
  registry.release.json     ← version + sha256 of every shipped file
  registry.provenance.jsonl ← append-only ledger: every batch and amendment ever applied
```

`registry.jsonl` is the artifact. Everything else in this repo either builds it, measures it, or
consumes it. One record, abridged:

```json
{
  "concept_id": "entity.financial_investment_fund",
  "definition": "The CVM 175 category covering funds whose portfolio is financial assets…",
  "semantic_class": "entity",
  "contexts": ["cga", "finance"],
  "labels": {
    "pt-BR": {"pref": "Fundo de Investimento Financeiro",
              "alt": ["FIF", "fundos de investimento financeiro"],
              "hidden": [], "observed": []},
    "en":    {"pref": "financial investment fund", "alt": [], "hidden": [], "observed": []}
  },
  "lexical_forms": {
    "pt-BR": [{"form": "FIF", "policy": "auto", "features": {}}, "…"]
  },
  "forbidden_variants": {"pt-BR": [], "en": []},
  "authority": "apostila-cga-2026#04-resolucao-cvm-175-fundos-de-investimento"
}
```

Three fields carry most of the weight:

| field | what it does |
|---|---|
| `policy` on each form | `auto` = the matcher may fire on it unattended. `review` = known-ambiguous, never fires automatically. |
| `labels.*.pref` | the **canonical** surface. This is what a rewrite substitutes, so it must be the lemma, not a heading spelling. |
| `contexts` | which domain packs the concept belongs to. Read at load time — see *Plug-and-play*. |

---

## Quick start

```bash
pip install -e .
make check          # validate registry + rebuild manifest + run the suite
```

```python
from semantic_normalizer import load_lexicon, normalize_text

lex = load_lexicon(contexts=["core", "cga"])
[out] = normalize_text(
    "É vedada a aplicação em cotas de FIF destinadas a investidores qualificados, "
    "exceto se a classe for restrita.",
    source="doc.md", kind="text", lexicon=lex,
)

out["concept_ids"]
# ['polarity.prohibition', 'action.subscribe', 'entity.fund_share',
#  'entity.financial_investment_fund', 'actor.investor', 'polarity.exception']

out["canonical_text"]
# 'É vedada a aplicar cota de Fundo de Investimento Financeiro destinadas a
#  investidor qualificados, exceto se a classe for restrita.'
```

That output is real, not illustrative — it is what the shipped registry produces today.

Two things to notice, because they are the design and not accidents:

- **`FIF` resolved to the full concept.** The acronym and `Fundo de Investimento Financeiro` are one
  concept, so a query for either finds both.
- **`canonical_text` is a LEMMA projection, not publishable prose.** `aplicação em → aplicar`,
  `cotas → cota`. It exists so two spellings of one thing collapse to a single string for indexing;
  `canonical_mappings` carries byte offsets back to the source for every rewrite.

Each record also carries `byte_start` / `byte_end` / `column`, `language`, and `lexicon_sha256`, so a
downstream consumer can prove which registry produced a given annotation.

---

## Plug-and-play: domain packs

The registry is **one file with scheme membership**, not one file per domain. Load it scoped:

```python
load_lexicon()                              # 584 concepts, 1582 automatic forms
load_lexicon(contexts=["cga"])              # 498 concepts, 1349 forms
load_lexicon(contexts=["core"])             #  51 concepts,  152 forms
load_lexicon(contexts=["core", "cga"])      # 519 concepts, 1402 forms
load_lexicon(contexts=["no-such-domain"])   # ContractError, listing what IS declared
```

**`core`** holds the 51 domain-agnostic operators — negation, prohibition, conditionals, temporal
markers, comparators. `polarity.negation` is not financial, and a domain pack without `não`,
`exceto` and `vencimento` is not a smaller dictionary, it is a broken one. Every pack composes with
`core`.

### Why scoping instead of one merged table

83 % of all matches (6,446 of 7,780) come from bare single-word Portuguese forms, and those are
exactly the surfaces that mean different things in different fields:

| form | in this pack | elsewhere |
|---|---|---|
| `ação` | share / stock | lawsuit (law) · a drug's action (medicine) |
| `título` | debt security | deed · academic degree |
| `fluxo` | cash flow | blood flow · laminar flow |
| `resistência` | — | electrical resistance · bacterial resistance |

When two concepts claim one surface the collision demoter sends **both** to `review` — so merging
medicine into the financial pack would degrade the financial pack too. The canonical counter-example
is the UMLS metathesaurus, whose most-cited problem is precisely ambiguity and duplication across
merged vocabularies.

Under a scoped load an out-of-scope form is not demoted or down-ranked. It is **absent from the table
the matcher reads**, so it cannot collide at all.

Integrity is still checked over the whole registry: relations, duplicate ids and schema conformance
are global. Scoping narrows what *matches*, never what is *validated*.

### Adding a domain

1. Write a batch config in `config/` whose concepts carry `"contexts": ["<domain>"]`.
2. `python scripts/import_cga_batch.py --batch config/<file>.json --registry-version <next>`
3. Measure it (see *Proving it works*). **A precision figure is a statement about a corpus** — keep
   each domain's sweep separate or the number stops being auditable.

---

## Use cases

### 1. Retrieval that survives paraphrase

A user searches `FIF` and the document says `Fundo de Investimento Financeiro`; or searches
`aceite bancário` and the text says `Banker's acceptance`. Lexical search misses both.

```python
lex = load_lexicon(contexts=["core", "cga"])

def concept_key(text):
    return {c for r in normalize_text(text, source="q", kind="text", lexicon=lex)
              for c in r["concept_ids"]}

concept_key("FIF") & concept_key("Fundo de Investimento Financeiro")
# {'entity.financial_investment_fund'} — same key, so the same index entry
```

Index documents by `concept_ids` alongside raw tokens: cross-language, cross-spelling recall without
an embedding model, and every hit is explainable by pointing at a registry line.

### 2. Compliance and rule extraction

A regulatory sentence's meaning lives in its operators, and normalizing makes the modality explicit:

```
"É vedada a aplicação em cotas de FIF … exceto se a classe for restrita."
  →  polarity.prohibition + action.subscribe + entity.fund_share
     + entity.financial_investment_fund + polarity.exception
```

`prohibition` and `exception` are `core`, so the same extraction works for a medical or engineering
pack. You can ask for "every sentence that prohibits something about `entity.fund_share`" without
writing a regex per phrasing.

### 3. Terminology governance across a team

`make export` emits SKOS with one `skos:ConceptScheme` per domain — 4,190 triples, 1,217 `inScheme`
statements — loadable into any triple store or glossary tool. `exports/synonyms.txt` emits only
approved automatic equivalences, in the format most search engines accept as a synonym file.

### 4. LLM grounding

Feed `concept_ids` and their definitions to a model instead of raw text, and it reasons over concepts
you can enumerate rather than strings it may invent. `authority` on every record cites the source
document and anchor, so an answer traces back to the page it came from.

---

## Proving it works

The claims are measured and the commands are in the repo:

```bash
python scripts/audit_precision.py           # every match event, with context
python scripts/report_precision.py          # Wilson-bounded precision from adjudicated draws
python scripts/measure_heading_coverage.py  # coverage against the corpus's own section headings
python scripts/cut_coverage_ceiling.py      # what is still uncovered, and why each item is refused
```

Current published figures for the CGA pack (`reports/`):

| metric | value | what it means |
|---|---|---|
| precision, 95 % lower bound | **0.9855** | 240 events drawn from the unread residual, read one by one, zero errors. The bound is the claim: a zero-error sample that size cannot rule out rates up to ~1.45 %. |
| coverage, distinct headings | **0.9532** | 326 of the corpus's 342 section headings resolve to a registered concept. |
| coverage, share of mass | 0.9781 | the same, weighted by how often each heading appears. |

**The admission rule** is why the remaining 16 headings stay uncovered: a phrase is registered only
when the *concept* is independently attested — already in the registry, or defined in the corpus
prose — **never because it appears as a heading**. Fifteen of the sixteen occur zero times outside a
heading. Registering them would make the metric measure itself.

---

## Repository map

```
src/semantic_normalizer/
  registry.py     load + fail-closed validation + domain scoping
  normalizer.py   the matcher: longest-match resolution, protected spans, canonical rewrite
  exporters.py    SKOS (with concept schemes), synonym graph
  cli.py          normalize / query / export / evaluate / validate-registry
  data/           THE ONTOLOGY (see top of this file)
config/           every batch and amendment ever applied, with its adjudication reasoning
scripts/          build, audit, measure, repair
reports/          the evidence behind every number quoted above
tests/            147 tests / ~2,993 subtests
docs/             architecture, governance, data-governance, evaluation plan
```

`config/` is worth reading before trusting a number: each amendment states what changed, how many
occurrences were counted, and **why candidates were refused**. Several entries record defects found
and repaired, including one where a fix was reverted because the diagnosis behind it was wrong.

---

## Honest limits

- **The corpus is not in this repo.** Scripts expect it at `../cga-2026-markdown`. The library works
  and the unit tests pass without it, but the corpus-dependent measurements cannot be reproduced.
- **`canonical_text` is a lemma projection.** It routinely disagrees with the source in grammatical
  number (`os títulos → Os Título`). Intended for indexing, wrong for display — map back through
  `canonical_mappings` rather than showing it to a user.
- **Only pt-BR and en.** `LANGUAGES` is a two-element constant; a third language is a schema change.
- **Precision is sampled; coverage is exhaustive.** Coverage checks all 342 headings. Precision rests
  on 873 of 7,803 events read individually (~11 %), and `reports/precision-final.json` carries a
  `what_this_sample_cannot_detect` section saying so.
- **One known unrepaired engine defect.** A candidate that wins overlap resolution and is then refused
  by the protection guard suppresses the valid shorter match that lost to it. Reproduced and pinned
  by a regression test, not fixed: the repair changes overlap resolution and needs its own sweep.

## License

See `LICENSE`.
