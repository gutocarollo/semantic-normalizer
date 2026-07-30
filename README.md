# Semantic Normalizer Skill

A runnable prototype for reversible English-Portuguese concept canonicalization. It is designed for technical documentation, comments, knowledge bases, grep, BM25, and hybrid retrieval.

## Design decision

The project does **not** try to reduce English and Portuguese to one universal word list. It separates:

1. **Concept identity** — stable, language-independent IDs such as `action.start`.
2. **Preferred labels** — one canonical label per language, such as `start` and `iniciar`.
3. **Observed variants** — synonyms, inflections, abbreviations, and hidden labels.
4. **Source assertions** — negation, modality, conditions, numbers, identifiers, and original spans.

This prevents a common failure of synonym replacement: two words can be close in one context and materially different in another. Version 0.2.0 also separates the domain concept registry from the human controlled lexicon: concepts are stable identities; lexical forms are governed, sourced, licensed and approved evidence.

Concept IDs and concept tokens are the authoritative canonical layer. `canonical_text` is deliberately conservative: it preserves an inflected source form when replacing it with a lemma would damage grammar.

## Architecture

```text
source text
   │
   ├── immutable original field
   │
   ├── protect code / IDs / paths / quoted text
   │
   ├── preferred + alternative + hidden labels
   │
   ├── context-based concept disambiguation
   │       └── optional constrained resolver for unresolved spans
   │
   ├── deterministic semantic validators
   │
   └── parallel projections
           ├── canonical text
           ├── concept IDs
           ├── concept tokens
           ├── semantic operator tokens
           ├── sentence segments
           └── augmented search text
```

The agent is deliberately narrow. It can select only a candidate concept already generated from the registry. It cannot invent a concept or rewrite the entire document.

## Quick start

The core runtime has no third-party dependency.

```bash
cd semantic-normalizer-skill

# Registry validation
PYTHONPATH=src python3 -m semantic_normalizer validate-registry

# Source-language canonicalization
PYTHONPATH=src python3 -m semantic_normalizer normalize \
  --text "O operador deve começar o servidor APP-01." \
  --lang pt --pretty

# English pivot projection for a retrieval experiment
PYTHONPATH=src python3 -m semantic_normalizer normalize \
  --text "O operador deve começar o servidor APP-01." \
  --lang pt --target-lang en --pretty

# Unit tests
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Install as an editable CLI:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
semantic-normalizer normalize --text "Begin the service." --lang en --pretty
```

## Example projection

Input:

```text
O operador deve começar o servidor APP-01.
```

Important output fields:

```json
{
  "original_text": "O operador deve começar o servidor APP-01.",
  "canonical_text": "O operador deve iniciar o servidor APP-01.",
  "concept_ids": [
    "role.operator",
    "action.start",
    "system.server"
  ],
  "concept_tokens": [
    "c__role__operator",
    "c__action__start",
    "c__system__server"
  ],
  "operator_tokens": [
    "modality__obligation"
  ],
  "status": "accepted"
}
```

`APP-01` remains unchanged and is stored as a protected value. The source text also remains unchanged.

## BM25 smoke test

The repository includes nine small bilingual documents and nine cross-language queries. The dependency-free BM25 harness produced this result with the sample registry:

| Projection | MRR | HitRate@1 | HitRate@3 |
|---|---:|---:|---:|
| Raw | 0.000 | 0.000 | 0.000 |
| Canonical English pivot | 0.778 | 0.556 | 1.000 |
| Expanded concept projection | 0.944 | 0.889 | 1.000 |

Run it with:

```bash
PYTHONPATH=src python3 -m semantic_normalizer evaluate \
  --documents examples/documents.jsonl \
  --queries examples/queries.jsonl \
  --k 1 3 --summary-only
```

This is a deliberately favorable synthetic test. It proves that the pipeline and metrics work. It does not prove production retrieval improvement or hallucination reduction.

## Retrieval fields

Use separate fields rather than replacing the source:

```json
{
  "text_raw": "O operador deve começar o servidor APP-01.",
  "text_canonical": "O operador deve iniciar o servidor APP-01.",
  "concept_tokens": "c__role__operator c__action__start c__system__server",
  "operator_tokens": "modality__obligation",
  "text_expanded": "...",
  "registry_version": "0.1.0"
}
```

Apply the same normalizer to queries. Tune field boosts against a held-out query set.

## Ambiguity behavior

The registry intentionally maps `remove/remover` to two concepts:

- `action.remove_physical`
- `action.delete_data`

Context separates these cases:

```text
remove the panel          -> action.remove_physical
remove the database row   -> action.delete_data
remove it                 -> review
```

An unresolved span is not silently forced into either concept.

## Optional agent resolver

Install the optional adapter:

```bash
pip install -e '.[agent]'
```

Then use an Instructor provider/model identifier:

```bash
semantic-normalizer normalize \
  --text "Remove it." \
  --lang en \
  --agent-model "ollama/qwen3:8b" \
  --max-attempts 2 \
  --pretty
```

The model receives only the unresolved span, source context, and an allow-list of candidate concepts. A low-confidence decision is rejected.

A human-approved JSON decision file can replace the model:

```json
{
  "remove": "action.remove_physical"
}
```

```bash
semantic-normalizer normalize \
  --text "Remove it." \
  --lang en \
  --decision-file decisions.json \
  --pretty
```

## Registry exports

Export a SKOS projection:

```bash
semantic-normalizer export-skos --output exports/concepts.ttl
```

Export explicit Elasticsearch `synonym_graph` rules that map variants to concept tokens:

```bash
semantic-normalizer export-synonyms --output exports/synonyms.txt
```

The export omits aliases that map to more than one concept. A flat synonym analyzer has no sentence context, so ambiguous forms must pass through the normalizer.

## Repository structure

```text
SKILL.md                         Agent operating contract
config/concepts.json             Editable bilingual concept registry
schemas/                         JSON Schema contracts
src/semantic_normalizer/         Runtime, loop, validators, BM25 harness
prompts/                         Constrained resolver and curation prompts
examples/                        Synthetic retrieval benchmark
reports/                         Generated validation and retrieval outputs
exports/                         Generated SKOS and safe synonym projections
scripts/run_smoke_test.sh        Complete local validation run
tests/                           Dependency-free regression tests
docs/architecture.md             System design and invariants
docs/evaluation-plan.md          Production measurement protocol
docs/governance.md               Terminology approval and versioning
docs/integration.md              Indexing and grep patterns
docs/references.md               Curated standards, libraries, and discussions
docs/rollout-plan.md             Delivery phases and acceptance gates
```

## Current limits

- The included registry has only 21 demonstration concepts.
- Morphology is represented by explicit surface forms, not a full English or Portuguese morphological analyzer.
- Same-language `canonical_text` preserves an inflected surface form when no verified canonical inflection is available; concept tokens still normalize it.
- The deterministic layer does not perform full syntactic or semantic-role parsing.
- The English pivot can create mixed-language syntax because it replaces terms, not complete grammar.
- Operator detection is conservative and rule-based.
- Exact semantic equivalence requires a domain gold set and, for difficult propositions, structured human review.
- Better retrieval is a prerequisite for grounded answers, not a sufficient condition for faithful generation.

## Recommended next implementation step

Build a real corpus benchmark before expanding the vocabulary broadly. Extract candidate variants from your documentation, approve concepts in batches, and measure mapping precision plus Recall@k after each registry release.
