---
name: semantic-normalizer
version: 0.1.0
description: "Normalize English and Portuguese technical text into a reversible concept-based representation for BM25, grep, filters, and hybrid retrieval. Use for ingestion, query normalization, terminology governance, ambiguity review, and retrieval evaluation."
---

<!-- argument-hint: [normalize, audit, propose-term, validate-registry, export-skos, export-synonyms, evaluate] [text-or-path] -->

# English-Portuguese Semantic Normalizer

## Mission

Create a parallel canonical projection of English or Portuguese source text. Reduce lexical variation without deleting technical meaning or overwriting the source.

Use language-independent concept IDs as semantic identity. Use one preferred label per language for each concept. Keep synonyms, inflections, abbreviations, and common misspellings as separate lexical evidence.

## Scope

Use this skill for:

- technical documentation;
- source-code comments and docstrings;
- operating procedures;
- internal policies and knowledge bases;
- ingestion and query normalization for lexical search;
- terminology curation and concept reconciliation;
- A/B evaluation of raw, canonical, and expanded retrieval fields.

Do not use this skill as:

- a replacement for the source text;
- a machine-translation system;
- a proof that two sentences are logically equivalent;
- a guarantee that an LLM will not hallucinate;
- an automatic authority for approving domain terminology;
- a claim of ASD-STE100 compliance.

## Non-negotiable invariants

1. Preserve `original_text` exactly.
2. Store every accepted mapping with source offsets and a concept ID.
3. Preserve negation, modality, conditions, exceptions, numbers, units, identifiers, code, paths, URLs, and quoted text.
4. Select only concepts that exist in the versioned registry.
5. Abstain when the context does not separate competing concepts.
6. Do not replace a specific term with a broader concept merely to increase coverage.
7. Do not merge near-synonyms when their scopes, consequences, temporal meanings, or object types differ.
8. Apply the same registry and normalization logic to documents and queries.
9. Keep raw and canonical retrieval fields separate.
10. Record registry and source hashes for reproducibility.

## Concept model

Treat the concept as the source of semantic identity:

```text
concept_id: action.start
preferred label EN: start
preferred label PT: iniciar
alternative EN: begin, commence
alternative PT: começar, dar início
surface forms: starts, started, inicie, iniciou
```

Follow these controls:

- one concept can have one preferred label per language;
- one concept can have many alternative or hidden labels;
- one surface form can point to several candidate concepts;
- context and domain evidence must resolve an ambiguous surface form;
- a concept definition and source authority are mandatory;
- part of speech is part of the mapping decision;
- deprecated terms remain searchable but are not emitted as preferred labels.

## Operating workflow

### 1. Protect source-sensitive spans

Detect and exclude code blocks, inline code, quoted strings, URLs, e-mail addresses, paths, UUIDs, and identifiers from replacement.

### 2. Detect or receive the source language

Use explicit metadata when available. Return `und` when automatic language evidence is insufficient.

### 3. Generate lexical candidates

Use the registry's preferred labels, alternative labels, hidden labels, and approved surface forms. Prefer longest phrase matches.

### 4. Resolve deterministic cases

Accept a unique candidate. For competing candidates, use part of speech, domain, nearby object terms, and negative context constraints.

Example:

```text
remove the panel -> action.remove_physical
remove the database record -> action.delete_data
```

### 5. Run the bounded ambiguity loop

Send only unresolved spans to the resolver. Give the resolver an allow-list of candidate concept IDs and their definitions.

```text
deterministic mapping
    -> unresolved spans?
        -> no: validate
        -> yes: constrained resolver
            -> select supplied concept or abstain
            -> validate
            -> one optional repair attempt
```

Use at most two total attempts by default. Never let the resolver invent a concept or rewrite the entire source.

### 6. Build parallel projections

Treat concept IDs and concept tokens as the primary canonical representation. Treat rewritten text as a secondary convenience field.

When a registered surface form is inflected and no verified equivalent inflection exists, preserve the source surface in same-language `canonical_text`. Keep the preferred label in the mapping and normalize retrieval through the concept token.

Emit these fields:

