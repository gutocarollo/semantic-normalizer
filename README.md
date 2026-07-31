# Semantic Normalizer Skill

A runnable prototype for reversible English-Portuguese concept canonicalization. It is designed for technical documentation, comments, knowledge bases, grep, BM25, and hybrid retrieval.

## Design decision

The project does **not** try to reduce English and Portuguese to one universal word list. It separates:

1. **Concept identity** — stable, language-independent IDs such as `action.start`.
2. **Preferred labels** — one canonical label per language, such as `start` and `iniciar`.
3. **Observed variants** — synonyms, inflections, abbreviations, and hidden labels.
4. **Source assertions** — negation, modality, conditions, numbers, identifiers, and original spans.

This prevents a common failure of synonym replacement: two words can be close in one context and materially different in another. Version 0.3.0 makes that split executable: concepts are stable identities, lexical forms carry an explicit `auto`/`review` policy, and every import is recorded in an append-only provenance ledger bound to a release record.

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

# Canonicalization: the language is detected from the text
PYTHONPATH=src python3 -m semantic_normalizer normalize \
  --text "O operador deve começar o servidor APP-01."

# Walk a corpus and emit sidecars carrying path, line and byte offsets
PYTHONPATH=src python3 -m semantic_normalizer index docs --output-dir sidecars/

# Tests
PYTHONPATH=src python3 -m pytest tests -q
```

Install as an editable CLI:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
semantic-normalizer normalize --text "Begin the service."
```

Subcommands: `validate-registry, normalize, query, index, evaluate,
reconcile-request, reconcile-apply, export, init-workspace`. Run `--help` on any
of them for its flags.

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
  "concept_ids": ["actor.operator", "action.start", "system.server"],
  "concept_tokens": ["c__actor__operator", "c__action__start", "c__system__server"],
  "canonical_status": "review",
  "needs_review": true,
  "protected_values": [{"value": "APP-01", "start": 35, "end": 41}],
  "unresolved": [
    {"original": "O ", "start": 0, "end": 2},
    {"original": "deve", "start": 11, "end": 15},
    {"original": " o ", "start": 23, "end": 26},
    {"original": ".", "start": 41, "end": 42}
  ]
}
```

`APP-01` remains unchanged and is stored as a protected value at its exact offsets. The source text also remains unchanged.

Note the status: three concepts resolved and the sentence is still `review`, because `deve` is ambiguous between `modality.obligation` and `modality.logical_necessity` and the remaining spans are unresolved. Resolving part of a sentence never certifies the whole of it — a projection is `accepted` only when nothing is left in `unresolved`.

## Retrieval evaluation

The retrieval ablations run against a development dataset and report per-condition metrics
with bootstrap confidence intervals:

```bash
PYTHONPATH=src python3 -m semantic_normalizer evaluate tests/fixtures/dev_retrieval.json
```

The 0.2.0 release shipped a 9x9 synthetic benchmark whose queries shared no content token
with their target documents, so its raw-BM25 baseline of MRR 0.000 was a property of the
fixtures, not a measurement. It was removed rather than reported. No production retrieval
claim is made until a real corpus with held-out relevance judgements exists — see
`docs/evaluation-plan.md`.

## Retrieval fields

Use separate fields rather than replacing the source:

```json
{
  "text_raw": "O operador deve começar o servidor APP-01.",
  "text_canonical": "O operador deve iniciar o servidor APP-01.",
  "concept_tokens": "c__actor__operator c__action__start c__system__server",
  "text_expanded": "...",
  "registry_version": "2.1.0"
}
```

Apply the same normalizer to queries. Tune field boosts against a held-out query set.

## Ambiguity behavior

Two different situations produce a `review`, and conflating them hides a real distinction.

**Cross-concept ambiguity** — one surface, several concepts. Verified in the registry:

```text
check the base   -> review: entity.facility_base | technical.numeral_base
```

No automatic rule chooses between a facility base and a numeral base, so the span goes to
reconciliation with both candidates.

**Unverified inflection** — one concept, but the inflected form is not yet approved for
automatic expansion:

```text
remova o painel  -> review: action.remove (single candidate, policy: review)
```

`remove`/`remover` themselves are *not* cross-concept ambiguous: they are the preferred
labels of `action.remove`, and the importer refused to attach them to `action.delete` as
alternatives precisely to keep that boundary. What carries `policy: review` is the imported
inflection whose paradigm was never verified.

**Grammatical abstention** — nothing lexical is uncertain; the *referent* is:

```text
Remove it.       -> review: ambiguous_candidates [], warnings
                    ["finite_grammar_abstained_coordination_or_anaphora"]
```

The finite grammar refuses to bind a span across anaphora, coordination, or an ambiguous
temporal structure. Read `warnings` before treating a `review` as a terminology gap: this
third case is not fixed by adding a concept.

An unresolved span is never silently forced into a concept — in any of the three cases.

## Terminology reconciliation

An unresolved span becomes a reconciliation request, not a guess. The workspace is a
directory outside the package, so a decision is always an explicit, auditable act:

```bash
semantic-normalizer init-workspace reconciliation/
semantic-normalizer reconcile-request --workspace reconciliation/ \
  --context "Check the base." --start 10 --end 14 --language en
# review the request, write the response, then apply it with an accountable reviewer:
semantic-normalizer reconcile-apply --workspace reconciliation/ \
  --request <req.json> --response <resp.json> --reviewer <name> \
  --rationale "context names the numeral base" --protected-slot-comparison preserved
```

A request carries only the unresolved span, its context, and an allow-list of candidate
concept IDs. A response that names a concept outside the allow-list is rejected.

## Registry exports

Export a SKOS projection:

```bash
semantic-normalizer export skos --output exports/concepts.ttl
```

Export explicit Elasticsearch `synonym_graph` rules that map variants to concept tokens:

```bash
semantic-normalizer export synonym-graph --output exports/synonyms.txt
```

Only aliases with `policy: auto` reach the flat synonym rules. An ambiguous alias such as
`remove`/`remover` carries `policy: review` and is deliberately excluded, because a flat
analyzer rule cannot disambiguate it.

The export omits aliases that map to more than one concept. A flat synonym analyzer has no sentence context, so ambiguous forms must pass through the normalizer.

## Repository structure

```text
SKILL.md                         Agent operating contract
src/semantic_normalizer/data/    Registry, its schema, release record and provenance ledger
schemas/                         JSON Schema contracts
src/semantic_normalizer/         Runtime, loop, validators, BM25 harness
prompts/                         Constrained resolver and curation prompts
examples/                        Synthetic retrieval benchmark
reports/                         Generated validation and retrieval outputs
exports/                         Generated SKOS and safe synonym projections
scripts/build_release.py         Reproducible offline wheel and skill ZIP
scripts/migrate_v02_concepts.py  Governed import of a legacy registry
tests/                           Dependency-free regression tests
docs/architecture.md             System design and invariants
docs/evaluation-plan.md          Production measurement protocol
docs/governance.md               Terminology approval and versioning
docs/integration.md              Indexing and grep patterns
docs/references.md               Curated standards, libraries, and discussions
docs/data-governance.md          Provenance and approval rules for lexical data
docs/migration-crosswalk-0.3.0.md  Measured 0.2.0 -> 0.3.0 migration record
docs/rollout-plan.md             Delivery phases and acceptance gates
```

## Current limits

- The registry has 86 concepts. It carries no vocabulary for any specific business domain yet.
- Morphology is represented by explicit surface forms, not a full English or Portuguese morphological analyzer.
- Same-language `canonical_text` preserves an inflected surface form when no verified canonical inflection is available; concept tokens still normalize it.
- The deterministic layer does not perform full syntactic or semantic-role parsing.
- Operator detection is conservative and rule-based.
- Exact semantic equivalence requires a domain gold set and, for difficult propositions, structured human review.
- Better retrieval is a prerequisite for grounded answers, not a sufficient condition for faithful generation.

## Recommended next implementation step

Build a real corpus benchmark before expanding the vocabulary broadly. Extract candidate variants from your documentation, approve concepts in batches, and measure mapping precision plus Recall@k after each registry release.