- `original_text`: immutable source;
- `canonical_text`: safe source-syntax projection with preferred labels where grammatical form is preserved;
- `concept_ids`: stable semantic identities;
- `concept_tokens`: BM25-safe tokens derived from concept IDs;
- `operator_tokens`: negation, modality, condition, and exception signals;
- `canonical_search_text`: raw text plus canonical labels, concept tokens, bilingual preferred labels, and operator tokens;
- `mappings`: source offsets, emitted label, preferred label, confidence, method, rationale, and candidates;
- `unresolved_terms`: spans that require review;
- `segments`: sentence-level projections;
- `provenance`: source, registry, and normalizer versions and hashes.

### 7. Validate the projection

Reject a projection when it changes:

- a protected value;
- a number or unit;
- negation;
- obligation, recommendation, permission, or capability;
- a condition or exception;
- source-span integrity;
- concept-registry integrity.

Return `review` for unresolved ambiguity. Return `rejected` for a failed invariant.

## Retrieval integration

Index at least these independent fields:

```text
text_raw
text_canonical
concept_tokens
entity_or_defined_terms
source_metadata
```

Do not replace `text_raw` with the canonical form. Fielded BM25 can boost exact raw matches while concept tokens recover synonym and cross-language matches.

Export only aliases that resolve to one concept into flat search-analyzer synonym rules. Route ambiguous aliases through the contextual normalizer.

Normalize the query through the same pipeline. Keep query expansion configurable so it can change without rebuilding the source corpus.

Benchmark these retrieval modes separately:

1. raw BM25;
2. canonical BM25;
3. expanded BM25;
4. dense retrieval;
5. sparse learned retrieval;
6. hybrid fusion;
7. optional reranking.

Do not infer success from cleaner text. Measure `Recall@k`, `HitRate@k`, MRR, nDCG, mapping precision, abstention quality, and answer faithfulness.

## Terminology proposal workflow

When the registry has no adequate concept:

1. Preserve the source span as unresolved.
2. Extract example contexts.
3. Propose a new concept ID, definitions, preferred labels, alternatives, part of speech, domain, and authority.
4. Search for collisions and broader existing concepts.
5. Require human or domain-authority approval.
6. Add gold positive and negative examples.
7. Increment the registry version.
8. Re-run retrieval regression tests.

Never add a synonym only because an embedding score is high.

## ASD-STE100 relationship

ASD-STE100 supplies useful design principles: controlled vocabulary, stable terminology, restricted meanings, explicit constructions, and context-preserving reconstruction. It does not define this bilingual concept scheme, concept-token indexing, BM25 expansion, or a hallucination-reduction guarantee.

Use the separate ASD-STE100 skill when the task requires an actual STE audit, exact dictionary evidence, procedural sentence limits, or certified technical-writing review.

## Commands

```bash
# Normalize in the source language.
PYTHONPATH=src python -m semantic_normalizer normalize \
  --text "O operador deve começar o servidor APP-01." \
  --lang pt --pretty

# Create an English pivot projection for retrieval experiments.
PYTHONPATH=src python -m semantic_normalizer normalize \
  --input examples/sample-input.txt \
  --lang pt --target-lang en --pretty

# Run the included raw/canonical/expanded BM25 comparison.
PYTHONPATH=src python -m semantic_normalizer evaluate \
  --documents examples/documents.jsonl \
  --queries examples/queries.jsonl \
  --k 1 3 5

# Validate and export the terminology registry.
PYTHONPATH=src python -m semantic_normalizer validate-registry
PYTHONPATH=src python -m semantic_normalizer export-skos --output exports/concepts.ttl
PYTHONPATH=src python -m semantic_normalizer export-synonyms --output exports/synonyms.txt
```

## Acceptance gate

A production release requires all conditions:

- no source mutation;
- no validation errors;
- approved registry version;
- mapping precision above the domain threshold;
- explicit unresolved-rate threshold;
- retrieval improvement on held-out real queries;
- no material regression in exact identifiers, quotations, numbers, or legal/safety language;
- review logs for every agent-resolved mapping;
- rollback path to the previous registry and normalizer version.
